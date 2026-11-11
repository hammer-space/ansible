#!/usr/bin/env python3
"""
Set per-volume utilization thresholds on existing Hammerspace volumes.

Two thresholds on each STORAGE_VOLUME live under
storageCapabilities.performance:
  - utilizationThreshold           (high — read/write halts approach this)
  - utilizationEvacuationThreshold (low  — evacuation target after high trips)

Both are floats in [0.0, 1.0]. This script PUTs updated values back via the
Anvil REST API. Filtering follows the same convention as
set_availability_drop.py / cleanup_instance_nodes.py.

Defaults: high=0.98, low=0.96.

Usage:
    # Check current thresholds on volumes for specific nodes
    python3 set_volume_thresholds.py --host <anvil_ip> --user admin \\
        --password-file ~/.hs_password \\
        --node instance20260116093135 --check

    # Set defaults (high=0.98, low=0.96) on every volume for matching nodes,
    # preview first:
    python3 set_volume_thresholds.py --host <anvil_ip> --user admin \\
        --password-file ~/.hs_password \\
        --prefix tier0- --dry-run

    # Apply:
    python3 set_volume_thresholds.py --host <anvil_ip> --user admin \\
        --password-file ~/.hs_password \\
        --prefix tier0- --yes

    # Apply to ALL volumes regardless of node (use with caution):
    python3 set_volume_thresholds.py --host <anvil_ip> --user admin \\
        --password-file ~/.hs_password \\
        --all-nodes --yes

    # Override defaults:
    python3 set_volume_thresholds.py --host <anvil_ip> --user admin \\
        --password-file ~/.hs_password \\
        --prefix tier0- --high 0.95 --low 0.90

    # From an instance-list file (one name per line, same as Ansible --limit @):
    python3 set_volume_thresholds.py --host <anvil_ip> --user admin \\
        --password-file ~/.hs_password \\
        --instances-file add_volumes.txt
"""

import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple

import requests

# Self-signed cert on the Anvil — match the other scripts in the repo.
requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)

# Default thresholds.
DEFAULT_HIGH = 0.98
DEFAULT_LOW = 0.96

# Float comparison tolerance for "already correct" detection.
EPS = 1e-4


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
        url = f"{self.base_url}/{endpoint}"
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                if response.status_code in (502, 503, 504) and attempt < self.max_retries - 1:
                    wait = self.retry_backoff ** attempt
                    print(f"    Retry {attempt + 1}/{self.max_retries} after HTTP "
                          f"{response.status_code} (wait {wait:.0f}s)")
                    time.sleep(wait)
                    continue
                return response
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait = self.retry_backoff ** attempt
                    print(f"    Retry {attempt + 1}/{self.max_retries} after "
                          f"connection error (wait {wait:.0f}s)")
                    time.sleep(wait)
        raise requests.exceptions.ConnectionError(
            f"Failed after {self.max_retries} retries: {last_exception}"
        )

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        response = self._request("GET", "nodes")
        response.raise_for_status()
        return response.json()

    def get_all_storage_volumes(self) -> List[Dict[str, Any]]:
        response = self._request("GET", "storage-volumes")
        response.raise_for_status()
        return response.json()

    def get_volume(self, volume_id: str) -> Optional[Dict[str, Any]]:
        response = self._request("GET", f"storage-volumes/{volume_id}")
        if response.status_code == 200:
            return response.json()
        return None

    def update_volume(self, volume_id: str, volume_data: Dict[str, Any]) -> Tuple[bool, str]:
        response = self._request("PUT", f"storage-volumes/{volume_id}", json=volume_data)
        if response.status_code in (200, 202, 204):
            if response.status_code == 202 and 'location' in response.headers:
                self._wait_for_task(response.headers['location'])
            return True, ""
        return False, f"HTTP {response.status_code}: {response.text[:500]}"

    def _wait_for_task(self, task_url: str, timeout: int = 120, interval: int = 5) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                r = self.session.get(task_url, verify=self.verify_ssl)
                if r.status_code == 200:
                    status = r.json().get('status', '')
                    if status == 'COMPLETED':
                        return True
                    if status in ('FAILED', 'CANCELLED'):
                        print(f"    Task ended: {status}")
                        return False
            except requests.exceptions.RequestException:
                pass
            time.sleep(interval)
        print(f"    Task timed out after {timeout}s")
        return False


# ─── Filtering helpers (mirrors set_availability_drop.py) ──────────────────

def find_matching_nodes(nodes: List[Dict], prefix: str = None, contains: str = None,
                        pattern: str = None, node_names: List[str] = None,
                        all_nodes: bool = False) -> List[Dict]:
    if all_nodes:
        return nodes
    matching = []
    for node in nodes:
        name = node.get('name', '')
        if node_names:
            if name in node_names:
                matching.append(node)
            continue
        if pattern and re.search(pattern, name, re.IGNORECASE):
            matching.append(node)
            continue
        if contains and contains.lower() in name.lower():
            matching.append(node)
            continue
        if prefix and name.lower().startswith(prefix.lower()):
            matching.append(node)
            continue
    return matching


