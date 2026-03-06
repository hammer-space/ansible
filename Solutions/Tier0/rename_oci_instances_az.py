#!/usr/bin/env python3
"""
Rename OCI instances by prepending their Hammerspace AZ prefix.

Reads AZ assignments from Hammerspace volume names (e.g., "AZ3:instance20260303192653::/path")
and renames OCI instances accordingly (e.g., "instance20260303192653" -> "AZ3_instance20260303192653").

Prerequisites:
    pip3 install oci requests

Usage:
    # Using instances file (default: tier0_instances_limit)
    python3 rename_oci_instances_az.py --host <anvil_ip> --user admin --password 'xxx' \
        --compartment-id <ocid> --instances-file tier0_instances_limit --dry-run

    # Using regex pattern to match OCI instance names directly
    python3 rename_oci_instances_az.py --host <anvil_ip> --user admin --password 'xxx' \
        --compartment-id <ocid> --name-pattern "instance2026.*" --dry-run

    # Skip instances that already have AZ prefix
    python3 rename_oci_instances_az.py --host <anvil_ip> --user admin --password 'xxx' \
        --compartment-id <ocid> --name-pattern "instance2026.*" --skip-existing

Examples:
    # Match all instances starting with "instance2026"
    python3 rename_oci_instances_az.py --host 10.0.10.15 --user admin --password 'Hammer.123!!' \
        --compartment-id ocid1.compartment.oc1..aaaaaa --name-pattern "^instance2026" --dry-run

    # Using instances file
    python3 rename_oci_instances_az.py --host 10.0.10.15 --user admin --password 'Hammer.123!!' \
        --compartment-id ocid1.compartment.oc1..aaaaaa --instances-file tier0_instances_limit --yes

    # Both: file + pattern (union of both)
    python3 rename_oci_instances_az.py --host 10.0.10.15 --user admin --password 'Hammer.123!!' \
        --compartment-id ocid1.compartment.oc1..aaaaaa --instances-file tier0_instances_limit \
        --name-pattern "^instance2026030319" --dry-run
"""

import argparse
import requests
import sys
import re
from typing import Dict, List, Any

# Disable SSL warnings for self-signed certificates
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

try:
    import oci
    OCI_AVAILABLE = True
except ImportError:
    OCI_AVAILABLE = False


class HammerspaceClient:
    def __init__(self, host: str, user: str, password: str, verify_ssl: bool = False):
        self.base_url = f"https://{host}:8443/mgmt/v1.2/rest"
        self.auth = (user, password)
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = self.verify_ssl

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/{endpoint}"
        return self.session.request(method, url, **kwargs)

    def get_all_storage_volumes(self) -> List[Dict[str, Any]]:
        response = self._request("GET", "storage-volumes")
        response.raise_for_status()
        return response.json()


def load_instances_file(filepath: str) -> List[str]:
    """Load instance names from file, one per line."""
    instances = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                instances.append(line)
    return instances


def get_az_from_volumes(volumes: List[Dict], instance_names: List[str]) -> Dict[str, str]:
    """Extract AZ prefix per instance from Hammerspace volume names.

    Volume name format: AZ1:instance_name::/path
    Returns: {instance_name: "AZ1", ...}
    """
    az_map = {}
    for volume in volumes:
        vol_name = volume.get('name', '')

        # Match AZ prefix: AZ<N>:<instance_name>::<path>
        match = re.match(r'^(AZ\d+):([^:]+)::', vol_name)
        if not match:
            continue

        az = match.group(1)
        node_name = match.group(2)

        if node_name in instance_names:
            if node_name in az_map and az_map[node_name] != az:
                print(f"  WARNING: {node_name} has conflicting AZ: {az_map[node_name]} vs {az}")
            az_map[node_name] = az

    return az_map


