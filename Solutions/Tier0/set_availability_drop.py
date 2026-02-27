#!/usr/bin/env python3
"""
Set availability-drop on Hammerspace volumes for specific nodes.

Used before RMA / planned maintenance to prevent volumes from being removed
when their storage instances are powered off. Setting availability-drop to
"disabled" keeps volumes registered (but offline) so they rejoin automatically
when the instance comes back.

Usage:
    # Check current availability-drop status for nodes being taken down
    python3 set_availability_drop.py --host <anvil_ip> --user admin --password 'xxx' \
        --node instance20260116093135 --node instance20260116093136 --check

    # Disable availability-drop (pre-shutdown: keeps volumes registered while offline)
    python3 set_availability_drop.py --host <anvil_ip> --user admin --password 'xxx' \
        --node instance20260116093135 --disable --dry-run

    # Re-enable availability-drop (post-maintenance, after instances are back)
    python3 set_availability_drop.py --host <anvil_ip> --user admin --password 'xxx' \
        --node instance20260116093135 --enable

    # Health check: verify volume and node operational state after restart
    python3 set_availability_drop.py --host <anvil_ip> --user admin --password 'xxx' \
        --node instance20260116093135 --health-check

Filter Options (same as cleanup_instance_nodes.py):
    --node NAME          Match specific node name (can be repeated)
    --prefix PREFIX      Match nodes starting with prefix
    --contains STRING    Match nodes containing string
    --pattern REGEX      Match nodes using regex pattern
    --all-nodes          Apply to ALL volumes (use with caution)

Examples:
    # Pre-shutdown workflow:
    python3 set_availability_drop.py --host 10.0.10.15 --user admin --password 'xxx' \
        --node instance20260116093135 --node instance20260116093136 --check
    python3 set_availability_drop.py --host 10.0.10.15 --user admin --password 'xxx' \
        --node instance20260116093135 --node instance20260116093136 --disable --dry-run
    python3 set_availability_drop.py --host 10.0.10.15 --user admin --password 'xxx' \
        --node instance20260116093135 --node instance20260116093136 --disable

    # Post-restart workflow:
    python3 set_availability_drop.py --host 10.0.10.15 --user admin --password 'xxx' \
        --node instance20260116093135 --node instance20260116093136 --health-check
    python3 set_availability_drop.py --host 10.0.10.15 --user admin --password 'xxx' \
        --node instance20260116093135 --node instance20260116093136 --enable
"""

import argparse
import re
import requests
import urllib.parse
import sys
import time
import json
from typing import List, Dict, Any, Optional, Tuple

# Disable SSL warnings for self-signed certificates
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

# availability-drop multiplier values
AVAIL_DROP_ENABLED = 0   # --availability-drop-enabled  (availability drops to 0 when unavailable)
AVAIL_DROP_DISABLED = 1  # --availability-drop-disabled (availability unchanged when unavailable)


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
        response = self.session.request(method, url, **kwargs)
        return response

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        response = self._request("GET", "nodes")
        response.raise_for_status()
        return response.json()

    def get_all_storage_volumes(self) -> List[Dict[str, Any]]:
        response = self._request("GET", "storage-volumes")
        response.raise_for_status()
        return response.json()

    def get_volume(self, volume_id: str) -> Optional[Dict[str, Any]]:
        """Get a single volume by UUID or URL-encoded name."""
        response = self._request("GET", f"storage-volumes/{volume_id}")
        if response.status_code == 200:
            return response.json()
        return None

    def update_volume(self, volume_id: str, volume_data: Dict[str, Any]) -> Tuple[bool, str]:
        """PUT updated volume data back to the API."""
        response = self._request("PUT", f"storage-volumes/{volume_id}", json=volume_data)
        if response.status_code in [200, 202, 204]:
            if response.status_code == 202 and 'location' in response.headers:
                self._wait_for_task(response.headers['location'])
            return True, ""
        return False, f"HTTP {response.status_code}: {response.text[:500]}"

    def get_events(self, uncleared_only: bool = True) -> List[Dict[str, Any]]:
        """Get cluster events."""
        endpoint = "events"
        if uncleared_only:
            endpoint += "?spec=cleared%3Deq%3Dfalse"
        response = self._request("GET", endpoint)
        if response.status_code == 200:
            return response.json()
        return []

    def _wait_for_task(self, task_url: str, timeout: int = 120, interval: int = 5):
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.session.get(task_url)
            if response.status_code == 200:
                task_status = response.json().get('status', '')
                if task_status == 'COMPLETED':
                    return True
                elif task_status in ['FAILED', 'CANCELLED']:
                    print(f"    Task failed: {task_status}")
                    return False
            time.sleep(interval)
        print(f"    Task timed out after {timeout}s")
        return False