def find_volumes_for_nodes(volumes: List[Dict], node_names: List[str]) -> Dict[str, List[Dict]]:
    node_volumes = {name: [] for name in node_names}
    for volume in volumes:
        vol_name = volume.get('name', '')
        vol_node = volume.get('node', {}).get('name', '')
        for node_name in node_names:
            if f"{node_name}::" in vol_name or vol_node == node_name:
                node_volumes[node_name].append(volume)
                break
    return node_volumes


def get_thresholds(volume: Dict) -> Tuple[Optional[float], Optional[float]]:
    perf = volume.get('storageCapabilities', {}).get('performance', {})
    return perf.get('utilizationThreshold'), perf.get('utilizationEvacuationThreshold')


def fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def equal_enough(a: Optional[float], b: float) -> bool:
    return a is not None and abs(a - b) < EPS


# ─── Modes ────────────────────────────────────────────────────────────────

def do_check(node_volumes: Dict[str, List[Dict]], target_high: float, target_low: float):
    print("\n" + "=" * 80)
    print(f"VOLUME THRESHOLDS — target high={fmt_pct(target_high)} "
          f"low={fmt_pct(target_low)}")
    print("=" * 80)

    total = 0
    needs_change = 0
    correct = 0

    for node_name, volumes in sorted(node_volumes.items()):
        print(f"\n  Node: {node_name}")
        if not volumes:
            print("    (no volumes)")
            continue
        for vol in volumes:
            total += 1
            vol_name = vol.get('name', '')
            high, low = get_thresholds(vol)
            mismatch = not (equal_enough(high, target_high) and equal_enough(low, target_low))
            marker = "  <-- needs change" if mismatch else ""
            if mismatch:
                needs_change += 1
            else:
                correct += 1
            print(f"    {vol_name}")
            print(f"      high={fmt_pct(high)}  low={fmt_pct(low)}{marker}")

    print(f"\n  Summary: {total} volumes — {correct} already correct, "
          f"{needs_change} need update")


def do_apply(client: HammerspaceClient, node_volumes: Dict[str, List[Dict]],
             target_high: float, target_low: float,
             dry_run: bool = False, skip_confirm: bool = False):
    to_update: List[Tuple[str, Dict]] = []
    already_set: List[Dict] = []

    for node_name, volumes in sorted(node_volumes.items()):
        for vol in volumes:
            high, low = get_thresholds(vol)
            if equal_enough(high, target_high) and equal_enough(low, target_low):
                already_set.append(vol)
            else:
                to_update.append((node_name, vol))

    if not to_update:
        print(f"\nAll {len(already_set)} volume(s) already at target thresholds. "
              "Nothing to do.")
        return

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Set utilization thresholds to "
          f"high={fmt_pct(target_high)} low={fmt_pct(target_low)}")
    print(f"  Volumes to update: {len(to_update)}")
    print(f"  Already correct:   {len(already_set)}")
    print()
    for _, vol in to_update:
        high, low = get_thresholds(vol)
        print(f"  {vol.get('name', '')}")
        print(f"    high {fmt_pct(high)} -> {fmt_pct(target_high)}    "
              f"low {fmt_pct(low)} -> {fmt_pct(target_low)}")

    if dry_run:
        print("\n[DRY RUN] No changes made.")
        return

    if not skip_confirm:
        print(f"\nThis will update {len(to_update)} volume(s).")
        if input("Type 'yes' to confirm: ").lower() != 'yes':
            print("Aborted.")
            sys.exit(0)

    print(f"\n{'=' * 80}")
    print("APPLYING")
    print("=" * 80)

    success = 0
    failed = 0
    for node_name, vol in to_update:
        vol_name = vol.get('name', '')
        vol_uuid = vol.get('uoid', {}).get('uuid', '')
        volume_id = vol_uuid or urllib.parse.quote(vol_name, safe='')

        print(f"\n  Updating: {vol_name}")
        fresh = client.get_volume(volume_id)
        if not fresh:
            print("    FAILED: could not fetch volume")
            failed += 1
            continue

        caps = fresh.setdefault('storageCapabilities', {})
        perf = caps.setdefault('performance', {})
        perf['utilizationThreshold'] = target_high
        perf['utilizationEvacuationThreshold'] = target_low

        ok, error = client.update_volume(volume_id, fresh)
        if ok:
            print(f"    OK: high={fmt_pct(target_high)} low={fmt_pct(target_low)}")
            success += 1
        else:
            print(f"    FAILED: {error}")
            failed += 1

    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print("=" * 80)
    print(f"  Updated:         {success}")
    print(f"  Failed:          {failed}")
    print(f"  Already correct: {len(already_set)}")
    if failed > 0:
        sys.exit(1)


# ─── CLI ──────────────────────────────────────────────────────────────────

def parse_threshold(s: str, name: str) -> float:
    """Accept 0.98 or 98 or 98%."""
    s = s.strip().rstrip('%')
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--{name} must be a number (got {s!r})")
    if v > 1.0:
        v = v / 100.0
    if not (0.0 < v <= 1.0):
        raise argparse.ArgumentTypeError(
            f"--{name} must be in (0, 1.0] (or 0-100%), got {v}"
        )
    return v


