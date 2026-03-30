#!/usr/bin/env python3
"""
Hammerspace Cleanup Script
Deletes all volumes from matching nodes and removes those nodes from Hammerspace.

Usage:
    python3 cleanup_instance_nodes.py --host <anvil_ip> --user <api_user> --password-file ~/.hs_password [filter_options]

Filter Options:
    --prefix PREFIX      Match nodes starting with prefix (default if no filter specified)
    --contains STRING    Match nodes containing string
    --pattern REGEX      Match nodes using regex pattern
    --node NAME          Match specific node name (can be repeated)
    --list-nodes         List all nodes and exit

Examples:
    # List all nodes
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password-file ~/.hs_password --list-nodes

    # Delete nodes containing 'bu-test'
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password-file ~/.hs_password --contains bu-test --dry-run

    # Delete specific nodes
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password-file ~/.hs_password --node bu-test-01 --node bu-test-02

    # Delete nodes matching regex pattern
    python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password-file ~/.hs_password --pattern "^bu-.*-01$" --dry-run
"""

import argparse
import getpass
import os
import re
import requests
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

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
        """Delete a storage volume and wait until it is fully removed from Hammerspace."""
        encoded_name = urllib.parse.quote(volume_name, safe='')
        response = self._request("DELETE", f"storage-volumes/{encoded_name}")

        if response.status_code in [200, 202, 204]:
            # Wait for async task if present
            if response.status_code == 202 and 'location' in response.headers:
                self._wait_for_task(response.headers['location'])
            # Verify the volume is actually gone from Hammerspace
            if not self.wait_for_volume_deletion(volume_name):
                print(f"  Volume '{volume_name}' still present after delete request")
                return False
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

    def get_storage_volume(self, volume_name: str) -> Dict[str, Any] | None:
        """Get a single storage volume by name. Returns None if not found."""
        encoded_name = urllib.parse.quote(volume_name, safe='')
        response = self._request("GET", f"storage-volumes/{encoded_name}")
        if response.status_code == 200:
            return response.json()
        return None

    def wait_for_volume_deletion(self, volume_name: str, interval: int = 5) -> bool:
        """Wait until a volume is fully deleted (no longer exists or not in Executing state)."""
        while True:
            vol = self.get_storage_volume(volume_name)
            if vol is None:
                return True
            status = vol.get('operationalStatus', '')
            if status == 'Executing':
                print(f"    Volume '{volume_name}' still Executing, waiting...")
                time.sleep(interval)
            else:
                # Volume exists but not executing - deletion may have failed
                return False

    def _wait_for_task(self, task_url: str, timeout: int = 300, interval: int = 5):
        """Wait for an async task to complete with timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self._request_url(task_url)
                if response.status_code == 200:
                    task_status = response.json().get('status', '')
                    if task_status == 'COMPLETED':
                        return True
                    elif task_status in ['FAILED', 'CANCELLED']:
                        print(f"  Task failed with status: {task_status}")
                        return False
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(interval)
        print(f"  Task timed out after {timeout}s")
        return False

    def _request_url(self, url: str, method: str = "GET", **kwargs) -> requests.Response:
        """Make a request to a full URL with retry."""
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                if response.status_code in (502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff ** attempt)
                    continue
                return response
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_backoff ** attempt)
        raise requests.exceptions.ConnectionError(f"Failed after {self.max_retries} retries: {last_exception}")


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
  python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password-file ~/.hs_password --list-nodes
  python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password-file ~/.hs_password --contains bu-test --dry-run
  python3 cleanup_instance_nodes.py --host 10.1.2.3 --user admin --password-file ~/.hs_password --node bu-test-01 --node bu-test-02
        """
    )
    parser.add_argument('--host', required=True, help='Hammerspace Anvil IP or hostname')
    parser.add_argument('--port', type=int, default=8443, help='API port (default: 8443)')
    parser.add_argument('--user', required=True, help='API username')
    parser.add_argument('--password', help='API password (or use --password-file / HAMMERSPACE_PASSWORD env var)')
    parser.add_argument('--password-file', help='Path to file containing API password')

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
    parser.add_argument('--parallel', type=int, default=1, metavar='N',
                        help='Number of parallel volume deletions (default: 1)')

    args = parser.parse_args()

    # Default to prefix='instance' if no filter specified
    if not any([args.prefix, args.contains, args.pattern, args.nodes, args.list_nodes]):
        args.prefix = 'instance'

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

    # Initialize client
    print(f"Connecting to Hammerspace at {args.host}:{args.port}...")
    client = HammerspaceClient(args.host, args.user, password, port=args.port)

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
    print(f"PHASE 1: Deleting volumes (parallel: {args.parallel})...")
    print("="*60)

    deleted_volumes = 0
    failed_volumes = 0
    # Track volume deletion failures per node
    node_volume_failures = {node.get('name'): 0 for node in instance_nodes}

    # Collect all volumes into a flat list with their node name
    all_volume_tasks = []
    for node_name, volumes in node_volumes.items():
        for volume in volumes:
            all_volume_tasks.append((node_name, volume))

    if args.parallel > 1 and all_volume_tasks:
        def _delete_volume(task):
            node_name, volume = task
            vol_name = volume.get('name')
            print(f"  [{node_name}] Deleting volume: {vol_name}...")
            success = client.delete_storage_volume(vol_name)
            if success:
                print(f"  [{node_name}] ✓ Deleted: {vol_name}")
            return node_name, success

        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {executor.submit(_delete_volume, task): task for task in all_volume_tasks}
            for future in as_completed(futures):
                node_name, success = future.result()
                if success:
                    deleted_volumes += 1
                else:
                    failed_volumes += 1
                    node_volume_failures[node_name] += 1
    else:
        for node_name, volume in all_volume_tasks:
            vol_name = volume.get('name')
            print(f"  [{node_name}] Deleting volume: {vol_name}...")
            if client.delete_storage_volume(vol_name):
                deleted_volumes += 1
                print(f"  [{node_name}] ✓ Deleted: {vol_name}")
            else:
                failed_volumes += 1
                node_volume_failures[node_name] += 1

    print(f"\nVolume deletion complete: {deleted_volumes} deleted, {failed_volumes} failed")

    # Delete nodes (only if all volumes were fully removed)
    print("\n" + "="*60)
    print("PHASE 2: Deleting nodes...")
    print("="*60)

    deleted_nodes = 0
    failed_nodes = 0
    skipped_nodes = 0

    for node in instance_nodes:
        node_name = node.get('name')
        node_uuid = node.get('uoid', {}).get('uuid')

        if node_volume_failures.get(node_name, 0) > 0:
            print(f"\n  Skipping node '{node_name}': not all volumes were removed")
            skipped_nodes += 1
            continue

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

    print(f"\nNode deletion complete: {deleted_nodes} deleted, {failed_nodes} failed, {skipped_nodes} skipped")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Volumes: {deleted_volumes} deleted, {failed_volumes} failed")
    print(f"Nodes:   {deleted_nodes} deleted, {failed_nodes} failed, {skipped_nodes} skipped")

    if failed_volumes > 0 or failed_nodes > 0 or skipped_nodes > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