def find_matching_nodes(nodes: List[Dict], prefix: str = None, contains: str = None,
                        pattern: str = None, node_names: List[str] = None,
                        all_nodes: bool = False) -> List[Dict]:
    """Filter nodes by criteria."""
    if all_nodes:
        return nodes

    matching = []
    for node in nodes:
        name = node.get('name', '')

        if node_names:
            if name in node_names:
                matching.append(node)
            continue

        if pattern:
            if re.search(pattern, name, re.IGNORECASE):
                matching.append(node)
            continue

        if contains:
            if contains.lower() in name.lower():
                matching.append(node)
            continue

        if prefix:
            if name.lower().startswith(prefix.lower()):
                matching.append(node)
            continue

    return matching


def find_volumes_for_nodes(volumes: List[Dict], node_names: List[str]) -> Dict[str, List[Dict]]:
    """Map volumes to their parent nodes."""
    node_volumes = {name: [] for name in node_names}

    for volume in volumes:
        vol_name = volume.get('name', '')
        vol_node = volume.get('node', {}).get('name', '')

        for node_name in node_names:
            if f"{node_name}::" in vol_name or vol_node == node_name:
                node_volumes[node_name].append(volume)
                break

    return node_volumes


def get_availability_drop_value(volume: Dict) -> Optional[int]:
    """Extract current unavailableStateAvailabilityMultiplier from volume data."""
    caps = volume.get('storageCapabilities', {})
    protection = caps.get('protection', {})
    return protection.get('unavailableStateAvailabilityMultiplier')


def get_volume_oper_state(volume: Dict) -> Tuple[str, str]:
    """Extract operational state and reason from volume."""
    oper_state = volume.get('operState', 'UNKNOWN')
    oper_reason = volume.get('operStateReason', '')
    return oper_state, oper_reason


def availability_drop_label(value: Optional[int]) -> str:
    """Human-readable label for the multiplier value."""
    if value is None:
        return "not set"
    if value == AVAIL_DROP_ENABLED:
        return "enabled (0)"
    if value == AVAIL_DROP_DISABLED:
        return "disabled (1)"
    return f"unknown ({value})"


# ─── Modes ───────────────────────────────────────────────────────────────────

def do_check(client: HammerspaceClient, node_volumes: Dict[str, List[Dict]]):
    """Report current availability-drop status for volumes."""
    print("\n" + "=" * 80)
    print("AVAILABILITY-DROP STATUS CHECK")
    print("=" * 80)

    total = 0
    enabled_count = 0
    disabled_count = 0

    for node_name, volumes in sorted(node_volumes.items()):
        print(f"\n  Node: {node_name}")
        if not volumes:
            print("    (no volumes)")
            continue

        for vol in volumes:
            total += 1
            vol_name = vol.get('name', '')
            current = get_availability_drop_value(vol)
            oper_state, oper_reason = get_volume_oper_state(vol)

            if current == AVAIL_DROP_ENABLED:
                enabled_count += 1
                marker = "  <-- needs change for shutdown"
            else:
                disabled_count += 1
                marker = ""

            state_info = f"oper={oper_state}"
            if oper_reason:
                state_info += f" ({oper_reason})"

            print(f"    {vol_name}")
            print(f"      availability-drop: {availability_drop_label(current)}{marker}")
            print(f"      {state_info}")

    print(f"\n  Summary: {total} volumes — "
          f"{enabled_count} with drop enabled, {disabled_count} with drop disabled")

    if enabled_count > 0:
        print(f"\n  ACTION NEEDED: {enabled_count} volume(s) have availability-drop enabled.")
        print("  Run with --disable to set availability-drop-disabled before shutdown.")


