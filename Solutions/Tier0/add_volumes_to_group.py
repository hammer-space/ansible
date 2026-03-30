#!/usr/bin/env python3
"""
Add volumes to an existing Hammerspace volume group for instances listed in a file.

Reads instance names from a file (e.g., tier0_instances_limit), finds their volumes
in Hammerspace, and adds them to a specified volume group.

Prerequisites:
    pip3 install requests

Usage:
    # Dry run - see what would be added
    python3 add_volumes_to_group.py --host <anvil_ip> --user admin --password-file ~/.hs_password \
        --group "tier0-az1-volumes" --instances-file tier0_instances_limit --dry-run

    # Apply changes
    python3 add_volumes_to_group.py --host <anvil_ip> --user admin --password-file ~/.hs_password \
        --group "tier0-az1-volumes" --instances-file tier0_instances_limit

    # Filter by AZ prefix
    python3 add_volumes_to_group.py --host <anvil_ip> --user admin --password-file ~/.hs_password \
        --group "tier0-az1-volumes" --instances-file tier0_instances_limit --az AZ1

    # List current volume group members
    python3 add_volumes_to_group.py --host <anvil_ip> --user admin --password-file ~/.hs_password \
        --group "tier0-az1-volumes" --list

Examples:
    python3 add_volumes_to_group.py --host 10.0.10.15 --user admin --password-file ~/.hs_password \
        --group "tier0-az1-volumes" --instances-file tier0_instances_limit --dry-run

    python3 add_volumes_to_group.py --host 10.0.10.15 --user admin --password-file ~/.hs_password \
        --group "tier0-az1-volumes" --instances-file tier0_instances_limit --yes
"""

import argparse
import getpass
import os
import requests
import sys
import time
import urllib.parse
from typing import List, Dict, Any, Optional

# Disable SSL warnings for self-signed certificates
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


