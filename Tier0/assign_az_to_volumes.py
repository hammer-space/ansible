#!/usr/bin/env python3
"""
Assign AZ prefix to Hammerspace volumes based on OCI GPU memory fabric.

This script uses GPU memory fabric (customergpumemoryfabric) to determine AZ
for instances and updates volume names in Hammerspace to include the AZ prefix.
Instances sharing the same GPU fabric OCID are assigned to the same AZ.

Prerequisites:
    pip3 install oci requests

Step 1: Collect GPU fabric info from instances (run from Ansible control node):
    ansible -i inventory.oci.yml all -m shell -a \\
        "curl -sH 'Authorization: Bearer Oracle' -L http://169.254.169.254/opc/v2/host/rdmaTopologyData/customerGpuMemoryFabric" \\
        | grep -E "ocid1|SUCCESS" > gpu_fabric_data.txt

    Or create a file manually with format (space or tab separated):
        <gpu_fabric_ocid> <instance_name> <private_ip>

Step 2: Run this script with the mapping file:
    python3 assign_az_to_volumes.py --host <anvil_ip> --user admin --password 'xxx' \\
        --gpu-fabric-file gpu_fabric_data.txt --dry-run

AZ Mapping:
    GPU fabric OCIDs are automatically mapped to AZ numbers:
    - First unique GPU fabric  -> AZ1
    - Second unique GPU fabric -> AZ2
    - etc.

Examples:
    # Using GPU fabric mapping file (recommended for GPU instances)
    python3 assign_az_to_volumes.py --host 10.1.2.3 --user admin --password 'Hammer.123!!' \\
        --gpu-fabric-file gpu_fabric_data.txt --dry-run

    # Using OCI API with fault domain (for non-GPU instances)
    python3 assign_az_to_volumes.py --host 10.1.2.3 --user admin --password 'Hammer.123!!' \\
        --compartment-id ocid1.compartment.oc1..xxx --az-source fault_domain --dry-run

    # Report only - generate CSV without modifying volumes
    python3 assign_az_to_volumes.py --host 10.1.2.3 --user admin --password 'Hammer.123!!' \\
        --gpu-fabric-file gpu_fabric_data.txt --report-only
"""

import argparse
import requests
import urllib.parse
import sys
import re
from typing import Dict, List, Any, Optional

# Disable SSL warnings for self-signed certificates
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

try:
    import oci
    OCI_AVAILABLE = True
except ImportError:
    OCI_AVAILABLE = False


def fault_domain_to_az(fault_domain: str) -> str:
    """Convert OCI fault domain to AZ prefix.

    FAULT-DOMAIN-1 -> AZ1
    FAULT-DOMAIN-2 -> AZ2
    FAULT-DOMAIN-3 -> AZ3
    """
    if not fault_domain:
        return ""
    match = re.search(r'FAULT-DOMAIN-(\d+)', fault_domain)
    if match:
        return f"AZ{match.group(1)}"
    return ""


def parse_gpu_fabric_file(filepath: str) -> Dict[str, Dict[str, str]]:
    """Parse GPU fabric data file.

    Expected format (space/tab separated):
        <gpu_fabric_ocid> <instance_name> <private_ip>

    Example:
        ocid1.computegpumemoryfabric.oc1.us-dallas-1.anqwyl...slutj7sca instance20260116093135 10.241.36.58

    Returns dict: {instance_name: {gpu_fabric, private_ip}}
    """
    instances = {}

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Skip ansible output lines
            if 'SUCCESS' in line or 'CHANGED' in line or 'FAILED' in line:
                continue
            if line.startswith('[') or '=>' in line or line.startswith('{'):
                continue

            # Parse space/tab separated values
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith('ocid1.computegpumemoryfabric'):
                gpu_fabric = parts[0]
                instance_name = parts[1]
                private_ip = parts[2]

                instances[instance_name] = {
                    'gpu_fabric': gpu_fabric,
                    'private_ip': private_ip
                }
            elif len(parts) >= 2 and parts[0].startswith('ocid1.computegpumemoryfabric'):
                # Format: gpu_fabric instance_name (no IP)
                gpu_fabric = parts[0]
                instance_name = parts[1]
                instances[instance_name] = {
                    'gpu_fabric': gpu_fabric,
                    'private_ip': ''
                }

    return instances