def do_set(client: HammerspaceClient, node_volumes: Dict[str, List[Dict]],
           target_value: int, dry_run: bool = False, skip_confirm: bool = False):
    """Set availability-drop on volumes."""
    target_label = "disabled" if target_value == AVAIL_DROP_DISABLED else "enabled"

    # Collect volumes that need changing
    to_update = []
    already_set = []

    for node_name, volumes in sorted(node_volumes.items()):
        for vol in volumes:
            current = get_availability_drop_value(vol)
            if current == target_value:
                already_set.append(vol)
            else:
                to_update.append((node_name, vol))

    if not to_update:
        print(f"\nAll {len(already_set)} volume(s) already have "
              f"availability-drop {target_label}. Nothing to do.")
        return

    # Show plan
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Setting availability-drop to "
          f"{target_label} (multiplier={target_value})")
    print(f"  Volumes to update: {len(to_update)}")
    print(f"  Already correct:   {len(already_set)}")
    print()

    for node_name, vol in to_update:
        current = get_availability_drop_value(vol)
        print(f"  {vol.get('name', '')}")
        print(f"    {availability_drop_label(current)} -> {availability_drop_label(target_value)}")

    if dry_run:
        print(f"\n[DRY RUN] No changes made.")
        return

    # Confirmation
    if not skip_confirm:
        print(f"\nThis will update {len(to_update)} volume(s).")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)

    # Apply changes
    print(f"\n{'=' * 80}")
    print(f"APPLYING: availability-drop {target_label}")
    print("=" * 80)

    success = 0
    failed = 0

    for node_name, vol in to_update:
        vol_name = vol.get('name', '')
        vol_uuid = vol.get('uoid', {}).get('uuid', '')
        volume_id = vol_uuid if vol_uuid else urllib.parse.quote(vol_name, safe='')

        print(f"\n  Updating: {vol_name}")

        # GET fresh copy
        vol_data = client.get_volume(volume_id)
        if not vol_data:
            print(f"    FAILED: could not fetch volume")
            failed += 1
            continue

        # Update the multiplier
        if 'storageCapabilities' not in vol_data:
            vol_data['storageCapabilities'] = {}
        if 'protection' not in vol_data['storageCapabilities']:
            vol_data['storageCapabilities']['protection'] = {}

        vol_data['storageCapabilities']['protection']['unavailableStateAvailabilityMultiplier'] = target_value

        ok, error = client.update_volume(volume_id, vol_data)
        if ok:
            print(f"    OK: availability-drop {target_label}")
            success += 1
        else:
            print(f"    FAILED: {error}")
            failed += 1

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print("=" * 80)
    print(f"  Updated:         {success}")
    print(f"  Failed:          {failed}")
    print(f"  Already correct: {len(already_set)}")

    if failed > 0:
        sys.exit(1)


def do_health_check(client: HammerspaceClient, node_volumes: Dict[str, List[Dict]],
                    matched_nodes: List[Dict]):
    """Post-restart health check: verify node and volume operational state."""
    print("\n" + "=" * 80)
    print("HEALTH CHECK")
    print("=" * 80)

    issues = []

    # ── Node health ──────────────────────────────────────────────────────
    print("\n--- Node Status ---")
    for node in sorted(matched_nodes, key=lambda n: n.get('name', '')):
        name = node.get('name', '')
        admin_state = node.get('adminState', 'UNKNOWN')
        oper_state = node.get('operState', 'UNKNOWN')
        status_ok = (oper_state.upper() == 'UP' or oper_state.upper() == 'ONLINE')
        marker = "" if status_ok else "  <-- ISSUE"

        print(f"  {name:<40} admin={admin_state:<6} oper={oper_state}{marker}")

        if not status_ok:
            issues.append(f"Node '{name}' oper state: {oper_state}")

    # ── Volume health ────────────────────────────────────────────────────
    print("\n--- Volume Status ---")
    for node_name, volumes in sorted(node_volumes.items()):
        if not volumes:
            print(f"  Node: {node_name} — (no volumes)")
            continue

        for vol in volumes:
            vol_name = vol.get('name', '')
            vol_uuid = vol.get('uoid', {}).get('uuid', '')
            volume_id = vol_uuid if vol_uuid else urllib.parse.quote(vol_name, safe='')

            # Fetch fresh data
            fresh = client.get_volume(volume_id)
            if not fresh:
                print(f"  {vol_name:<55} FETCH FAILED")
                issues.append(f"Volume '{vol_name}': could not fetch")
                continue

            oper_state, oper_reason = get_volume_oper_state(fresh)
            avail_drop = get_availability_drop_value(fresh)

            state_ok = oper_state.upper() in ('ONLINE', 'UP', 'AVAILABLE')
            marker = "" if state_ok else "  <-- ISSUE"

            print(f"  {vol_name}")
            print(f"    oper={oper_state}  availability-drop={availability_drop_label(avail_drop)}{marker}")
            if oper_reason:
                print(f"    reason: {oper_reason}")

            if not state_ok:
                reason_str = f" ({oper_reason})" if oper_reason else ""
                issues.append(f"Volume '{vol_name}': oper state {oper_state}{reason_str}")

    # ── Uncleared events ─────────────────────────────────────────────────
    print("\n--- Uncleared Events ---")
    events = client.get_events(uncleared_only=True)

    # Filter events related to our nodes
    node_names = {n.get('name', '') for n in matched_nodes}
    relevant_events = []
    for event in events:
        event_str = json.dumps(event).lower()
        for name in node_names:
            if name.lower() in event_str:
                relevant_events.append(event)
                break

    if relevant_events:
        for event in relevant_events[:20]:
            severity = event.get('severity', 'UNKNOWN')
            message = event.get('message', event.get('description', 'N/A'))
            timestamp = event.get('timestamp', event.get('created', ''))
            print(f"  [{severity}] {timestamp} — {message}")
            issues.append(f"Event: [{severity}] {message}")
        if len(relevant_events) > 20:
            print(f"  ... and {len(relevant_events) - 20} more events")
    else:
        print("  No uncleared events related to these nodes.")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    if issues:
        print(f"HEALTH CHECK: {len(issues)} issue(s) found")
        print("=" * 80)
        for issue in issues:
            print(f"  - {issue}")
        print("\nRecommendation: Investigate the above issues in the Hammerspace GUI under")
        print("  Infrastructure > Storage Systems and Infrastructure > Volumes")
    else:
        print("HEALTH CHECK: ALL OK")
        print("=" * 80)
        print("  All nodes and volumes are operational. No uncleared events.")


