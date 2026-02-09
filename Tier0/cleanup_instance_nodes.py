#!/usr/bin/env python3
"""
Hammerspace Cleanup Script
Deletes all volumes from matching nodes and removes those nodes from Hammerspace.

Usage:
    python3 cleanup_instance_nodes.py --host <anvil_ip> --user <api_user> --password <api_password> [filter_options]

Filter Options:
    --prefix PREFIX      Match nodes starting with prefix (default if no filter specified)
    --contains STRING    Match nodes containing string
    --pattern REGEX      Match nodes using regex pattern
    --node NAME          Match specific node name (can be repeated)
    --list-nodes         List all nodes and exit

Examples:
    # List all nodes
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password 'xxx' --list-nodes

    # Delete nodes containing 'bu-test'
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password 'xxx' --contains bu-test --dry-run

    # Delete specific nodes
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password 'xxx' --node bu-test-01 --node bu-test-02

    # Delete nodes matching regex pattern
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password 'xxx' --pattern "^bu-.*-01$" --dry-run
"""

import argparse
import re
import requests
import urllib.parse
import sys
import time
from typing import List, Dict, Any

# Disable SSL warnings for self-signed certificates
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)


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

    def delete_storage_volume(self, volume_name: str) -> bool:
        """Delete a storage volume by name."""
        encoded_name = urllib.parse.quote(volume_name, safe='')
        response = self._request("DELETE", f"storage-volumes/{encoded_name}")

        if response.status_code in [200, 202, 204]:
            # Wait for task completion if async
            if response.status_code == 202 and 'location' in response.headers:
                self._wait_for_task(response.headers['location'])
            return True
        elif response.status_code == 404:
            print(f"  Volume '{volume_name}' not found (already deleted?)")
            return True
        else:
            print(f"  Failed to delete volume '{volume_name}': {response.status_code} - {response.text}")
            return False

    def delete_node(self, node_uuid: str, node_name: str) -> bool:
        """Delete a node by UUID."""
        response = self._request("DELETE", f"nodes/{node_uuid}")

        if response.status_code in [200, 202, 204]:
            # Wait for task completion if async
            if response.status_code == 202 and 'location' in response.headers:
                self._wait_for_task(response.headers['location'])
            return True
        elif response.status_code == 404:
            print(f"  Node '{node_name}' not found (already deleted?)")
            return True
        else:
            print(f"  Failed to delete node '{node_name}': {response.status_code} - {response.text}")
            return False

    def _wait_for_task(self, task_url: str, timeout: int = 120, interval: int = 5):
        """Wait for an async task to complete."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.session.get(task_url)
            if response.status_code == 200:
                task_status = response.json().get('status', '')
                if task_status == 'COMPLETED':
                    return True
                elif task_status in ['FAILED', 'CANCELLED']:
                    print(f"  Task failed with status: {task_status}")
                    return False
            time.sleep(interval)
        print(f"  Task timed out after {timeout} seconds")
        return False


def find_instance_nodes(nodes: List[Dict[str, Any]], prefix: str = None, contains: str = None,
                        pattern: str = None, node_names: List[str] = None) -> List[Dict[str, Any]]:
    """Find nodes matching the specified filter criteria.

    Args:
        nodes: List of all nodes
        prefix: Match nodes starting with this prefix
        contains: Match nodes containing this string
        pattern: Match nodes using regex pattern
        node_names: Match specific node names
    """
    matching_nodes = []

    for node in nodes:
        node_name = node.get('name', '')

        # Match by specific node names
        if node_names:
            if node_name in node_names:
                matching_nodes.append(node)
            continue

        # Match by regex pattern
        if pattern:
            if re.search(pattern, node_name, re.IGNORECASE):
                matching_nodes.append(node)
            continue

        # Match by contains
        if contains:
            if contains.lower() in node_name.lower():
                matching_nodes.append(node)
            continue

        # Match by prefix (default behavior)
        if prefix:
            if node_name.lower().startswith(prefix.lower()):
                matching_nodes.append(node)
            continue

    return matching_nodes


def find_volumes_for_node(volumes: List[Dict[str, Any]], node_name: str) -> List[Dict[str, Any]]:
    """Find all volumes associated with a node."""
    matching_volumes = []
    for volume in volumes:
        # Check volume name pattern: [AZ:]nodename::path
        vol_name = volume.get('name', '')

        # Check if volume belongs to this node
        # Volume names follow pattern: [AZ:]nodename::path or nodename::path
        if f"{node_name}::" in vol_name:
            matching_volumes.append(volume)

        # Also check the node reference in the volume
        vol_node = volume.get('node', {})
        if vol_node and vol_node.get('name', '') == node_name:
            if volume not in matching_volumes:
                matching_volumes.append(volume)

    return matching_volumes


def main():
    parser = argparse.ArgumentParser(
        description="Delete all volumes from matching nodes and remove those nodes from Hammerspace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Filter examples:
  --prefix instance          Match nodes starting with 'instance' (default)
  --contains bu-test         Match nodes containing 'bu-test'
  --pattern "^bu-.*-01$"     Match nodes using regex pattern
  --node node1 --node node2  Match specific node names
  --list-nodes               List all nodes without deleting

Examples:
  python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password 'xxx' --list-nodes
  python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password 'xxx' --contains bu-test --dry-run
  python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password 'xxx' --node bu-test-01 --node bu-test-02
        """
    )
    parser.add_argument('--host', required=True, help='Hammerspace Anvil IP or hostname')
    parser.add_argument('--user', required=True, help='API username')
    parser.add_argument('--password', required=True, help='API password')

    # Filter options (mutually exclusive)
    filter_group = parser.add_mutually_exclusive_group()
    filter_group.add_argument('--prefix', help='Match nodes starting with this prefix')
    filter_group.add_argument('--contains', help='Match nodes containing this string')
    filter_group.add_argument('--pattern', help='Match nodes using regex pattern')
    filter_group.add_argument('--node', action='append', dest='nodes', metavar='NAME',
                              help='Match specific node name (can be used multiple times)')
    filter_group.add_argument('--list-nodes', action='store_true', help='List all nodes and exit')

    parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted without actually deleting')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')

    args = parser.parse_args()

    # Default to prefix='instance' if no filter specified
    if not any([args.prefix, args.contains, args.pattern, args.nodes, args.list_nodes]):
        args.prefix = 'instance'

    # Initialize client
    print(f"Connecting to Hammerspace at {args.host}...")
    client = HammerspaceClient(args.host, args.user, args.password)

    # Get all nodes
    try:
        print("Fetching nodes...")
        all_nodes = client.get_all_nodes()
        print(f"  Found {len(all_nodes)} total nodes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching nodes: {e}")
        sys.exit(1)

    # List all nodes and exit if requested
    if args.list_nodes:
        print("\nAll nodes in Hammerspace:")
        for node in sorted(all_nodes, key=lambda x: x.get('name', '')):
            node_name = node.get('name', 'N/A')
            node_type = node.get('nodeType', 'N/A')
            node_ip = node.get('mgmtIpAddress', {}).get('address', 'N/A')
            print(f"  - {node_name} (Type: {node_type}, IP: {node_ip})")
        sys.exit(0)

    # Build filter description for output
    if args.nodes:
        filter_desc = f"matching names: {', '.join(args.nodes)}"
    elif args.pattern:
        filter_desc = f"matching pattern: '{args.pattern}'"
    elif args.contains:
        filter_desc = f"containing: '{args.contains}'"
    else:
        filter_desc = f"starting with: '{args.prefix}'"

    # Find nodes matching filter
    instance_nodes = find_instance_nodes(
        all_nodes,
        prefix=args.prefix,
        contains=args.contains,
        pattern=args.pattern,
        node_names=args.nodes
    )

    if not instance_nodes:
        print(f"\nNo nodes found {filter_desc}")
        sys.exit(0)

    print(f"\nFound {len(instance_nodes)} nodes {filter_desc}:")
    for node in instance_nodes:
        print(f"  - {node.get('name')} (UUID: {node.get('uoid', {}).get('uuid', 'N/A')})")

    # Get all volumes
    try:
        print("\nFetching storage volumes...")
        all_volumes = client.get_all_storage_volumes()
        print(f"  Found {len(all_volumes)} total volumes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching volumes: {e}")
        sys.exit(1)

    # Map volumes to nodes
    node_volumes = {}
    for node in instance_nodes:
        node_name = node.get('name')
        volumes = find_volumes_for_node(all_volumes, node_name)
        node_volumes[node_name] = volumes

    # Display what will be deleted
    total_volumes = sum(len(v) for v in node_volumes.values())
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Will delete {total_volumes} volumes from {len(instance_nodes)} nodes:")
    for node_name, volumes in node_volumes.items():
        print(f"\n  Node: {node_name}")
        if volumes:
            for vol in volumes:
                print(f"    - Volume: {vol.get('name')}")
        else:
            print(f"    - (no volumes)")

    # Confirmation
    if not args.dry_run and not args.yes:
        print(f"\n{'='*60}")
        print("WARNING: This will permanently delete the above resources!")
        print(f"{'='*60}")
        confirm = input("\nType 'yes' to confirm deletion: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)

    if args.dry_run:
        print("\n[DRY RUN] No changes made.")
        sys.exit(0)

    # Delete volumes first
    print("\n" + "="*60)
    print("PHASE 1: Deleting volumes...")
    print("="*60)

    deleted_volumes = 0
    failed_volumes = 0

    for node_name, volumes in node_volumes.items():
        if volumes:
            print(f"\nDeleting volumes for node '{node_name}':")
            for volume in volumes:
                vol_name = volume.get('name')
                print(f"  Deleting volume: {vol_name}...")
                if client.delete_storage_volume(vol_name):
                    deleted_volumes += 1
                    print(f"  ✓ Deleted: {vol_name}")
                else:
                    failed_volumes += 1
                time.sleep(1)  # Small delay between deletions

    print(f"\nVolume deletion complete: {deleted_volumes} deleted, {failed_volumes} failed")

    # Delete nodes
    print("\n" + "="*60)
    print("PHASE 2: Deleting nodes...")
    print("="*60)

    deleted_nodes = 0
    failed_nodes = 0

    for node in instance_nodes:
        node_name = node.get('name')
        node_uuid = node.get('uoid', {}).get('uuid')

        if not node_uuid:
            print(f"  Skipping node '{node_name}': no UUID found")
            failed_nodes += 1
            continue

        print(f"\nDeleting node: {node_name}...")
        if client.delete_node(node_uuid, node_name):
            deleted_nodes += 1
            print(f"  ✓ Deleted: {node_name}")
        else:
            failed_nodes += 1
        time.sleep(1)  # Small delay between deletions

    print(f"\nNode deletion complete: {deleted_nodes} deleted, {failed_nodes} failed")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Volumes: {deleted_volumes} deleted, {failed_volumes} failed")
    print(f"Nodes:   {deleted_nodes} deleted, {failed_nodes} failed")

    if failed_volumes > 0 or failed_nodes > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