class GpuFabricMapper:
    """Maps GPU fabric OCIDs to AZ numbers."""

    def __init__(self):
        self.fabric_to_az = {}
        self.next_az = 1

    def learn_from_existing(self, existing_mappings: Dict[str, str]):
        """Learn GPU fabric to AZ mappings from existing volume names.

        Args:
            existing_mappings: Dict of {gpu_fabric_ocid: az_string} (e.g., {"ocid1...": "AZ1"})
        """
        for gpu_fabric_ocid, az in existing_mappings.items():
            if gpu_fabric_ocid and az:
                self.fabric_to_az[gpu_fabric_ocid] = az
                # Extract the AZ number and update next_az if needed
                match = re.match(r'AZ(\d+)', az)
                if match:
                    az_num = int(match.group(1))
                    if az_num >= self.next_az:
                        self.next_az = az_num + 1

    def get_az(self, gpu_fabric_ocid: str) -> str:
        """Get AZ for a GPU fabric OCID. Assigns new AZ number if first seen."""
        if not gpu_fabric_ocid:
            return ""

        if gpu_fabric_ocid not in self.fabric_to_az:
            self.fabric_to_az[gpu_fabric_ocid] = f"AZ{self.next_az}"
            self.next_az += 1

        return self.fabric_to_az[gpu_fabric_ocid]

    def get_short_id(self, gpu_fabric_ocid: str) -> str:
        """Get a short identifier for the GPU fabric (last 8 chars)."""
        if not gpu_fabric_ocid:
            return ""
        return gpu_fabric_ocid[-8:]

    def get_mapping(self) -> Dict[str, str]:
        """Return the full fabric -> AZ mapping."""
        return self.fabric_to_az.copy()


def get_oci_instances(
    compartment_id: str,
    config_profile: str = "DEFAULT",
    shape_filter: str = None,
    lifecycle_state: str = "RUNNING",
    az_source: str = "gpu_fabric"
) -> tuple[Dict[str, Dict[str, str]], GpuFabricMapper]:
    """Query OCI to get all instances with their GPU fabric info.

    Args:
        compartment_id: OCI compartment OCID
        config_profile: OCI config profile name
        shape_filter: Filter by shape (e.g., "BM.GPU.GB200-v3.4")
        lifecycle_state: Filter by lifecycle state (default: RUNNING)
        az_source: Source for AZ - "gpu_fabric" (default) or "fault_domain"

    Returns:
        tuple: (instances dict, GpuFabricMapper)
    """
    if not OCI_AVAILABLE:
        print("ERROR: OCI SDK not installed. Run: pip3 install oci")
        sys.exit(1)

    # Load OCI config
    config = oci.config.from_file(profile_name=config_profile)

    # Initialize clients
    compute_client = oci.core.ComputeClient(config)
    vnic_client = oci.core.VirtualNetworkClient(config)

    instances = {}
    gpu_fabric_mapper = GpuFabricMapper()

    # List all instances in compartment
    list_instances_response = compute_client.list_instances(
        compartment_id=compartment_id,
        lifecycle_state=lifecycle_state
    )

    for instance in list_instances_response.data:
        # Filter by shape if specified
        if shape_filter and instance.shape != shape_filter:
            continue

        display_name = instance.display_name
        fault_domain = instance.fault_domain  # e.g., "FAULT-DOMAIN-1"
        instance_id = instance.id

        # Get GPU memory fabric attachment
        gpu_fabric_ocid = ""
        try:
            # List compute GPU memory fabric attachments for this instance
            attachments = compute_client.list_compute_gpu_memory_fabrics(
                compartment_id=compartment_id
            ).data

            for attachment in attachments:
                if hasattr(attachment, 'instance_id') and attachment.instance_id == instance_id:
                    gpu_fabric_ocid = attachment.id
                    break
        except Exception:
            pass

        # Alternative: Try to get from instance metadata or tags
        if not gpu_fabric_ocid:
            try:
                if instance.metadata:
                    gpu_fabric_ocid = instance.metadata.get('customergpumemoryfabric', '')
                if not gpu_fabric_ocid and instance.freeform_tags:
                    gpu_fabric_ocid = instance.freeform_tags.get('gpu_fabric', '')
            except Exception:
                pass

        # Get VNIC attachments to find private IP
        private_ip = ""
        try:
            vnic_attachments = compute_client.list_vnic_attachments(
                compartment_id=compartment_id,
                instance_id=instance_id
            ).data

            if vnic_attachments:
                vnic = vnic_client.get_vnic(vnic_attachments[0].vnic_id).data
                private_ip = vnic.private_ip
        except Exception:
            pass

        # Determine AZ based on source
        if az_source == "gpu_fabric" and gpu_fabric_ocid:
            az = gpu_fabric_mapper.get_az(gpu_fabric_ocid)
        else:
            az = fault_domain_to_az(fault_domain)

        instances[display_name] = {
            'fault_domain': fault_domain,
            'gpu_fabric': gpu_fabric_ocid,
            'gpu_fabric_short': gpu_fabric_mapper.get_short_id(gpu_fabric_ocid),
            'private_ip': private_ip,
            'az': az,
            'lifecycle_state': instance.lifecycle_state,
            'shape': instance.shape
        }

    return instances, gpu_fabric_mapper