def main():
    parser = argparse.ArgumentParser(
        description="Set per-volume utilization thresholds on Hammerspace volumes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
API fields touched (per volume):
  storageCapabilities.performance.utilizationThreshold           (high)
  storageCapabilities.performance.utilizationEvacuationThreshold (low)

Defaults: high=0.98 (98%), low=0.96 (96%)
Override with --high / --low (accepts 0.96, 96, or 96%).

Workflow:
  1. Preview:  --check
  2. Plan:     --dry-run
  3. Apply:    (no --dry-run); --yes to skip confirmation
        """
    )

    # Connection
    parser.add_argument('--host', required=True, help='Hammerspace Anvil IP/hostname')
    parser.add_argument('--port', type=int, default=8443, help='API port (default: 8443)')
    parser.add_argument('--user', required=True, help='API username')
    parser.add_argument('--password', help='API password (or --password-file / HAMMERSPACE_PASSWORD)')
    parser.add_argument('--password-file', help='File containing API password')

    # Node filter (mutually exclusive)
    fg = parser.add_mutually_exclusive_group()
    fg.add_argument('--node', action='append', dest='nodes', metavar='NAME',
                    help='Specific node name (repeatable)')
    fg.add_argument('--prefix', help='Match nodes starting with this prefix')
    fg.add_argument('--contains', help='Match nodes containing this substring')
    fg.add_argument('--pattern', help='Match nodes by regex (case-insensitive)')
    fg.add_argument('--instances-file', help='File with one node name per line '
                    '(same shape as Ansible --limit @file)')
    fg.add_argument('--all-nodes', action='store_true',
                    help='Apply to ALL nodes (use with caution)')

    # Action
    parser.add_argument('--check', action='store_true',
                        help='Report current thresholds; do not modify')
    parser.add_argument('--high', type=lambda s: parse_threshold(s, 'high'),
                        default=DEFAULT_HIGH,
                        help=f'High threshold — utilizationThreshold (default {DEFAULT_HIGH})')
    parser.add_argument('--low', type=lambda s: parse_threshold(s, 'low'),
                        default=DEFAULT_LOW,
                        help=f'Low threshold  — utilizationEvacuationThreshold (default {DEFAULT_LOW})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show planned changes without applying')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip confirmation prompt')

    args = parser.parse_args()

    if args.low >= args.high:
        parser.error(f"--low ({args.low}) must be strictly less than --high ({args.high})")

    # Resolve instances-file into args.nodes for uniform downstream handling.
    if args.instances_file:
        with open(args.instances_file) as f:
            args.nodes = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        if not args.nodes:
            parser.error(f"--instances-file '{args.instances_file}' is empty")

    if not any([args.nodes, args.prefix, args.contains, args.pattern, args.all_nodes]):
        parser.error("Must specify a node filter "
                     "(--node, --prefix, --contains, --pattern, --instances-file, or --all-nodes)")

    # Password resolution: --password > --password-file > env > prompt
    if args.password:
        password = args.password
    elif args.password_file:
        with open(args.password_file) as f:
            password = f.read().strip()
    elif os.environ.get('HAMMERSPACE_PASSWORD'):
        password = os.environ['HAMMERSPACE_PASSWORD']
    else:
        password = getpass.getpass('Hammerspace API password: ')

    print(f"Connecting to Hammerspace at {args.host}:{args.port}...")
    client = HammerspaceClient(args.host, args.user, password, port=args.port)

    try:
        all_nodes = client.get_all_nodes()
        print(f"  Found {len(all_nodes)} total nodes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching nodes: {e}")
        sys.exit(1)

    matched_nodes = find_matching_nodes(
        all_nodes,
        prefix=args.prefix,
        contains=args.contains,
        pattern=args.pattern,
        node_names=args.nodes,
        all_nodes=args.all_nodes,
    )
    if not matched_nodes:
        print("\nNo matching nodes found.")
        sys.exit(0)

    matched_names = [n.get('name', '') for n in matched_nodes]
    print(f"\nMatched {len(matched_nodes)} node(s):")
    for name in sorted(matched_names):
        print(f"  - {name}")

    try:
        all_volumes = client.get_all_storage_volumes()
        print(f"\n  Found {len(all_volumes)} total volumes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching volumes: {e}")
        sys.exit(1)

    node_volumes = find_volumes_for_nodes(all_volumes, matched_names)
    total_vols = sum(len(v) for v in node_volumes.values())
    print(f"  {total_vols} volume(s) on matched nodes")

    if total_vols == 0:
        print("\nNo volumes found on matched nodes. Nothing to do.")
        sys.exit(0)

    if args.check:
        do_check(node_volumes, args.high, args.low)
    else:
        do_apply(client, node_volumes, args.high, args.low,
                 dry_run=args.dry_run, skip_confirm=args.yes)


if __name__ == "__main__":
    main()