def find_oci_instances(compute_client, compartment_id: str,
                       instance_names: List[str] = None,
                       name_pattern: str = None) -> Dict[str, Dict]:
    """Find OCI instances matching the given names or regex pattern.

    Args:
        compute_client: OCI ComputeClient
        compartment_id: OCI compartment OCID
        instance_names: Explicit list of instance names to match
        name_pattern: Regex pattern to match display names (e.g., "instance2026.*")

    Returns: {display_name: {id, display_name, lifecycle_state}}
    """
    found = {}

    # Paginate through all instances
    response = oci.pagination.list_call_get_all_results(
        compute_client.list_instances,
        compartment_id=compartment_id,
        lifecycle_state="RUNNING"
    )

    name_set = set(instance_names) if instance_names else None
    compiled_pattern = re.compile(name_pattern) if name_pattern else None

    for instance in response.data:
        match = False
        if name_set and instance.display_name in name_set:
            match = True
        elif compiled_pattern and compiled_pattern.search(instance.display_name):
            match = True

        if match:
            found[instance.display_name] = {
                'id': instance.id,
                'display_name': instance.display_name,
                'lifecycle_state': instance.lifecycle_state
            }

    return found


def main():
    parser = argparse.ArgumentParser(
        description="Rename OCI instances with Hammerspace AZ prefix",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Naming convention:
  Before: instance20260303192653
  After:  AZ3_instance20260303192653

The AZ is determined from Hammerspace volume names (AZ<N>:instance_name::/path).
        """
    )

    # Hammerspace connection
    parser.add_argument('--host', required=True, help='Hammerspace Anvil IP or hostname')
    parser.add_argument('--user', required=True, help='Hammerspace API username')
    parser.add_argument('--password', required=True, help='Hammerspace API password')

    # OCI
    parser.add_argument('--compartment-id', required=True, help='OCI compartment OCID')
    parser.add_argument('--oci-profile', default='DEFAULT', help='OCI config profile (default: DEFAULT)')

    # Instance selection (file, pattern, or both)
    parser.add_argument('--instances-file',
                        help='File with instance names (default: tier0_instances_limit)')
    parser.add_argument('--name-pattern',
                        help='Regex pattern to match OCI instance display names '
                             '(e.g., "instance2026.*", "^instance2026030319")')

    # Options
    parser.add_argument('--separator', default='_',
                        help='Separator between AZ and instance name (default: _)')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip instances whose display name already starts with AZ')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be renamed')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')

    args = parser.parse_args()

    if not OCI_AVAILABLE:
        print("ERROR: OCI SDK not installed. Run: pip3 install oci")
        sys.exit(1)

    # Require at least one instance source
    if not args.instances_file and not args.name_pattern:
        # Default to file if neither specified
        args.instances_file = 'tier0_instances_limit'

    # Load instance names from file
    instance_names_from_file = []
    if args.instances_file:
        try:
            instance_names_from_file = load_instances_file(args.instances_file)
            print(f"Loaded {len(instance_names_from_file)} instance(s) from {args.instances_file}")
        except FileNotFoundError:
            if not args.name_pattern:
                print(f"ERROR: File not found: {args.instances_file}")
                sys.exit(1)
            else:
                print(f"  File not found: {args.instances_file} (using --name-pattern instead)")

    if args.name_pattern:
        print(f"Using OCI name pattern: {args.name_pattern}")

    # Connect to OCI
    print(f"\nConnecting to OCI (profile: {args.oci_profile})...")
    config = oci.config.from_file(profile_name=args.oci_profile)
    compute_client = oci.core.ComputeClient(config)

    # Find OCI instances (from file names, pattern, or both)
    print(f"Fetching instances in compartment...")
    oci_instances = find_oci_instances(
        compute_client, args.compartment_id,
        instance_names=instance_names_from_file if instance_names_from_file else None,
        name_pattern=args.name_pattern
    )
    print(f"  Found {len(oci_instances)} matching OCI instance(s)")

    if not oci_instances:
        print("\nNo matching OCI instances found.")
        sys.exit(0)

    # Use discovered instance names for Hammerspace lookup
    instance_names = list(oci_instances.keys())

    # Connect to Hammerspace and get AZ mapping
    print(f"\nConnecting to Hammerspace at {args.host}...")
    hs_client = HammerspaceClient(args.host, args.user, args.password)

    try:
        volumes = hs_client.get_all_storage_volumes()
        print(f"  Found {len(volumes)} storage volumes")
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Hammerspace: {e}")
        sys.exit(1)

    az_map = get_az_from_volumes(volumes, instance_names)
    print(f"  Resolved AZ for {len(az_map)} instance(s) from volume names")

    if not az_map:
        print("\nNo AZ mappings found in Hammerspace volumes. Nothing to rename.")
        sys.exit(0)

    # Show AZ distribution
    az_counts = {}
    for az in az_map.values():
        az_counts[az] = az_counts.get(az, 0) + 1
    print(f"\n  AZ distribution:")
    for az, count in sorted(az_counts.items()):
        print(f"    {az}: {count} instance(s)")

    # Build rename plan
    to_rename = []
    skipped_no_az = []
    skipped_already = []

    for inst_name in instance_names:
        # Check AZ mapping
        if inst_name not in az_map:
            skipped_no_az.append(inst_name)
            continue

        az = az_map[inst_name]
        new_name = f"{az}{args.separator}{inst_name}"
        current_name = oci_instances[inst_name]['display_name']

        # Skip if already has AZ prefix
        if args.skip_existing and re.match(r'^AZ\d+[_\-]', current_name):
            skipped_already.append(current_name)
            continue

        to_rename.append({
            'instance_id': oci_instances[inst_name]['id'],
            'current_name': current_name,
            'new_name': new_name,
            'az': az
        })

    # Display plan
    print(f"\n{'='*80}")
    print("RENAME PLAN")
    print(f"{'='*80}")

    if to_rename:
        print(f"\n  Instances to rename: {len(to_rename)}")
        print(f"  {'Current Name':<40} {'New Name':<45}")
        print(f"  {'-'*40} {'-'*45}")
        for r in sorted(to_rename, key=lambda x: x['new_name']):
            print(f"  {r['current_name']:<40} {r['new_name']:<45}")

    if skipped_no_az:
        print(f"\n  No AZ mapping (skipped): {len(skipped_no_az)}")
        for name in skipped_no_az[:5]:
            print(f"    - {name}")
        if len(skipped_no_az) > 5:
            print(f"    ... and {len(skipped_no_az) - 5} more")

    if skipped_already:
        print(f"\n  Already has AZ prefix (skipped): {len(skipped_already)}")
        for name in skipped_already[:5]:
            print(f"    - {name}")
        if len(skipped_already) > 5:
            print(f"    ... and {len(skipped_already) - 5} more")

    if not to_rename:
        print("\nNothing to rename.")
        sys.exit(0)

    if args.dry_run:
        print(f"\n[DRY RUN] No changes made.")
        sys.exit(0)

    # Confirmation
    if not args.yes:
        print(f"\nThis will rename {len(to_rename)} OCI instance(s).")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)

    # Apply renames
    print(f"\n{'='*80}")
    print("APPLYING CHANGES")
    print(f"{'='*80}")

    success = 0
    failed = 0

    for r in to_rename:
        print(f"\n  Renaming: {r['current_name']} -> {r['new_name']}")
        try:
            compute_client.update_instance(
                instance_id=r['instance_id'],
                update_instance_details=oci.core.models.UpdateInstanceDetails(
                    display_name=r['new_name']
                )
            )
            print(f"    OK")
            success += 1
        except oci.exceptions.ServiceError as e:
            print(f"    FAILED: {e.message}")
            failed += 1

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"  Renamed:          {success}")
    print(f"  Failed:           {failed}")
    print(f"  No AZ mapping:    {len(skipped_no_az)}")
    if skipped_already:
        print(f"  Already prefixed:  {len(skipped_already)}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