class HammerspaceClient:
    def __init__(self, host: str, user: str, password: str, verify_ssl: bool = False):
        self.base_url = f"https://{host}:8443/mgmt/v1.2/rest"
        self.auth = (user, password)
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = self.verify_ssl

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an API request."""
        url = f"{self.base_url}/{endpoint}"
        response = self.session.request(method, url, **kwargs)
        return response

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Get all nodes from Hammerspace."""
        response = self._request("GET", "nodes")
        response.raise_for_status()
        return response.json()

    def get_all_storage_volumes(self) -> List[Dict[str, Any]]:
        """Get all storage volumes from Hammerspace."""
        response = self._request("GET", "storage-volumes")
        response.raise_for_status()
        return response.json()

    def delete_volume(self, volume_name: str) -> bool:
        """Delete a volume by name."""
        encoded_name = urllib.parse.quote(volume_name, safe='')
        response = self._request("DELETE", f"storage-volumes/{encoded_name}")
        return response.status_code in [200, 202, 204]

    def add_volume(self, volume_payload: Dict) -> bool:
        """Add a new volume."""
        response = self._request(
            "POST",
            "storage-volumes?force=true",
            json=volume_payload
        )
        return response.status_code in [200, 202]

    def rename_volume(self, old_name: str, new_name: str, volume_uuid: str = None) -> tuple[bool, str]:
        """Rename a volume by updating its name property.

        Args:
            old_name: Current volume name
            new_name: New volume name
            volume_uuid: Optional UUID for more reliable addressing

        Returns:
            tuple: (success: bool, error_message: str)
        """
        # Prefer UUID if available, otherwise use URL-encoded name
        if volume_uuid:
            volume_id = volume_uuid
        else:
            volume_id = urllib.parse.quote(old_name, safe='')

        # GET the full volume data first
        get_response = self._request("GET", f"storage-volumes/{volume_id}")
        if get_response.status_code != 200:
            return False, f"Failed to get volume: {get_response.status_code}"

        volume_data = get_response.json()

        # Update the name field
        volume_data['name'] = new_name

        # PUT the updated volume data back
        put_response = self._request(
            "PUT",
            f"storage-volumes/{volume_id}",
            json=volume_data
        )

        if put_response.status_code in [200, 202, 204]:
            return True, ""

        return False, f"PUT failed: {put_response.status_code} - {put_response.text[:500]}"


def parse_volume_name(volume_name: str) -> Dict[str, str]:
    """Parse volume name into components.

    Volume name formats:
    - nodename::path
    - AZ1:nodename::path

    Returns dict with: az_prefix, node_name, path
    """
    result = {"az_prefix": "", "node_name": "", "path": ""}

    # Check for AZ prefix (AZ1:, AZ2:, etc.)
    az_match = re.match(r'^(AZ\d+):(.+)$', volume_name)
    if az_match:
        result["az_prefix"] = az_match.group(1)
        volume_name = az_match.group(2)

    # Parse nodename::path
    if "::" in volume_name:
        parts = volume_name.split("::", 1)
        result["node_name"] = parts[0]
        result["path"] = parts[1] if len(parts) > 1 else ""
    else:
        result["node_name"] = volume_name

    return result


