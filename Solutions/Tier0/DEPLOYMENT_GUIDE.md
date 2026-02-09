# Hammerspace Tier 0 Deployment Guide for OCI

Step-by-step guide for deploying Hammerspace Tier 0 storage on Oracle Cloud Infrastructure (OCI) GPU instances.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Control Machine Setup](#2-control-machine-setup)
3. [OCI Authentication Setup](#3-oci-authentication-setup)
4. [Configure Inventory](#4-configure-inventory)
5. [Configure Variables](#5-configure-variables)
6. [Run Preflight Check](#6-run-preflight-check)
7. [Deploy to New Instances](#7-deploy-to-new-instances)
8. [Verify Deployment](#8-verify-deployment)
9. [Availability Zone (AZ) Configuration with GPU Fabric](#9-availability-zone-az-configuration-with-gpu-fabric)
10. [Adding New Instances (Future Deployments)](#10-adding-new-instances-future-deployments)
11. [Troubleshooting](#11-troubleshooting)
12. [Decommissioning Instances](#12-decommissioning-instances)

---

## 1. Prerequisites

Before starting, ensure you have:

| Requirement | Description |
|-------------|-------------|
| OCI Tenancy | Access to OCI with compute instances running |
| GPU Instances | BM.GPU.GB200-v3.4 or similar bare metal instances |
| SSH Access | SSH key configured for instance access |
| Hammerspace Cluster | Anvil management IP and admin credentials |
| Network | Instances can reach Hammerspace Anvil on port 8443 |

---

## 2. Control Machine Setup

Run these commands on your control machine (laptop, bastion host, or workstation).

### 2.1 Install Ansible

**macOS:**
```bash
brew install ansible
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install -y ansible python3-pip
```

**Linux (RHEL/Rocky):**
```bash
sudo dnf install -y ansible python3-pip
```

### 2.2 Install OCI Python SDK

```bash
pip3 install oci
```

### 2.3 Clone the Repository

```bash
git clone <repository-url> ansible-tier0
cd ansible-tier0
```

### 2.4 Install Ansible Collections

```bash
ansible-galaxy collection install -r requirements.yml
```

**Expected output:**
```
Installing 'ansible.posix:>=1.4.0' ...
Installing 'community.general:>=6.0.0' ...
Installing 'oracle.oci:>=5.0.0' ...
```

---

## 3. OCI Authentication Setup

### 3.1 Install OCI CLI (if not installed)

```bash
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"
```

### 3.2 Configure OCI Authentication

```bash
oci setup config
```

Follow the prompts:
1. Enter your OCI user OCID
2. Enter your tenancy OCID
3. Enter your region (e.g., `us-sanjose-1`)
4. Generate a new API key or use existing

**Verify configuration:**
```bash
oci iam user get --user-id <your-user-ocid>
```

### 3.3 Verify OCI Connectivity

```bash
# List compartments
oci iam compartment list --query "data[].{name:name, id:id}" --output table

# List instances in your compartment
oci compute instance list --compartment-id <your-compartment-ocid> --output table
```

---

## 4. Configure Inventory

### 4.1 Update ansible.cfg

Edit `ansible.cfg` to use OCI dynamic inventory and SSH key:

```ini
[defaults]
inventory = inventory.oci.yml
private_key_file = /path/to/your/ssh/key    # <-- Path to your SSH private key
```

**Important:** The SSH public key must be configured on all GPU instances. Ensure:

1. The SSH key pair exists on your control machine
2. The public key is added to the `~/.ssh/authorized_keys` file on each GPU instance
3. The key was added during instance provisioning, or add it manually:

```bash
# Option 1: If you have existing access, copy the key
ssh-copy-id -i /path/to/your/ssh/key.pub ubuntu@<instance-ip>

# Option 2: Add via OCI Console
# Navigate to: Compute > Instances > Instance Details > Console Connection
# Or add SSH key during instance creation

# Option 3: Add manually on each instance
echo "ssh-rsa AAAA...your-public-key... user@host" >> ~/.ssh/authorized_keys
```

**Verify SSH access:**
```bash
ssh -i /path/to/your/ssh/key ubuntu@<instance-ip> "hostname"
```

### 4.2 Configure OCI Inventory

Edit `inventory.oci.yml` to match your environment:

```yaml
---
plugin: oracle.oci.oci
regions:
  - us-sanjose-1    # <-- Update to your region

fetch_hosts_from_subcompartments: true

hostname_format_preferences:
  - "display_name"
  - "private_ip"

# Filter to running GPU instances
filters:
  lifecycle_state: "RUNNING"

include_host_filters:
  - "shape == 'BM.GPU.GB200-v3.4'"    # <-- Update to your instance shape

# Create storage_servers group
groups:
  storage_servers: "shape == 'BM.GPU.GB200-v3.4'"

compose:
  ansible_host: private_ip
  ansible_user: "'ubuntu'"              # <-- Update if using different OS user
  ansible_python_interpreter: "'/usr/bin/python3'"
  ansible_become: true
  oci_fault_domain: fault_domain
  oci_availability_domain: availability_domain
  hammerspace_volume_az_prefix: fault_domain | regex_replace('FAULT-DOMAIN-', 'AZ') ~ ":"

keyed_groups:
  - key: fault_domain
    prefix: az
    separator: "_"
```

### 4.3 Test Inventory Discovery

```bash
# List all discovered hosts
ansible-inventory -i inventory.oci.yml --list

# Show as graph
ansible-inventory -i inventory.oci.yml --graph

# Ping all storage servers
ansible -i inventory.oci.yml storage_servers -m ping
```

**Expected output:**
```
instance20260127011850 | SUCCESS => {
    "ping": "pong"
}
instance20260127011851 | SUCCESS => {
    "ping": "pong"
}
```

---

## 5. Configure Variables

### 5.1 Edit vars/main.yml

Update the following sections in `vars/main.yml`:

#### Hammerspace API Configuration (Required)

```yaml
# Anvil management IP
hammerspace_api_host: "10.241.0.105"    # <-- Update to your Anvil IP

# API credentials
hammerspace_api_user: "admin"
hammerspace_api_password: "your-password"    # <-- Update password

# Skip SSL validation (for self-signed certs)
hammerspace_api_validate_certs: false
```

#### NFS Export Configuration

```yaml
# Hammerspace node IPs (require no_root_squash)
hammerspace_nodes:
  - "10.241.0.105"    # <-- Anvil cluster IP

mover_nodes:
  - "10.241.0.10"     # <-- DI/Mover node IPs
  - "10.241.0.11"

# Client subnets (use root_squash)
client_subnets:
  - "10.200.104.0/24"
  - "10.200.105.0/24"
```

#### Storage Configuration (Usually No Changes Needed)

```yaml
# Dynamic NVMe discovery (recommended)
use_dynamic_discovery: true

# RAID level (0 for Tier 0)
raid_level: 0

# Mount point base path
mount_base_path: /hammerspace
```

---

## 6. Run Preflight Check

The preflight check compares your OCI inventory with Hammerspace to identify new instances that need deployment.

### 6.1 Run Preflight Check

```bash
ansible-playbook preflight_check.yml -i inventory.oci.yml
```

### 6.2 Review the Report

**Example output:**
```
================================================================================
PREFLIGHT CHECK REPORT
================================================================================
Hammerspace API: 10.241.0.105

SUMMARY
--------------------------------------------------------------------------------
Inventory hosts (storage_servers): 10
Hammerspace registered nodes:      7
Already registered:                7
New instances to deploy:           3

NEW INSTANCES (need deployment)
--------------------------------------------------------------------------------
- instance20260201011850
- instance20260201011851
- instance20260201011852

================================================================================
RECOMMENDED COMMANDS
================================================================================
# Deploy to new instances only:
ansible-playbook site.yml --limit "instance20260201011850,instance20260201011851,instance20260201011852"
================================================================================
```

### 6.3 Output Files

| File | Description |
|------|-------------|
| `.new_instances_limit` | List of new instance names for `--limit` |
| `preflight_report.txt` | Full report saved to disk |

---

## 7. Deploy to New Instances

### Option A: Using the Deployment Script (Recommended)

```bash
# Interactive mode - prompts for confirmation
./deploy_new_instances.sh -i inventory.oci.yml

# Dry run first (recommended)
./deploy_new_instances.sh -i inventory.oci.yml --check

# Auto mode (no confirmation)
./deploy_new_instances.sh -i inventory.oci.yml --auto
```

### Option B: Manual Commands

```bash
# Step 1: Dry run to verify changes
ansible-playbook site.yml -i inventory.oci.yml --limit @.new_instances_limit --check

# Step 2: Run precheck only
ansible-playbook site.yml -i inventory.oci.yml --limit @.new_instances_limit --tags precheck

# Step 3: Full deployment
ansible-playbook site.yml -i inventory.oci.yml --limit @.new_instances_limit
```

### Option C: Deploy to Specific Instances

```bash
# Single instance
ansible-playbook site.yml -i inventory.oci.yml --limit "instance20260201011850"

# Multiple instances
ansible-playbook site.yml -i inventory.oci.yml --limit "instance20260201011850,instance20260201011851"

# Pattern matching
ansible-playbook site.yml -i inventory.oci.yml --limit "instance202602*"
```

### Deployment Progress

The playbook will execute these roles in order:

| Step | Role | Description |
|------|------|-------------|
| 1 | `nvme_discovery` | Discover NVMe drives, group by NUMA |
| 2 | `precheck` | Validate drives, network, packages |
| 3 | `raid_setup` | Create mdadm RAID arrays |
| 4 | `filesystem_setup` | Create XFS filesystems |
| 5 | `nfs_setup` | Configure NFS server and exports |
| 6 | `firewall_setup` | Open NFS and RDMA ports |
| 7 | `hammerspace_integration` | Register node and volumes via API |

---

## 8. Verify Deployment

### 8.1 Verify on Target Instances

SSH to a deployed instance and check:

```bash
# Check RAID arrays
cat /proc/mdstat

# Check mounts
df -h | grep hammerspace

# Check NFS exports
exportfs -v

# Check NFS service
systemctl status nfs-server

# Test local mount
showmount -e localhost
```

### 8.2 Verify in Hammerspace

**Via Anvil CLI:**
```bash
anvil> node-list
anvil> volume-list
anvil> volume-list --node-name instance20260201011850
```

**Via API:**
```bash
# List all nodes
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/nodes | jq '.[].name'

# List volumes for a node
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/storage-volumes | jq '.[] | select(.nodeName | contains("instance20260201"))'
```

### 8.3 Run Verification Playbook

```bash
ansible-playbook verify_nfs.yml -i inventory.oci.yml --limit @.new_instances_limit
```

---

## 9. Availability Zone (AZ) Configuration with GPU Fabric

For multi-AZ deployments, Hammerspace uses AZ prefixes (e.g., `AZ1:`, `AZ2:`) to ensure data placement and redundancy across failure domains. On OCI GPU instances, the **GPU Memory Fabric** determines which instances share the same high-speed interconnect and should be grouped in the same AZ.

### 9.1 Understanding GPU Memory Fabric

| Concept | Description |
|---------|-------------|
| **GPU Memory Fabric** | OCI's high-bandwidth interconnect linking GPUs within a cluster |
| **Fabric OCID** | Unique identifier for each GPU fabric (e.g., `ocid1.computegpumemoryfabric.oc1...`) |
| **AZ Mapping** | Instances sharing the same GPU fabric OCID = Same AZ |

**Why GPU Fabric for AZ?**
- Instances on the same GPU fabric have ultra-low latency between them
- GPU fabric boundaries represent natural failure domains
- Distributing data across fabrics provides true redundancy

### 9.2 AZ Mapping Logic

```
GPU Fabric OCID                                    → AZ
─────────────────────────────────────────────────────────
ocid1.computegpumemoryfabric.oc1...aaaa (1st unique) → AZ1
ocid1.computegpumemoryfabric.oc1...bbbb (2nd unique) → AZ2
ocid1.computegpumemoryfabric.oc1...cccc (3rd unique) → AZ3
...
```

**Example:**
```
Instance              GPU Fabric (last 12 chars)    AZ
──────────────────────────────────────────────────────────
instance-001          ...slutj7sca                   AZ1
instance-002          ...slutj7sca                   AZ1  (same fabric)
instance-003          ...xk8m2pqrs                   AZ2
instance-004          ...xk8m2pqrs                   AZ2  (same fabric)
instance-005          ...abc123xyz                   AZ3
```

### 9.3 Collect GPU Fabric Data

**Step 1:** Run the GPU fabric collection playbook:
```bash
ansible-playbook collect_gpu_fabric.yml -i inventory.oci.yml
```

**Output:**
```
============================================
GPU FABRIC DATA COLLECTED
============================================
Output file: gpu_fabric_data.txt
Instances: 10
Unique GPU fabrics (AZs): 3
============================================
```

**Step 2:** Review the collected data:
```bash
cat gpu_fabric_data.txt
```

**Example output:**
```
# GPU Fabric Data - Generated by collect_gpu_fabric.yml
# Format: gpu_fabric_ocid instance_name private_ip
ocid1.computegpumemoryfabric.oc1.us-sanjose-1.anqwyl...slutj7sca instance20260127011850 10.241.36.58
ocid1.computegpumemoryfabric.oc1.us-sanjose-1.anqwyl...slutj7sca instance20260127011851 10.241.36.59
ocid1.computegpumemoryfabric.oc1.us-sanjose-1.anqwyl...xk8m2pqrs instance20260127011852 10.241.36.60
```

### 9.4 Assign AZ Prefixes to Volumes

After collecting GPU fabric data, assign AZ prefixes to Hammerspace volumes:

**Dry run (recommended first):**
```bash
python3 assign_az_to_volumes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'your-password' \
  --gpu-fabric-file gpu_fabric_data.txt \
  --dry-run
```

**Apply changes:**
```bash
python3 assign_az_to_volumes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'your-password' \
  --gpu-fabric-file gpu_fabric_data.txt
```

**Generate report only (CSV output):**
```bash
python3 assign_az_to_volumes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'your-password' \
  --gpu-fabric-file gpu_fabric_data.txt \
  --report-only
```

### 9.5 Alternative: Fault Domain-Based AZ

For non-GPU instances or simpler deployments, use OCI Fault Domains instead:

| OCI Fault Domain | Hammerspace AZ |
|------------------|----------------|
| FAULT-DOMAIN-1 | AZ1 |
| FAULT-DOMAIN-2 | AZ2 |
| FAULT-DOMAIN-3 | AZ3 |

This mapping is **automatic** when using the OCI dynamic inventory. The `hammerspace_volume_az_prefix` is set based on fault domain:

```yaml
# In inventory.oci.yml (already configured)
compose:
  hammerspace_volume_az_prefix: fault_domain | regex_replace('FAULT-DOMAIN-', 'AZ') ~ ":"
```

### 9.6 Verify AZ Assignment

**Check volume names in Hammerspace:**
```bash
# Via Anvil CLI
anvil> volume-list

# Via API
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/storage-volumes | \
  jq -r '.[] | "\(.name) -> \(.nodeName)"'
```

**Expected output with AZ prefixes:**
```
AZ1:instance20260127011850::/hammerspace/hsvol0
AZ1:instance20260127011851::/hammerspace/hsvol0
AZ2:instance20260127011852::/hammerspace/hsvol0
AZ3:instance20260127011853::/hammerspace/hsvol0
```

### 9.7 AZ Best Practices

| Recommendation | Description |
|----------------|-------------|
| **Minimum 4 AZs** | When data is stored only on Tier 0 |
| **6 AZs Recommended** | For optimal redundancy |
| **Symmetric Design** | Same number of nodes/volumes per AZ |
| **Re-run on New Instances** | Collect GPU fabric and assign AZ after adding instances |

---

## 10. Adding New Instances (Future Deployments)

When new GPU instances are added to OCI, follow these steps:

### 10.1 Quick Deployment

```bash
# One command to check and deploy new instances
./deploy_new_instances.sh -i inventory.oci.yml
```

### 10.2 Step-by-Step

```bash
# 1. Verify new instances are discovered
ansible-inventory -i inventory.oci.yml --graph

# 2. Run preflight check
ansible-playbook preflight_check.yml -i inventory.oci.yml

# 3. Review preflight_report.txt

# 4. Deploy to new instances
ansible-playbook site.yml -i inventory.oci.yml --limit @.new_instances_limit

# 5. Collect GPU fabric and assign AZ (if using GPU fabric-based AZ)
ansible-playbook collect_gpu_fabric.yml -i inventory.oci.yml
python3 assign_az_to_volumes.py --host <ANVIL_IP> --user admin --password 'xxx' \
  --gpu-fabric-file gpu_fabric_data.txt
```

### 10.3 Workflow Diagram

```
┌─────────────────────┐
│  New OCI Instances  │
│    Provisioned      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Run Preflight      │
│  ansible-playbook   │
│  preflight_check.yml│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Review Report      │
│  New vs Registered  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Deploy New Only    │
│  --limit @.new_     │
│  instances_limit    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Verify in          │
│  Hammerspace GUI    │
└─────────────────────┘
```

---

## 11. Troubleshooting

### OCI Inventory Issues

**Problem:** `The oci dynamic inventory plugin requires oci python sdk`
```bash
# Solution: Install OCI SDK
pip3 install oci
```

**Problem:** `Unable to parse inventory`
```bash
# Verify OCI config
oci iam user get --user-id $(grep user ~/.oci/config | cut -d= -f2)

# Test inventory directly
ansible-inventory -i inventory.oci.yml --list
```

**Problem:** No hosts discovered
```bash
# Check filters in inventory.oci.yml
# Verify instance shape matches your filter
oci compute instance list --compartment-id <ocid> --query "data[].shape" --output table
```

### SSH Connection Issues

**Problem:** `Permission denied (publickey)`
```bash
# Verify SSH key path in ansible.cfg
# Test SSH manually
ssh -i /path/to/key ubuntu@<instance-ip>
```

**Problem:** `Connection timed out`
```bash
# Check security lists allow SSH (port 22)
# Verify you're connecting via correct network (VPN, bastion, etc.)
```

### Hammerspace API Issues

**Problem:** `Failed to connect to Hammerspace API`
```bash
# Test API connectivity
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/nodes

# Check firewall allows port 8443
nc -zv 10.241.0.105 8443
```

**Problem:** `Node already exists`
```
# This is normal - the playbook skips existing nodes
# Check status: already registered vs newly added
```

### RAID/Storage Issues

**Problem:** `No NVMe drives found`
```bash
# SSH to instance and check
lsblk
ls /dev/nvme*
```

**Problem:** `Drive already in use`
```bash
# Check if drives are already mounted or in RAID
cat /proc/mdstat
mount | grep nvme
```

### Common Commands Reference

```bash
# Re-run specific roles
ansible-playbook site.yml -i inventory.oci.yml --tags precheck
ansible-playbook site.yml -i inventory.oci.yml --tags raid
ansible-playbook site.yml -i inventory.oci.yml --tags nfs
ansible-playbook site.yml -i inventory.oci.yml --tags hammerspace

# Skip specific roles
ansible-playbook site.yml -i inventory.oci.yml --skip-tags hammerspace

# Verbose output for debugging
ansible-playbook site.yml -i inventory.oci.yml -vvv

# Check mode (dry run)
ansible-playbook site.yml -i inventory.oci.yml --check
```

---

## 12. Decommissioning Instances

When terminating GPU instances, you should first remove them from Hammerspace to avoid orphaned nodes and volumes.

### 12.1 Using the Cleanup Script

The `cleanup_instance_nodes.py` script removes nodes and their volumes from Hammerspace.

**Step 1: Dry run (see what will be deleted)**
```bash
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'your-password' \
  --dry-run
```

**Example output:**
```
Connecting to Hammerspace at 10.241.0.105...
Fetching nodes...
  Found 15 total nodes

Found 10 nodes starting with 'instance':
  - instance20260127011850 (UUID: abc123...)
  - instance20260127011851 (UUID: def456...)
  ...

Fetching storage volumes...
  Found 20 total volumes

[DRY RUN] Will delete 20 volumes from 10 nodes:

  Node: instance20260127011850
    - Volume: AZ1:instance20260127011850::/hammerspace/hsvol0
    - Volume: AZ1:instance20260127011850::/hammerspace/hsvol1
  ...

[DRY RUN] No changes made.
```

**Step 2: Execute cleanup**
```bash
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'your-password'
```

**Step 3: Confirm deletion**
```
Type 'yes' to confirm deletion: yes

PHASE 1: Deleting volumes...
  ✓ Deleted: AZ1:instance20260127011850::/hammerspace/hsvol0
  ...

PHASE 2: Deleting nodes...
  ✓ Deleted: instance20260127011850
  ...

SUMMARY
Volumes: 20 deleted, 0 failed
Nodes:   10 deleted, 0 failed
```

### 12.2 Cleanup Options

| Option | Description |
|--------|-------------|
| `--host` | Hammerspace Anvil IP (required) |
| `--user` | API username (required) |
| `--password` | API password (required) |
| `--list-nodes` | List all nodes and exit (no deletion) |
| `--prefix` | Match nodes starting with prefix |
| `--contains` | Match nodes containing string |
| `--pattern` | Match nodes using regex pattern |
| `--node NAME` | Match specific node name (repeatable) |
| `--dry-run` | Show what would be deleted without deleting |
| `--yes`, `-y` | Skip confirmation prompt |

**List all nodes first:**
```bash
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'xxx' \
  --list-nodes
```

**Filter examples:**
```bash
# Delete nodes STARTING WITH "bu-test"
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'xxx' \
  --prefix "bu-test" \
  --dry-run

# Delete nodes CONTAINING "test"
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'xxx' \
  --contains "test" \
  --dry-run

# Delete nodes matching REGEX pattern
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'xxx' \
  --pattern "^bu-.*-01$" \
  --dry-run

# Delete SPECIFIC nodes by name
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'xxx' \
  --node bu-test-01 \
  --node bu-test-02 \
  --dry-run

# Skip confirmation (for automation)
python3 cleanup_instance_nodes.py \
  --host 10.241.0.105 \
  --user admin \
  --password 'xxx' \
  --contains "test" \
  --yes
```

### 12.3 Decommission Workflow

```
┌─────────────────────────┐
│  Identify instances     │
│  to decommission        │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Run cleanup script     │
│  with --dry-run         │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Review volumes/nodes   │
│  to be deleted          │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Run cleanup script     │
│  (confirm deletion)     │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Terminate OCI          │
│  instances              │
└─────────────────────────┘
```

### 12.4 Important Notes

| Warning | Description |
|---------|-------------|
| **Order matters** | Always remove from Hammerspace BEFORE terminating instances |
| **Data loss** | Deleting volumes removes data from Hammerspace metadata (NFS data on instance is lost when terminated) |
| **No undo** | Deletion is permanent - always use `--dry-run` first |
| **Prefix matching** | Script matches node names starting with prefix (case-insensitive) |

---

## Quick Reference Card

| Task | Command |
|------|---------|
| Test inventory | `ansible-inventory -i inventory.oci.yml --graph` |
| Ping all hosts | `ansible -i inventory.oci.yml storage_servers -m ping` |
| Preflight check | `ansible-playbook preflight_check.yml -i inventory.oci.yml` |
| Deploy new instances | `./deploy_new_instances.sh -i inventory.oci.yml` |
| Dry run | `ansible-playbook site.yml -i inventory.oci.yml --check` |
| Precheck only | `ansible-playbook site.yml -i inventory.oci.yml --tags precheck` |
| Full deploy | `ansible-playbook site.yml -i inventory.oci.yml` |
| Verify NFS | `ansible-playbook verify_nfs.yml -i inventory.oci.yml` |
| Collect GPU fabric | `ansible-playbook collect_gpu_fabric.yml -i inventory.oci.yml` |
| Assign AZ to volumes | `python3 assign_az_to_volumes.py --host <IP> --gpu-fabric-file gpu_fabric_data.txt` |
| List all nodes | `python3 cleanup_instance_nodes.py --host <IP> --user admin --password 'xxx' --list-nodes` |
| Cleanup (dry run) | `python3 cleanup_instance_nodes.py --host <IP> --user admin --password 'xxx' --contains "name" --dry-run` |
| Cleanup (execute) | `python3 cleanup_instance_nodes.py --host <IP> --user admin --password 'xxx' --contains "name"` |

---

## Support

For issues or questions:
- Check the main [README.md](README.md) for detailed configuration options
- Review `vars/main.yml` for all available settings
- Contact Hammerspace support for cluster-related issues
