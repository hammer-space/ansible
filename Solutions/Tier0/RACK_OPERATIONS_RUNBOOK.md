# Rack Operations Runbook — Hammerspace Tier 0

**Version:** 1.0
**Audience:** Customer Operations Teams
**Prerequisite:** Hammerspace Fundamentals Training, Ansible basics

---

## Table of Contents

1. [Overview](#1-overview)
2. [Tools & Scripts Reference](#2-tools--scripts-reference)
3. [Rack Bring-Down (Planned Maintenance)](#3-rack-bring-down-planned-maintenance)
4. [Rack Bring-Up (Return to Service)](#4-rack-bring-up-return-to-service)
5. [Volume Decommission (Permanent Removal)](#5-volume-decommission-permanent-removal)
6. [Node Removal (Permanent)](#6-node-removal-permanent)
7. [Node Re-Addition (After Rebuild / Replacement)](#7-node-re-addition-after-rebuild--replacement)
8. [Rolling Rack Maintenance (AZ-Aware)](#8-rolling-rack-maintenance-az-aware)
9. [Emergency: Unrecoverable Node Loss](#9-emergency-unrecoverable-node-loss)
10. [Manual Fallback Procedures](#10-manual-fallback-procedures)
11. [Verification & Health Checks](#11-verification--health-checks)
12. [Troubleshooting](#12-troubleshooting)
13. [Quick Reference Card](#13-quick-reference-card)

---

## 1. Overview

### The Golden Rule

> **Decommission before Deletion.** Never terminate an instance or power off a rack without first preparing Hammerspace. The node holds unique data mirrors — removing it without preparation causes unnecessary data movement and potential data loss.

### Operation Types

| Operation | Data Impact | Reversible | Use Case |
|-----------|-------------|------------|----------|
| **Bring-Down** (Maintenance) | None — data stays on disk | Yes | Kernel patch, firmware update, NVMe replacement |
| **Bring-Up** (Return) | None — node rejoins automatically | Yes | After planned maintenance |
| **Volume Decommission** | Data evacuated to other nodes | No | Permanent volume removal, capacity reduction |
| **Node Removal** | All volumes removed first | No | Permanent node retirement |
| **Re-Addition** | Node re-provisioned from scratch | N/A | Rack replacement, new hardware |

### Key Concepts

- **Availability Drop**: Controls how Hammerspace reacts when a node goes offline
  - **Enabled** (default, multiplier=0): Hammerspace immediately starts re-replicating data — appropriate for permanent failures
  - **Disabled** (multiplier=1): Hammerspace waits for the node to return — appropriate for planned maintenance
- **Max Suspected Time (MST)**: How long Hammerspace waits before marking a volume as "Unavailable" (default: 0 = system default). Set this higher than your reboot cycle time + 5 minute buffer.

---

## 2. Tools & Scripts Reference

All scripts are in the Ansible-Tier0 project root.

> **Credential setup (all scripts):** Use `--password-file` (recommended), `HAMMERSPACE_PASSWORD` env var, or interactive prompt. The `--password` flag still works for backward compatibility.
> ```bash
> echo '<PASSWORD>' > ~/.hs_password && chmod 600 ~/.hs_password
> ```

### set_availability_drop.py

Controls maintenance mode for nodes/volumes.

```
python3 set_availability_drop.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password [OPTIONS]

Modes:
  --check          Show current availability-drop status
  --disable        Pre-shutdown: keep volumes registered while offline
  --enable         Post-maintenance: restore normal availability behavior
  --health-check   Verify node and volume health after restart

Node Filters:
  --node NAME      Specific node (repeatable)
  --prefix PREFIX  Nodes starting with prefix
  --contains STR   Nodes containing string
  --pattern REGEX  Regex match
  --all-nodes      All nodes (use with extreme caution)

Safety:
  --dry-run        Preview changes without applying
```

### cleanup_instance_nodes.py

Permanently removes volumes and nodes from Hammerspace.

```
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password [OPTIONS]

Node Filters:
  --node NAME      Specific node (repeatable)
  --prefix PREFIX  Nodes starting with prefix
  --contains STR   Nodes containing string
  --pattern REGEX  Regex match
  --list-nodes     List all nodes and exit

Options:
  --parallel N     Delete N volumes concurrently (default: 1)
  --dry-run        Preview changes without deleting
  --yes / -y       Skip confirmation prompt
```

### Ansible Playbooks

| Playbook | Purpose |
|----------|---------|
| `site.yml` | Full provisioning (OCI / general) |
| `site-onprem-nvme.yml` | On-prem NVMe provisioning (no firewall) |

---

## 3. Rack Bring-Down (Planned Maintenance)

**Use when:** Kernel patch, firmware update, NVMe swap, planned reboot — any scenario where the node will return with its NVMe data intact.

### Step 1: Pre-Flight Check

Verify current state of the nodes in the rack.

```bash
# Check current status and availability-drop setting
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --check
```

Expected output: All volumes show `availability-drop: enabled` (normal state).

Also verify from Hammerspace CLI:

```
anvil> node-list
# Confirm all target nodes show "Healthy" state
```

### Step 2: Disable Availability Drop

This tells Hammerspace "these nodes are going offline temporarily — do NOT start re-replicating data."

```bash
# Dry run first — always
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --disable --dry-run
```

Review the output, then execute:

```bash
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --disable
```

### Step 3: Verify Availability Drop is Disabled

```bash
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --check
```

Expected: All volumes show `availability-drop: disabled`.

### Step 4: Perform Maintenance

You can now safely:
- Reboot the nodes
- Apply kernel patches
- Swap NVMe drives (if rebuilding RAID, see [Re-Addition](#7-node-re-addition-after-rebuild--replacement))
- Apply firmware updates

```bash
# Example: reboot nodes
ssh root@cri22cn409 'reboot'
ssh root@cri22cn410 'reboot'
```

### Step 5: Continue to [Rack Bring-Up](#4-rack-bring-up-return-to-service)

---

## 4. Rack Bring-Up (Return to Service)

**Use when:** Nodes are back online after planned maintenance.

### Step 1: Verify Nodes Are Online

```bash
# SSH connectivity check
ssh root@cri22cn409 'hostname && uptime'
ssh root@cri22cn410 'hostname && uptime'

# Verify NFS is running
ssh root@cri22cn409 'systemctl status nfs-server'

# Verify mounts
ssh root@cri22cn409 'df -h | grep hammerspace'

# Verify RAID health (if software RAID)
ssh root@cri22cn409 'cat /proc/mdstat'
```

### Step 2: Health Check via Hammerspace API

```bash
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --health-check
```

Expected: Nodes show "Online"/"Healthy", volumes show "Available".

If volumes show "Suspected" — wait a few minutes for Hammerspace to detect the node is back. Re-run the health check.

### Step 3: Re-Enable Availability Drop

Once health checks pass, restore normal availability behavior:

```bash
# Dry run
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --enable --dry-run

# Execute
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --enable
```

### Step 4: Final Verification

```bash
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --check
```

Expected: `availability-drop: enabled` (normal state restored).

Verify from Hammerspace CLI:

```
anvil> node-list
# All nodes "Healthy"

anvil> volume-list
# All volumes "Available"
```

---

## 5. Volume Decommission (Permanent Removal)

**Use when:** Permanently removing volumes from a node (e.g., reducing capacity, retiring specific drives).

> **WARNING:** This is irreversible. Hammerspace will evacuate data from these volumes to other nodes in the cluster. Ensure sufficient capacity exists elsewhere.

### Step 1: Pre-Flight — Check Cluster Capacity

Before decommissioning, ensure the remaining cluster has enough capacity to absorb the data:

```
anvil> volume-list
# Note the used capacity on target volumes

anvil> cluster-show
# Verify remaining free capacity > data on target volumes
```

### Step 2: Identify Volumes to Remove

```bash
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 \
  --list-nodes
```

This lists all volumes associated with the node.

### Step 3: Dry Run

```bash
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 \
  --dry-run
```

Review carefully — this shows exactly which volumes and nodes will be deleted.

### Step 4: Execute Volume Deletion

```bash
# Sequential (safest)
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409

# Parallel (faster for many volumes)
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 \
  --parallel 5
```

The script:
1. **Phase 1:** Deletes all volumes (waits for each to fully complete)
2. **Phase 2:** Deletes the node only if all volumes succeeded
3. Safety: If any volume fails, the node is NOT deleted

### Step 5: Monitor Data Evacuation

After volume deletion, Hammerspace re-replicates data to maintain durability:

```
anvil> volume-list
# Verify target volumes are gone

# Monitor rebalancing progress
anvil> task-list
# Watch for mobility/replication tasks
```

---

## 6. Node Removal (Permanent)

**Use when:** Permanently retiring a node from the Hammerspace cluster.

This is the same workflow as [Volume Decommission](#5-volume-decommission-permanent-removal) — the `cleanup_instance_nodes.py` script handles both volumes and node removal in sequence.

### Complete Workflow

```bash
# 1. List what will be affected
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --dry-run

# 2. Execute (with confirmation prompt)
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --parallel 5

# 3. Verify removal
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --list-nodes
# Confirm target nodes no longer appear
```

### Bulk Removal by Pattern

```bash
# Remove all nodes matching a rack prefix
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --prefix "cri22cn4" \
  --dry-run

# Remove all nodes containing "rack3"
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --contains "rack3" \
  --parallel 10
```

---

## 7. Node Re-Addition (After Rebuild / Replacement)

**Use when:** A node has been rebuilt (new OS, new RAID), re-racked, or replaced with new hardware.

### Prerequisites

- Node is accessible via SSH
- Network connectivity confirmed
- Node is NOT currently registered in Hammerspace (if it is, [remove it first](#6-node-removal-permanent))

### Step 1: Verify Node State

```bash
ssh root@<NODE_IP> 'hostname && lsblk && df -h'
```

### Step 2: Update Inventory

Ensure the node is in `inventory.yml`:

```yaml
storage_servers:
  hosts:
    cri22cn409:
      ansible_host: 10.50.47.89
      ansible_user: root
```

### Step 3: Run Full Provisioning (Single Node)

**On-Prem NVMe (no firewall):**

```bash
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit cri22cn409
```

**General (OCI / with firewall):**

```bash
ansible-playbook site.yml -i inventory.yml --limit cri22cn409
```

This runs the full stack:
1. **Discovery** — detects NVMe drives, excludes boot device, groups by NUMA
2. **Precheck** — validates hardware, drive count, sector sizes
3. **RAID Setup** — creates software RAID arrays (mdadm)
4. **Filesystem** — formats XFS, creates mount points, sets up fstab
5. **NFS Setup** — configures exports, sunrpc tuning, thread count
6. **Hammerspace Integration** — registers node and volumes via API

### Step 4: Verify Registration

```
anvil> node-list
# Confirm new node appears as "Healthy"

anvil> volume-list
# Confirm volumes are registered
```

```bash
# From another node, verify NFS exports
showmount -e <NODE_IP>
```

### Step 5: (Optional) Assign AZ and Volume Group

```bash
# Assign AZ label
python3 assign_az_to_volumes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --gpu-fabric-file gpu_fabric_data.txt

# Add to volume group
python3 add_volumes_to_group.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --group Tier0 \
  --instances-file tier0_instances_limit
```

---

## 8. Rolling Rack Maintenance (AZ-Aware)

**Use when:** Patching or rebooting an entire rack while maintaining data availability.

> **Critical:** Never bring down nodes from multiple AZs simultaneously. This can violate durability policies and risk data loss.

### Planning

1. Identify which AZ each node belongs to:

```
anvil> node-list
# Note the AZ prefix on each node
```

2. Group nodes by AZ. Process one AZ at a time.

3. Verify durability can tolerate the loss of one AZ:

```
# Check file instance distribution
anvil> volume-list
# Ensure no files have all copies in a single AZ
```

### Execution Per AZ

For each AZ batch:

```bash
# === AZ1 Batch ===

# 1. Disable availability-drop for AZ1 nodes
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --disable

# 2. Perform maintenance on AZ1 nodes
ssh root@cri22cn409 'yum update -y && reboot'
ssh root@cri22cn410 'yum update -y && reboot'

# 3. Wait for nodes to come back (monitor via ping or SSH)

# 4. Health check AZ1
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --health-check

# 5. Re-enable availability-drop for AZ1
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node cri22cn409 --node cri22cn410 \
  --enable

# 6. Verify cluster is healthy before proceeding to AZ2
anvil> node-list
# ALL nodes should be "Healthy" before proceeding

# === Repeat for AZ2, AZ3, etc. ===
```

### Critical Safety Check Between AZs

Before proceeding to the next AZ, verify:

```
anvil> node-list    # All nodes healthy
anvil> volume-list  # All volumes available
anvil> task-list    # No pending mobility tasks
```

---

## 9. Emergency: Unrecoverable Node Loss

**Use when:** Hardware failure, unrecoverable NVMe loss, or OCI host error where the node cannot be brought back with its data intact.

### Step 1: Assess the Damage

```
anvil> node-list
# Identify nodes in "Suspected" or "Unavailable" state
```

### Step 2: Wait for Max Suspected Time

Hammerspace will wait MST before marking volumes as "Unavailable." If the node might come back, wait.

### Step 3: Force Remove (Only if Confirmed Unrecoverable)

```bash
# Dry run — verify what will be cleaned up
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node <DEAD_NODE_NAME> \
  --dry-run

# Execute force cleanup
python3 cleanup_instance_nodes.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node <DEAD_NODE_NAME> \
  --parallel 5
```

### Step 4: Monitor Re-Replication

After removal, Hammerspace automatically re-replicates data from remaining copies:

```
anvil> task-list
# Monitor replication tasks until complete
```

### Step 5: (If replacing) Provision New Node

Follow [Node Re-Addition](#7-node-re-addition-after-rebuild--replacement).

---

## 10. Manual Fallback Procedures

If the Python scripts are unavailable or the Ansible control node is down.

### Manual Availability-Drop via Hammerspace CLI

```bash
# Check current setting
anvil> volume-list
# Look for availability-drop setting per volume

# Disable availability-drop (pre-maintenance)
anvil> volume-update --name "<VOLUME_NAME>" --availability-drop-disabled

# Enable availability-drop (post-maintenance)
anvil> volume-update --name "<VOLUME_NAME>" --availability-drop-enabled
```

### Manual Volume Decommission via CLI

```bash
# List volumes for a node
anvil> volume-list

# Remove each volume
anvil> volume-decommission --name "<VOLUME_NAME>"
# Wait for decommission to complete
anvil> volume-remove --name "<VOLUME_NAME>"
```

### Manual Node Removal via CLI

```bash
# Only after all volumes are removed
anvil> node-remove --name "<NODE_NAME>"
```

### Manual Node Re-Addition via CLI

```bash
# Register the node
anvil> node-add --type OTHER --name <NODE_NAME> --ip <NODE_IP>

# Add each volume
anvil> volume-add \
  --name "<NODE_NAME>::/mnt/hsvol0/Hammerspace/hsvol0" \
  --node-name "<NODE_NAME>" \
  --logical-volume-name "/mnt/hsvol0/Hammerspace/hsvol0"
```

---

## 11. Verification & Health Checks

### Post-Operation Checklist

Run after every rack operation:

| Check | Command | Expected |
|-------|---------|----------|
| Node state | `anvil> node-list` | All nodes "Healthy" |
| Volume state | `anvil> volume-list` | All volumes "Available" |
| NFS exports | `showmount -e <NODE_IP>` | Exports listed |
| RAID health | `ssh <NODE> 'cat /proc/mdstat'` | All arrays active, no degraded |
| Mount points | `ssh <NODE> 'df -h \| grep hammerspace'` | All mounts present |
| NFS service | `ssh <NODE> 'systemctl status nfs-server'` | Active (running) |
| Task queue | `anvil> task-list` | No stuck/failed tasks |

### API Health Check

```bash
python3 set_availability_drop.py \
  --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --all-nodes \
  --health-check
```

---

## 12. Troubleshooting

### Node Stays "Suspected" After Restart

**Cause:** NFS service not running, or firewall blocking Hammerspace communication.

```bash
ssh root@<NODE> 'systemctl restart nfs-server'
ssh root@<NODE> 'showmount -e localhost'
```

If NFS is running but node is still suspected, check network connectivity between the node and the Anvil.

### Volume Stuck in "Executing" During Deletion

**Cause:** Large data evacuation in progress.

Wait for it to complete. Monitor:

```
anvil> task-list
```

If stuck for > 1 hour with no progress, contact Hammerspace support.

### Availability-Drop Script Fails with SSL Error

**Cause:** Python 3.12+ SSL compatibility issue.

The script uses `requests` library which handles SSL differently. Ensure:

```bash
pip3 install --upgrade requests urllib3
```

Or use the `--insecure` flag if available.

### RAID Degraded After NVMe Replacement

```bash
# Check RAID status
cat /proc/mdstat

# If a drive was replaced, re-add it
mdadm --manage /dev/md0 --add /dev/nvme1n1

# Monitor rebuild
watch cat /proc/mdstat
```

### Ansible Fails During Re-Addition

If `site-onprem-nvme.yml` fails during re-provisioning:

```bash
# Run with verbose output
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit <NODE> -vvv

# Run specific phase only
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit <NODE> --tags discovery,precheck
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit <NODE> --tags raid,filesystem
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit <NODE> --tags nfs
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit <NODE> --tags hammerspace
```

---

## 13. Quick Reference Card

### Planned Maintenance (Node Returns With Data)

```bash
# PRE-SHUTDOWN
python3 set_availability_drop.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <NODE> --check
python3 set_availability_drop.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <NODE> --disable --dry-run
python3 set_availability_drop.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <NODE> --disable

# MAINTENANCE (reboot, patch, etc.)

# POST-RESTART
python3 set_availability_drop.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <NODE> --health-check
python3 set_availability_drop.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <NODE> --enable
```

### Permanent Removal (Node Retired)

```bash
python3 cleanup_instance_nodes.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <NODE> --dry-run
python3 cleanup_instance_nodes.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <NODE> --parallel 5
```

### New Node / Replacement

```bash
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit <NODE>
```

### Emergency Force Remove (Dead Node)

```bash
python3 cleanup_instance_nodes.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <DEAD_NODE> --parallel 5
# Then re-provision replacement if needed
ansible-playbook site-onprem-nvme.yml -i inventory.yml --limit <NEW_NODE>
```