def generate_instance_report(instances: Dict[str, Dict], output_file: str = "instance_report.csv"):
    """Generate CSV report of instances with GPU fabric and AZ."""
    with open(output_file, 'w') as f:
        f.write("display_name,gpu_fabric,fault_domain,az,private_ip,shape\n")
        for name, info in sorted(instances.items()):
            gpu_fabric = info.get('gpu_fabric', '') or ''
            f.write(f"{name},{gpu_fabric},{info['fault_domain']},{info['az']},{info['private_ip']},{info.get('shape', '')}\n")
    print(f"Instance report saved to: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Assign AZ prefix to Hammerspace volumes based on OCI fault domains"
    )
    parser.add_argument('--host', required=True, help='Hammerspace Anvil IP or hostname')
    parser.add_argument('--user', required=True, help='API username')
    parser.add_argument('--password', required=True, help='API password')

    # GPU fabric file OR OCI API options
    parser.add_argument('--gpu-fabric-file', help='File with GPU fabric data (gpu_fabric instance_name ip)')
    parser.add_argument('--compartment-id', help='OCI compartment OCID (if not using --gpu-fabric-file)')
    parser.add_argument('--oci-profile', default='DEFAULT', help='OCI config profile name (default: DEFAULT)')
    parser.add_argument('--shape', default='BM.GPU.GB200-v3.4', help='Filter by OCI shape (default: BM.GPU.GB200-v3.4)')
    parser.add_argument('--lifecycle-state', default='RUNNING', help='Filter by lifecycle state (default: RUNNING)')
    parser.add_argument('--az-source', default='gpu_fabric', choices=['gpu_fabric', 'fault_domain'],
                        help='Source for AZ mapping: gpu_fabric (default) or fault_domain')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be changed without making changes')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')
    parser.add_argument('--report-only', action='store_true', help='Only generate instance report, do not modify volumes')
    parser.add_argument('--output', default='instance_report.csv', help='Output file for instance report')

    args = parser.parse_args()

    # Get instance data from GPU fabric file or OCI API
    gpu_fabric_mapper = GpuFabricMapper()

    if args.gpu_fabric_file:
        # Use GPU fabric mapping file
        print(f"Loading GPU fabric data from {args.gpu_fabric_file}...")
        try:
            raw_instances = parse_gpu_fabric_file(args.gpu_fabric_file)
            print(f"  Found {len(raw_instances)} instances in file\n")
        except Exception as e:
            print(f"Error loading GPU fabric file: {e}")
            sys.exit(1)

        # Connect to Hammerspace early to learn existing AZ mappings
        print(f"Connecting to Hammerspace at {args.host} to learn existing AZ mappings...")
        client = HammerspaceClient(args.host, args.user, args.password)

        try:
            existing_volumes = client.get_all_storage_volumes()
            print(f"  Found {len(existing_volumes)} existing volumes")

            # Learn AZ mappings from existing volumes
            existing_az_mappings = {}
            for volume in existing_volumes:
                vol_name = volume.get('name', '')
                parsed = parse_volume_name(vol_name)
                if parsed['az_prefix'] and parsed['node_name']:
                    # Find this node's GPU fabric from the input file
                    node_name = parsed['node_name']
                    if node_name in raw_instances:
                        gpu_fabric = raw_instances[node_name]['gpu_fabric']
                        az = parsed['az_prefix']
                        if gpu_fabric not in existing_az_mappings:
                            existing_az_mappings[gpu_fabric] = az
                            print(f"    Learned: {az} -> ...{gpu_fabric[-12:]}")

            if existing_az_mappings:
                gpu_fabric_mapper.learn_from_existing(existing_az_mappings)
                print(f"  Learned {len(existing_az_mappings)} existing AZ mappings")
                print(f"  Next new AZ will be: AZ{gpu_fabric_mapper.next_az}")
            else:
                print("  No existing AZ mappings found")
            print()

        except requests.exceptions.RequestException as e:
            print(f"  Warning: Could not fetch existing volumes: {e}")
            print("  Proceeding without learning existing mappings...\n")

        # Build instances dict with AZ mapping (now using learned mappings)
        oci_instances = {}
        for name, info in raw_instances.items():
            gpu_fabric = info['gpu_fabric']
            az = gpu_fabric_mapper.get_az(gpu_fabric)
            oci_instances[name] = {
                'gpu_fabric': gpu_fabric,
                'gpu_fabric_short': gpu_fabric_mapper.get_short_id(gpu_fabric),
                'private_ip': info['private_ip'],
                'az': az,
                'fault_domain': '',
                'lifecycle_state': 'RUNNING',
                'shape': args.shape
            }
    elif args.compartment_id:
        # Query OCI API
        print(f"Querying OCI for instances in compartment...")
        print(f"  Filters: shape={args.shape}, lifecycle_state={args.lifecycle_state}")
        print(f"  AZ source: {args.az_source}")

        # First, connect to Hammerspace to learn existing AZ mappings
        print(f"\nConnecting to Hammerspace at {args.host} to learn existing AZ mappings...")
        client = HammerspaceClient(args.host, args.user, args.password)

        try:
            oci_instances, gpu_fabric_mapper = get_oci_instances(
                compartment_id=args.compartment_id,
                config_profile=args.oci_profile,
                shape_filter=args.shape,
                lifecycle_state=args.lifecycle_state,
                az_source=args.az_source
            )
            print(f"  Found {len(oci_instances)} matching instances\n")

            # Learn from existing Hammerspace volumes
            try:
                existing_volumes = client.get_all_storage_volumes()
                print(f"  Found {len(existing_volumes)} existing volumes")

                existing_az_mappings = {}
                for volume in existing_volumes:
                    vol_name = volume.get('name', '')
                    parsed = parse_volume_name(vol_name)
                    if parsed['az_prefix'] and parsed['node_name']:
                        node_name = parsed['node_name']
                        if node_name in oci_instances:
                            gpu_fabric = oci_instances[node_name].get('gpu_fabric', '')
                            if gpu_fabric:
                                az = parsed['az_prefix']
                                if gpu_fabric not in existing_az_mappings:
                                    existing_az_mappings[gpu_fabric] = az
                                    print(f"    Learned: {az} -> ...{gpu_fabric[-12:]}")

                if existing_az_mappings:
                    gpu_fabric_mapper.learn_from_existing(existing_az_mappings)
                    print(f"  Learned {len(existing_az_mappings)} existing AZ mappings")

                    # Re-assign AZ to instances using learned mappings
                    for name, info in oci_instances.items():
                        gpu_fabric = info.get('gpu_fabric', '')
                        if gpu_fabric:
                            info['az'] = gpu_fabric_mapper.get_az(gpu_fabric)
                else:
                    print("  No existing AZ mappings found")

            except requests.exceptions.RequestException as e:
                print(f"  Warning: Could not fetch existing volumes: {e}")
                print("  Proceeding without learning existing mappings...")

        except Exception as e:
            print(f"Error querying OCI: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        print("ERROR: Must provide either --gpu-fabric-file or --compartment-id")
        sys.exit(1)

    # Show GPU fabric to AZ mapping
    if gpu_fabric_mapper.get_mapping():
        print("  GPU Fabric to AZ Mapping:")
        print("  " + "-"*80)
        for fabric_ocid, az in gpu_fabric_mapper.get_mapping().items():
            short_id = fabric_ocid[-12:] if len(fabric_ocid) > 12 else fabric_ocid
            print(f"    {az}: ...{short_id}")
        print()

    print(f"  {'Display Name':<35} {'GPU Fabric':<12} {'AZ':<5} {'Private IP':<15}")
    print(f"  {'-'*35} {'-'*12} {'-'*5} {'-'*15}")
    for name, info in sorted(oci_instances.items()):
        gpu_short = info.get('gpu_fabric_short', '')[:12] if info.get('gpu_fabric_short') else 'N/A'
        print(f"  {name:<35} {gpu_short:<12} {info['az']:<5} {info['private_ip']:<15}")

    # Generate instance report
    generate_instance_report(oci_instances, args.output)

    if args.report_only:
        print("\n[REPORT ONLY] Exiting without modifying volumes.")
        sys.exit(0)

    # Refresh volumes list (client was already created during AZ learning phase)
    try:
        print("\nRefreshing storage volumes list...")
        all_volumes = client.get_all_storage_volumes()
        print(f"  Found {len(all_volumes)} total volumes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching volumes: {e}")
        sys.exit(1)

    # Analyze volumes and determine changes needed
    changes = []
    already_has_az = []  # Volumes that already have an AZ prefix (skip)
    no_mapping = []

    for volume in all_volumes:
        vol_name = volume.get('name', '')
        vol_uuid = volume.get('uoid', {}).get('uuid', '')

        parsed = parse_volume_name(vol_name)
        node_name = parsed['node_name']
        current_az = parsed['az_prefix']
        path = parsed['path']

        # Skip volumes that already have an AZ prefix configured
        if current_az:
            already_has_az.append({
                'volume': vol_name,
                'az': current_az,
                'node': node_name
            })
            continue

        # Find fault domain for this node from OCI
        instance_info = oci_instances.get(node_name, {})
        expected_az = instance_info.get('az', '')

        if not expected_az:
            no_mapping.append({
                'volume': vol_name,
                'node': node_name,
                'reason': 'No matching OCI instance found'
            })
            continue

        # Need to add AZ prefix
        if path:
            new_name = f"{expected_az}:{node_name}::{path}"
        else:
            new_name = f"{expected_az}:{node_name}"

        changes.append({
            'volume': vol_name,
            'new_name': new_name,
            'uuid': vol_uuid,
            'node': node_name,
            'current_az': '(none)',
            'new_az': expected_az,
            'volume_data': volume
        })

    # Display summary
    print("\n" + "="*70)
    print("VOLUME ANALYSIS SUMMARY")
    print("="*70)

    if already_has_az:
        print(f"\n⏭ Volumes with existing AZ prefix (skipped): {len(already_has_az)}")
        for v in already_has_az[:5]:
            print(f"    {v['volume']} ({v['az']})")
        if len(already_has_az) > 5:
            print(f"    ... and {len(already_has_az) - 5} more")

    if no_mapping:
        print(f"\n⚠ Volumes with no OCI instance match: {len(no_mapping)}")
        for v in no_mapping[:5]:
            print(f"    {v['volume']} (node: {v['node']})")
        if len(no_mapping) > 5:
            print(f"    ... and {len(no_mapping) - 5} more")

    if changes:
        print(f"\n→ Volumes to be updated: {len(changes)}")
        print("-"*70)
        print(f"{'Current Name':<40} {'New Name':<40}")
        print("-"*70)
        for c in changes:
            print(f"{c['volume']:<40} {c['new_name']:<40}")
    else:
        print("\n✓ No changes needed - all volumes have correct AZ prefix")
        sys.exit(0)

    if args.dry_run:
        print(f"\n[DRY RUN] No changes made.")
        sys.exit(0)

    # Confirmation
    if not args.yes:
        print(f"\n{'='*70}")
        print("WARNING: This will rename the above volumes!")
        print(f"{'='*70}")
        confirm = input("\nType 'yes' to confirm: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)

    # Apply changes (rename volumes)
    print("\n" + "="*70)
    print("APPLYING CHANGES")
    print("="*70)

    success = 0
    failed = 0

    for change in changes:
        print(f"\nRenaming: {change['volume']} -> {change['new_name']}")

        ok, error = client.rename_volume(
            change['volume'],
            change['new_name'],
            volume_uuid=change.get('uuid')
        )
        if ok:
            print(f"  ✓ Success")
            success += 1
        else:
            print(f"  ✗ Failed to rename: {error}")
            failed += 1

    # Final summary
    print("\n" + "="*70)
    print("FINAL SUMMARY")
    print("="*70)
    print(f"Successfully updated: {success}")
    print(f"Failed: {failed}")
    print(f"Skipped (existing AZ): {len(already_has_az)}")
    print(f"No OCI match: {len(no_mapping)}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