class HammerspaceClient:
    def __init__(self, host: str, user: str, password: str, port: int = 8443,
                 verify_ssl: bool = False, max_retries: int = 3, retry_backoff: float = 2.0):
        self.base_url = f"https://{host}:{port}/mgmt/v1.2/rest"
        self.auth = (user, password)
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = self.verify_ssl

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an API request with retry on transient errors."""
        url = f"{self.base_url}/{endpoint}"
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                if response.status_code in (502, 503, 504) and attempt < self.max_retries - 1:
                    wait = self.retry_backoff ** attempt
                    print(f"    Retry {attempt + 1}/{self.max_retries} after HTTP {response.status_code} (wait {wait:.0f}s)")
                    time.sleep(wait)
                    continue
                return response
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait = self.retry_backoff ** attempt
                    print(f"    Retry {attempt + 1}/{self.max_retries} after connection error (wait {wait:.0f}s)")
                    time.sleep(wait)
        raise requests.exceptions.ConnectionError(f"Failed after {self.max_retries} retries: {last_exception}")

    def get_all_storage_volumes(self) -> List[Dict[str, Any]]:
        response = self._request("GET", "storage-volumes")
        response.raise_for_status()
        return response.json()

    def get_volume_group(self, group_name: str) -> Optional[Dict[str, Any]]:
        encoded = urllib.parse.quote(group_name, safe='')
        response = self._request("GET", f"volume-groups/{encoded}")
        if response.status_code == 200:
            return response.json()
        return None

    def update_volume_group(self, group_name: str, payload: Dict) -> tuple:
        encoded = urllib.parse.quote(group_name, safe='')
        response = self._request("PUT", f"volume-groups/{encoded}", json=payload)
        if response.status_code in [200, 201, 202]:
            return True, ""
        return False, f"HTTP {response.status_code}: {response.text[:500]}"


def load_instances_file(filepath: str) -> List[str]:
    """Load instance names from file, one per line."""
    instances = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                instances.append(line)
    return instances


def find_volumes_for_instances(volumes: List[Dict], instance_names: List[str],
                                az_filter: str = None) -> List[Dict]:
    """Find volumes belonging to the listed instances."""
    matched = []
    for volume in volumes:
        vol_name = volume.get('name', '')
        vol_node = volume.get('node', {}).get('name', '')

        # Check if volume belongs to any listed instance
        belongs = False
        for inst in instance_names:
            if f"{inst}::" in vol_name or vol_node == inst:
                belongs = True
                break

        if not belongs:
            continue

        # Optional AZ filter
        if az_filter:
            if not vol_name.startswith(f"{az_filter}:"):
                continue

        matched.append(volume)

    return matched


def get_existing_group_volumes(group_data: Dict) -> List[str]:
    """Extract volume names already in the volume group."""
    existing = []
    expressions = group_data.get('expressions', [])
    for expr in expressions:
        locations = expr.get('locations', [])
        for loc in locations:
            sv = loc.get('storageVolume', {})
            name = sv.get('name', '')
            if name:
                existing.append(name)
    return existing


def main():
    parser = argparse.ArgumentParser(
        description="Add volumes to an existing Hammerspace volume group for instances in a file",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--host', required=True, help='Hammerspace Anvil IP or hostname')
    parser.add_argument('--port', type=int, default=8443, help='API port (default: 8443)')
    parser.add_argument('--user', required=True, help='API username')
    parser.add_argument('--password', help='API password (or use --password-file / HAMMERSPACE_PASSWORD env var)')
    parser.add_argument('--password-file', help='Path to file containing API password')
    parser.add_argument('--group', required=True, help='Volume group name (must already exist)')
    parser.add_argument('--instances-file', default='tier0_instances_limit',
                        help='File with instance names, one per line (default: tier0_instances_limit)')
    parser.add_argument('--az', help='Only include volumes with this AZ prefix (e.g., AZ1)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be added without applying')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')
    parser.add_argument('--list', action='store_true', help='List current volume group members and exit')

    args = parser.parse_args()

    # Resolve password: --password > --password-file > HAMMERSPACE_PASSWORD env > prompt
    if args.password:
        password = args.password
    elif args.password_file:
        with open(args.password_file) as f:
            password = f.read().strip()
    elif os.environ.get('HAMMERSPACE_PASSWORD'):
        password = os.environ['HAMMERSPACE_PASSWORD']
    else:
        password = getpass.getpass('Hammerspace API password: ')

    # Connect to Hammerspace
    print(f"Connecting to Hammerspace at {args.host}:{args.port}...")
    client = HammerspaceClient(args.host, args.user, password, port=args.port)

    # Get volume group
    print(f"Fetching volume group '{args.group}'...")
    group_data = client.get_volume_group(args.group)
    if not group_data:
        print(f"ERROR: Volume group '{args.group}' not found.")
        sys.exit(1)

    existing_volume_names = get_existing_group_volumes(group_data)
    print(f"  Volume group has {len(existing_volume_names)} existing member(s)")

    # List mode
    if args.list:
        print(f"\nMembers of volume group '{args.group}':")
        if existing_volume_names:
            for name in sorted(existing_volume_names):
                print(f"  - {name}")
        else:
            print("  (empty)")
        sys.exit(0)

    # Load instances
    try:
        instance_names = load_instances_file(args.instances_file)
        print(f"\nLoaded {len(instance_names)} instance(s) from {args.instances_file}")
    except FileNotFoundError:
        print(f"ERROR: File not found: {args.instances_file}")
        sys.exit(1)

    # Get all volumes
    print("Fetching storage volumes...")
    try:
        all_volumes = client.get_all_storage_volumes()
        print(f"  Found {len(all_volumes)} total volumes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching volumes: {e}")
        sys.exit(1)

    # Find volumes for listed instances
    matched_volumes = find_volumes_for_instances(all_volumes, instance_names, az_filter=args.az)
    print(f"  Matched {len(matched_volumes)} volume(s) for listed instances"
          + (f" (AZ filter: {args.az})" if args.az else ""))

    if not matched_volumes:
        print("\nNo matching volumes found. Nothing to do.")
        sys.exit(0)

    # Determine which are new (not already in group)
    existing_set = set(existing_volume_names)
    to_add = []
    already_in = []

    for vol in matched_volumes:
        vol_name = vol.get('name', '')
        if vol_name in existing_set:
            already_in.append(vol_name)
        else:
            to_add.append(vol_name)

    # Display summary
    print(f"\n{'='*70}")
    print(f"VOLUME GROUP: {args.group}")
    print(f"{'='*70}")
    print(f"  Already in group:  {len(already_in)}")
    print(f"  To be added:       {len(to_add)}")

    if already_in:
        print(f"\n  Already members (skipped):")
        for name in sorted(already_in)[:10]:
            print(f"    - {name}")
        if len(already_in) > 10:
            print(f"    ... and {len(already_in) - 10} more")

    if not to_add:
        print("\nAll matched volumes are already in the group. Nothing to do.")
        sys.exit(0)

    print(f"\n  Volumes to add:")
    for name in sorted(to_add):
        print(f"    + {name}")

    if args.dry_run:
        print(f"\n[DRY RUN] No changes made.")
        sys.exit(0)

    # Confirmation
    if not args.yes:
        print(f"\nThis will add {len(to_add)} volume(s) to group '{args.group}'.")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)

    # Re-fetch the full volume group data for the PUT payload
    group_data = client.get_volume_group(args.group)
    if not group_data:
        print(f"ERROR: Could not re-fetch volume group '{args.group}'.")
        sys.exit(1)

    # Update locations in the first expression
    expressions = group_data.get('expressions', [])
    if expressions:
        current_locations = expressions[0].get('locations', [])
    else:
        current_locations = []
        expressions = [{"operator": "IN", "locations": []}]
        group_data['expressions'] = expressions

    # Append new volumes
    for vol_name in to_add:
        current_locations.append({
            "_type": "VOLUME_LOCATION",
            "extendedInfo": {},
            "storageVolume": {
                "_type": "STORAGE_VOLUME",
                "extendedInfo": {},
                "name": vol_name
            }
        })

    expressions[0]['locations'] = current_locations

    # Use full group data as PUT payload
    update_payload = group_data

    # Apply
    print(f"\n{'='*70}")
    print("APPLYING CHANGES")
    print(f"{'='*70}")

    ok, error = client.update_volume_group(args.group, update_payload)
    if ok:
        print(f"\n  Successfully added {len(to_add)} volume(s) to '{args.group}'")
    else:
        print(f"\n  FAILED to update volume group: {error}")
        sys.exit(1)

    # Final summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Volume group:      {args.group}")
    print(f"  Volumes added:     {len(to_add)}")
    print(f"  Already in group:  {len(already_in)}")
    print(f"  Total in group:    {len(existing_volume_names) + len(to_add)}")


if __name__ == "__main__":
    main()