def main():
    parser = argparse.ArgumentParser(
        description="Set availability-drop on Hammerspace volumes for node maintenance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workflow for planned shutdown (RMA):
  1. Check current state:   --check
  2. Disable avail-drop:    --disable --dry-run  (preview)
  3. Apply:                 --disable
  4. Shut down instances
  5. After restart:         --health-check
  6. Re-enable avail-drop:  --enable

CLI equivalents:
  --disable  =  hs volume set --availability-drop-disabled <volume>
  --enable   =  hs volume set --availability-drop-enabled <volume>

API field: storageCapabilities.protection.unavailableStateAvailabilityMultiplier
  0 = availability-drop enabled  (availability goes to 0 when volume is unavailable)
  1 = availability-drop disabled (availability stays unchanged when volume is unavailable)
        """
    )

    # Connection
    parser.add_argument('--host', required=True, help='Hammerspace Anvil IP or hostname')
    parser.add_argument('--user', required=True, help='API username')
    parser.add_argument('--password', required=True, help='API password')

    # Node filter (mutually exclusive)
    filter_group = parser.add_mutually_exclusive_group()
    filter_group.add_argument('--node', action='append', dest='nodes', metavar='NAME',
                              help='Specific node name (can repeat)')
    filter_group.add_argument('--prefix', help='Match nodes starting with prefix')
    filter_group.add_argument('--contains', help='Match nodes containing string')
    filter_group.add_argument('--pattern', help='Match nodes by regex')
    filter_group.add_argument('--all-nodes', action='store_true',
                              help='Apply to ALL nodes (use with caution)')

    # Action (mutually exclusive)
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument('--disable', action='store_true',
                              help='Set availability-drop DISABLED (for pre-shutdown)')
    action_group.add_argument('--enable', action='store_true',
                              help='Set availability-drop ENABLED (for post-maintenance)')
    action_group.add_argument('--check', action='store_true',
                              help='Check current availability-drop status')
    action_group.add_argument('--health-check', action='store_true',
                              help='Verify node/volume health after restart')

    # Options
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without applying')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip confirmation prompt')

    args = parser.parse_args()

    # Require a node filter (unless --all-nodes)
    if not any([args.nodes, args.prefix, args.contains, args.pattern, args.all_nodes]):
        parser.error("Must specify a node filter (--node, --prefix, --contains, --pattern, or --all-nodes)")

    # Connect
    print(f"Connecting to Hammerspace at {args.host}...")
    client = HammerspaceClient(args.host, args.user, args.password)

    # Fetch nodes
    try:
        all_nodes = client.get_all_nodes()
        print(f"  Found {len(all_nodes)} total nodes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching nodes: {e}")
        sys.exit(1)

    # Filter nodes
    matched_nodes = find_matching_nodes(
        all_nodes,
        prefix=args.prefix,
        contains=args.contains,
        pattern=args.pattern,
        node_names=args.nodes,
        all_nodes=args.all_nodes
    )

    if not matched_nodes:
        print("\nNo matching nodes found.")
        sys.exit(0)

    matched_names = [n.get('name', '') for n in matched_nodes]
    print(f"\nMatched {len(matched_nodes)} node(s):")
    for name in sorted(matched_names):
        print(f"  - {name}")

    # Fetch volumes
    try:
        all_volumes = client.get_all_storage_volumes()
        print(f"\n  Found {len(all_volumes)} total volumes")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching volumes: {e}")
        sys.exit(1)

    node_volumes = find_volumes_for_nodes(all_volumes, matched_names)

    total_vols = sum(len(v) for v in node_volumes.values())
    print(f"  {total_vols} volume(s) on matched nodes")

    if total_vols == 0 and not args.health_check:
        print("\nNo volumes found on the matched nodes. Nothing to do.")
        sys.exit(0)

    # Dispatch to mode
    if args.check:
        do_check(client, node_volumes)
    elif args.health_check:
        do_health_check(client, node_volumes, matched_nodes)
    elif args.disable:
        do_set(client, node_volumes, AVAIL_DROP_DISABLED, dry_run=args.dry_run, skip_confirm=args.yes)
    elif args.enable:
        do_set(client, node_volumes, AVAIL_DROP_ENABLED, dry_run=args.dry_run, skip_confirm=args.yes)


if __name__ == "__main__":
    main()
