# Hammerspace Tier 0 Deployment Guide - Darksite / HDD Environments

Step-by-step guide for deploying Hammerspace Tier 0 storage on air-gapped (darksite) bare-metal servers with HDD or hardware RAID storage.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Offline Package Preparation](#3-offline-package-preparation)
4. [Control Machine Setup](#4-control-machine-setup)
5. [Configure Static Inventory](#5-configure-static-inventory)
6. [Configure Variables - HDD with Software RAID](#6-configure-variables---hdd-with-software-raid)
7. [Configure Variables - Hardware RAID](#7-configure-variables---hardware-raid)
8. [Transfer Ansible Bundle to Darksite](#8-transfer-ansible-bundle-to-darksite)
9. [Run Preflight Check](#9-run-preflight-check)
10. [Deploy](#10-deploy)
11. [Verify Deployment](#11-verify-deployment)
12. [Adding New Nodes](#12-adding-new-nodes)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Overview

This guide covers deployments where:

| Characteristic | Description |
|----------------|-------------|
| **Network** | Air-gapped / no internet access (darksite) |
| **Storage** | HDD, SSD, or hardware RAID (not NVMe) |
| **Platform** | Bare-metal on-premises servers |
| **Inventory** | Static inventory file (no cloud dynamic inventory) |
| **OS** | RHEL 8/9, Rocky Linux 8/9, or Ubuntu 22.04/24.04 |

### Key Differences from Cloud/NVMe Deployments

| Feature | Cloud / NVMe | Darksite / HDD |
|---------|-------------|----------------|
| Inventory | OCI/AWS/GCP dynamic | Static `inventory.yml` |
| Storage | NVMe (`/dev/nvme*n1`) | HDD/SSD (`/dev/sd*`) or HW RAID |
| RAID | Software RAID (mdadm) | Software or Hardware RAID |
| Package install | Online repos | Offline RPM/DEB bundles |
| AZ assignment | GPU fabric / fault domain | Manual or rack-based |

---

## 2. Prerequisites

| Requirement | Description |
|-------------|-------------|
| Bare-metal servers | Physical servers with HDD/SSD storage |
| SSH access | SSH key or password auth to all target nodes |
| Hammerspace cluster | Anvil management IP and admin credentials |
| Network | Nodes can reach Hammerspace Anvil on port 8443 |
| OS installed | RHEL/Rocky 8-9 or Ubuntu 22.04+ |
| Root/sudo access | Ansible requires `become: true` |

### Storage Requirements

Identify your storage configuration:

**Option A: Software RAID (mdadm)**
- Multiple HDDs/SSDs visible as `/dev/sda`, `/dev/sdb`, etc.
- Ansible will create mdadm RAID arrays
- Set `storage_type: "hdd"` or `"ssd"` or `"scsi"`

**Option B: Hardware RAID Controller**
- RAID controller (MegaRAID, HP Smart Array, Adaptec, etc.)
- Logical volumes already created by HW RAID controller
- Presented as `/dev/sda`, `/dev/sdb`, `/dev/md126`, `/dev/cciss/c0d0`, etc.
- Ansible will skip mdadm and use HW RAID devices directly
- Set `use_raid: false` and define `hw_raid_devices`

**Identify your HW RAID devices:**
```bash
# List all block devices
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL

# Check for HW RAID controllers
lspci | grep -i raid

# MegaRAID (LSI/Broadcom)
megacli -LDInfo -Lall -aAll 2>/dev/null || storcli /c0/vall show 2>/dev/null

# HP Smart Array
ssacli ctrl all show config 2>/dev/null

# Check mdadm for existing arrays
cat /proc/mdstat 2>/dev/null
```

---

## 3. Offline Package Preparation

On an **internet-connected machine** with the same OS version, download required packages.

### 3.1 RHEL/Rocky Linux

```bash
# Create package download directory
mkdir -p /tmp/tier0-packages

# Download packages (does NOT install, just downloads)
# For software RAID deployments:
dnf download --resolve --destdir /tmp/tier0-packages \
  mdadm xfsprogs parted nfs-utils smartmontools hdparm

# For hardware RAID deployments (no mdadm needed):
dnf download --resolve --destdir /tmp/tier0-packages \
  xfsprogs parted nfs-utils

# Tar up the packages
tar czf tier0-packages-rhel.tar.gz -C /tmp tier0-packages/
```

### 3.2 Ubuntu/Debian

```bash
# Create package download directory
mkdir -p /tmp/tier0-packages

# Download packages
cd /tmp/tier0-packages

# For software RAID deployments:
apt-get download mdadm xfsprogs parted nfs-kernel-server \
  smartmontools hdparm

# Resolve dependencies
apt-get download $(apt-cache depends --recurse --no-recommends --no-suggests \
  --no-conflicts --no-breaks --no-replaces --no-enhances \
  mdadm xfsprogs parted nfs-kernel-server 2>/dev/null | grep "^\w" | sort -u)

# For hardware RAID deployments:
apt-get download xfsprogs parted nfs-kernel-server

# Tar up
tar czf tier0-packages-ubuntu.tar.gz -C /tmp tier0-packages/
```

### 3.3 Ansible Collections (for control machine)

```bash
# Download collections for offline install
ansible-galaxy collection download ansible.posix -p /tmp/ansible-collections/
ansible-galaxy collection download community.general -p /tmp/ansible-collections/

# Tar up
tar czf ansible-collections-offline.tar.gz -C /tmp ansible-collections/
```

---

## 4. Control Machine Setup

The control machine (bastion/jump host) must be inside the darksite network.

### 4.1 Install Ansible

If the control machine has no internet:
```bash
# Option 1: Install from offline RPM (RHEL/Rocky)
# Transfer ansible RPM to the control machine
rpm -ivh ansible-core-*.rpm

# Option 2: Install from pip wheel
pip3 install --no-index --find-links=/path/to/wheels/ ansible-core
```

If the control machine has internet:
```bash
# RHEL/Rocky
sudo dnf install -y ansible-core

# Ubuntu
sudo apt install -y ansible
```

### 4.2 Install Ansible Collections (Offline)

```bash
# Transfer ansible-collections-offline.tar.gz to the control machine
tar xzf ansible-collections-offline.tar.gz
ansible-galaxy collection install -r requirements.yml -p ./collections/ --offline
```

Or install from tarballs:
```bash
ansible-galaxy collection install /path/to/ansible-posix-*.tar.gz
ansible-galaxy collection install /path/to/community-general-*.tar.gz
```

### 4.3 Clone/Copy the Repository

```bash
# Copy the ansible-tier0 directory to the control machine
# (via USB, SCP from transfer host, etc.)
scp -r ansible-tier0/ bastion:/home/admin/
cd /home/admin/ansible-tier0
```

---

## 5. Configure Static Inventory

Create `inventory.yml` with your bare-metal servers:

### 5.1 Basic Static Inventory

```yaml
---
all:
  children:
    storage_servers:
      hosts:
        tier0-node-01:
          ansible_host: 192.168.1.101
        tier0-node-02:
          ansible_host: 192.168.1.102
        tier0-node-03:
          ansible_host: 192.168.1.103
        tier0-node-04:
          ansible_host: 192.168.1.104

      vars:
        ansible_user: root
        ansible_ssh_private_key_file: /home/admin/.ssh/id_rsa
        ansible_python_interpreter: /usr/bin/python3
        ansible_become: true
        ansible_become_method: sudo
```

### 5.2 Inventory with AZ Groups (Multi-Rack)

```yaml
---
all:
  children:
    storage_servers:
      children:
        rack_a:
          hosts:
            tier0-rack-a-01:
              ansible_host: 192.168.1.101
              hammerspace_volume_az_prefix: "AZ1:"
            tier0-rack-a-02:
              ansible_host: 192.168.1.102
              hammerspace_volume_az_prefix: "AZ1:"
        rack_b:
          hosts:
            tier0-rack-b-01:
              ansible_host: 192.168.2.101
              hammerspace_volume_az_prefix: "AZ2:"
            tier0-rack-b-02:
              ansible_host: 192.168.2.102
              hammerspace_volume_az_prefix: "AZ2:"
        rack_c:
          hosts:
            tier0-rack-c-01:
              ansible_host: 192.168.3.101
              hammerspace_volume_az_prefix: "AZ3:"

      vars:
        ansible_user: root
        ansible_ssh_private_key_file: /home/admin/.ssh/id_rsa
        ansible_python_interpreter: /usr/bin/python3
        ansible_become: true
```

### 5.3 Update ansible.cfg

```ini
[defaults]
inventory = inventory.yml
private_key_file = /home/admin/.ssh/id_rsa
host_key_checking = False
timeout = 30

[privilege_escalation]
become = True
become_method = sudo
```

### 5.4 Test Connectivity

```bash
# Ping all storage servers
ansible storage_servers -m ping

# Check OS info
ansible storage_servers -m setup -a "filter=ansible_distribution*"
```

---

## 6. Configure Variables - HDD with Software RAID

Edit `vars/main.yml` for HDD environments using software RAID (mdadm):

```yaml
# ============================================================================
# Storage Type - HDD
# ============================================================================
storage_type: "hdd"          # Discover /dev/sd* devices (rotational=1)
# storage_type: "ssd"        # Discover /dev/sd* devices (rotational=0)
# storage_type: "scsi"       # All /dev/sd* devices (HDD + SSD)

# ============================================================================
# RAID Configuration - Software RAID (mdadm)
# ============================================================================
use_raid: true
use_dynamic_discovery: true
raid_level: 0                 # 0=stripe, 1=mirror, 5=parity, 10=stripe+mirror
mount_base_path: /hammerspace

# RAID grouping: numa, single, or per_drive
raid_grouping_strategy: numa

# For HDD, you may want larger arrays
raid_max_drives_per_array: 0  # 0=all drives per NUMA node
raid_min_drives_per_array: 2

# ============================================================================
# SCSI/SATA Device Exclusion
# ============================================================================
# Boot device is always excluded automatically

# Exclude specific devices by name
scsi_exclude_devices: []
  # - sda              # Example: exclude first disk

# Exclude by device path
scsi_exclude_paths: []
  # - /dev/sda

# Exclude by serial number
scsi_exclude_serials: []

# Exclude by model name
scsi_exclude_models: []
  # - "VIRTUAL-DISK"  # Example: exclude virtual disks

# ============================================================================
# Hammerspace API Configuration
# ============================================================================
hammerspace_api_host: "10.0.10.15"         # Anvil management IP
hammerspace_api_user: "admin"
hammerspace_api_password: "your-password"
hammerspace_api_validate_certs: false

# ============================================================================
# NFS Configuration
# ============================================================================
nfs_threads: 128

hammerspace_nodes:
  - "10.0.10.15"               # Anvil cluster IP

mover_nodes:
  - "10.0.12.242"              # DI/Mover node IPs

client_subnets:
  - "192.168.0.0/16"           # Client access subnet

# ============================================================================
# Network Settings
# ============================================================================
expected_mtu: 9000
network_test_targets:
  - "10.0.10.15"               # Anvil IP for MTU test

# ============================================================================
# Safety Settings
# ============================================================================
flush_iptables: true
skip_confirmation: false
force_raid_recreate: false
force_fs_recreate: false
fail_on_drives_in_use: false   # Set false for re-runs
```

---

## 7. Configure Variables - Hardware RAID

Edit `vars/main.yml` for environments with a hardware RAID controller:

```yaml
# ============================================================================
# Storage Type
# ============================================================================
# storage_type is not critical for HW RAID since discovery is skipped,
# but set it to match your underlying media for proper precheck behavior
storage_type: "hdd"

# ============================================================================
# Hardware RAID Configuration
# ============================================================================
# Skip software RAID (mdadm) - use HW RAID logical volumes directly
use_raid: false
use_dynamic_discovery: true

# Define the HW RAID logical volumes presented to the OS
# These are the block devices created by your RAID controller
hw_raid_devices:
  - /dev/sda                   # HW RAID logical volume 1
  - /dev/sdb                   # HW RAID logical volume 2
  # - /dev/md126               # Some HW RAID controllers use /dev/md*
  # - /dev/cciss/c0d0          # HP Smart Array
  # - /dev/dm-0                # Device mapper

# Mount base path (volumes mount as /hammerspace/hsvol0, hsvol1, ...)
mount_base_path: /hammerspace

# ============================================================================
# Hammerspace API Configuration
# ============================================================================
hammerspace_api_host: "10.0.10.15"
hammerspace_api_user: "admin"
hammerspace_api_password: "your-password"
hammerspace_api_validate_certs: false

# ============================================================================
# NFS Configuration
# ============================================================================
nfs_threads: 128

hammerspace_nodes:
  - "10.0.10.15"

mover_nodes:
  - "10.0.12.242"

client_subnets:
  - "192.168.0.0/16"

# ============================================================================
# Network Settings
# ============================================================================
expected_mtu: 9000
network_test_targets:
  - "10.0.10.15"

# ============================================================================
# Safety Settings
# ============================================================================
flush_iptables: true
skip_confirmation: false
force_fs_recreate: false
```

### Identifying HW RAID Devices

Run these commands on a target node to identify the correct block devices:

```bash
# List all block devices with size and type
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL,ROTA

# Example output for HW RAID:
# NAME    SIZE  TYPE MOUNTPOINT MODEL              ROTA
# sda     1.8T  disk            LOGICAL VOLUME      0
# sdb     1.8T  disk            LOGICAL VOLUME      0
# sdc     447G  disk /          INTEL SSDSC2KB480G8  0  <- boot (auto-excluded)

# Check if device is boot device
findmnt -n -o SOURCE /

# Verify HW RAID controller info
lspci | grep -i -E "raid|megaraid|smart array|adaptec"

# MegaRAID: Show logical drives
storcli /c0/vall show
# or
megacli -LDInfo -Lall -aAll

# HP Smart Array: Show logical drives
ssacli ctrl all show config
```

---

## 8. Transfer Ansible Bundle to Darksite

### 8.1 Package Everything

On the internet-connected preparation machine:

```bash
# Create the transfer bundle
mkdir -p /tmp/tier0-bundle

# Copy Ansible project
cp -r ansible-tier0/ /tmp/tier0-bundle/

# Copy offline packages
cp tier0-packages-rhel.tar.gz /tmp/tier0-bundle/   # or ubuntu
cp ansible-collections-offline.tar.gz /tmp/tier0-bundle/

# Create the transfer archive
tar czf tier0-darksite-bundle.tar.gz -C /tmp tier0-bundle/
```

### 8.2 Transfer to Darksite

Transfer `tier0-darksite-bundle.tar.gz` via:
- USB drive / portable media
- Secure file transfer (if a data diode exists)
- Cross-domain solution (CDS) per site security policy

### 8.3 Unpack on Control Machine

```bash
# On the darksite control machine
tar xzf tier0-darksite-bundle.tar.gz
cd tier0-bundle/ansible-tier0/
```

### 8.4 Install Offline Packages on Target Nodes

Copy the package archive to each target node and install:

```bash
# Using Ansible ad-hoc to distribute and install packages
# First, copy packages to all nodes
ansible storage_servers -m copy -a "src=tier0-packages-rhel.tar.gz dest=/tmp/"

# Extract on all nodes
ansible storage_servers -m shell -a "tar xzf /tmp/tier0-packages-rhel.tar.gz -C /tmp/"

# Install on RHEL/Rocky
ansible storage_servers -m shell -a "dnf localinstall -y /tmp/tier0-packages/*.rpm"

# Install on Ubuntu
# ansible storage_servers -m shell -a "dpkg -i /tmp/tier0-packages/*.deb; apt-get -f install -y"
```

Or create a simple pre-install playbook (`install_offline_packages.yml`):

```yaml
---
- name: Install offline packages on darksite nodes
  hosts: storage_servers
  become: true

  tasks:
    - name: Copy package archive
      ansible.builtin.copy:
        src: tier0-packages-rhel.tar.gz
        dest: /tmp/tier0-packages-rhel.tar.gz

    - name: Extract packages
      ansible.builtin.unarchive:
        src: /tmp/tier0-packages-rhel.tar.gz
        dest: /tmp/
        remote_src: true

    - name: Install packages (RHEL/Rocky)
      ansible.builtin.shell: dnf localinstall -y /tmp/tier0-packages/*.rpm
      when: ansible_os_family == "RedHat"

    - name: Install packages (Ubuntu/Debian)
      ansible.builtin.shell: dpkg -i /tmp/tier0-packages/*.deb && apt-get -f install -y
      when: ansible_os_family == "Debian"
```

```bash
ansible-playbook install_offline_packages.yml
```

---

## 9. Run Preflight Check

### 9.1 Precheck Only

```bash
# Run precheck tag only (validates drives, network, packages)
ansible-playbook site.yml --tags precheck

# If using a non-root user with sudo, pass the become password:
ansible-playbook site.yml --tags precheck --ask-become-pass -K
```

### 9.2 What Precheck Validates

**For HDD with Software RAID:**

| Check | Description |
|-------|-------------|
| SCSI/SATA drive count | Matches expected count |
| Drive in-use status | Ensures drives are not mounted/in RAID/LVM |
| NUMA balance | Warns if drives are unevenly distributed |
| MTU / jumbo frames | Tests network MTU to Hammerspace |
| Required packages | `mdadm`, `xfsprogs`, `parted`, `nfs-utils`, `smartmontools`, `hdparm` |
| NFS exports | Shows existing exports |

**For Hardware RAID:**

| Check | Description |
|-------|-------------|
| MTU / jumbo frames | Tests network MTU to Hammerspace |
| Required packages | `xfsprogs`, `parted`, `nfs-utils` (no `mdadm`) |
| NFS exports | Shows existing exports |
| HW RAID devices | Verified during discovery role |

---

## 10. Deploy

### 10.1 Dry Run (Recommended First)

> **Note:** `--check` mode skips shell commands, so storage discovery will not work.
> Use `--tags "discovery,precheck"` instead — these roles are read-only and safe to run.

```bash
# Recommended: run discovery + precheck (read-only, no changes to storage)
ansible-playbook site.yml --tags "discovery,precheck"

# If using a non-root user with sudo:
ansible-playbook site.yml --tags "discovery,precheck" --ask-become-pass -K
```

### 10.2 Full Deployment

```bash
# Deploy to all storage servers
ansible-playbook site.yml
```
# If using a non-root user with sudo, pass the become password:
ansible-playbook site.yml --ask-become-pass -K
```

### 10.3 Deploy to Specific Nodes

```bash
# Single node
ansible-playbook site.yml --limit "tier0-node-01"

# Multiple nodes
ansible-playbook site.yml --limit "tier0-node-01,tier0-node-02"

# Pattern
ansible-playbook site.yml --limit "tier0-rack-a-*"
```

### 10.4 Deploy by Role (Step-by-Step)

```bash
# Step 1: Discovery and precheck
ansible-playbook site.yml --tags "discovery,precheck"

# Step 2: Storage setup (RAID + filesystem)
ansible-playbook site.yml --tags "storage"

# Step 3: NFS
ansible-playbook site.yml --tags "nfs"

# Step 4: Firewall
ansible-playbook site.yml --tags "firewall"

# Step 5: Hammerspace integration
ansible-playbook site.yml --tags "hammerspace"
```

### 10.5 Deployment Flow

**HDD + Software RAID:**
```
┌─────────────────────────────────────────────────────────────┐
│  1. nvme_discovery    Discover /dev/sd* devices, group by   │
│                       NUMA, build RAID arrays               │
├─────────────────────────────────────────────────────────────┤
│  2. precheck          Validate drives, MTU, packages        │
├─────────────────────────────────────────────────────────────┤
│  3. raid_setup        Create mdadm RAID arrays              │
├─────────────────────────────────────────────────────────────┤
│  4. filesystem_setup  Create XFS filesystems, mount         │
├─────────────────────────────────────────────────────────────┤
│  5. nfs_setup         Configure NFS server and exports      │
├─────────────────────────────────────────────────────────────┤
│  6. firewall_setup    Open NFS and RDMA ports               │
├─────────────────────────────────────────────────────────────┤
│  7. hammerspace_integration  Register node + volumes        │
└─────────────────────────────────────────────────────────────┘
```

**Hardware RAID:**
```
┌─────────────────────────────────────────────────────────────┐
│  1. nvme_discovery    HW RAID mode: verify devices, build   │
│                       mount points (skip drive discovery)    │
├─────────────────────────────────────────────────────────────┤
│  2. precheck          Validate MTU, packages (skip drive    │
│                       checks, skip mdadm)                   │
├─────────────────────────────────────────────────────────────┤
│  3. raid_setup        *** SKIPPED (use_raid: false) ***     │
├─────────────────────────────────────────────────────────────┤
│  4. filesystem_setup  Create XFS filesystems on HW RAID     │
│                       devices, mount                        │
├─────────────────────────────────────────────────────────────┤
│  5. nfs_setup         Configure NFS server and exports      │
├─────────────────────────────────────────────────────────────┤
│  6. firewall_setup    Open NFS and RDMA ports               │
├─────────────────────────────────────────────────────────────┤
│  7. hammerspace_integration  Register node + volumes        │
└─────────────────────────────────────────────────────────────┘
```

---

## 11. Verify Deployment

### 11.1 On Target Nodes

```bash
# SSH to a deployed node
ssh tier0-node-01

# Check mounts
df -h | grep hammerspace
# Expected:
# /dev/md0       3.6T   33M  3.6T   1% /hammerspace/hsvol0   (SW RAID)
# /dev/sda       1.8T   33M  1.8T   1% /hammerspace/hsvol0   (HW RAID)

# Check NFS exports
exportfs -v

# Check NFS service
systemctl status nfs-server

# Test NFS from another node
showmount -e 192.168.1.101

# For software RAID - check RAID status
cat /proc/mdstat

# Check fstab entries
grep hammerspace /etc/fstab
```

### 11.2 In Hammerspace

```bash
# Via Anvil CLI
anvil> node-list
anvil> volume-list
anvil> volume-list --node-name tier0-node-01

# Via API
curl -sk -u admin:password \
  https://10.0.10.15:8443/mgmt/v1.2/rest/nodes | python3 -m json.tool

curl -sk -u admin:password \
  https://10.0.10.15:8443/mgmt/v1.2/rest/storage-volumes | \
  python3 -c "import sys,json; [print(v['name']) for v in json.load(sys.stdin)]"
```

### 11.3 Verify NFS Mount from Client

```bash
# From a Hammerspace DSX/DI node or client
mount -t nfs -o vers=3 192.168.1.101:/hammerspace/hsvol0 /mnt/test
ls /mnt/test
umount /mnt/test
```

---

## 12. Adding New Nodes

### 12.1 Update Inventory

Add new nodes to `inventory.yml`:

```yaml
storage_servers:
  hosts:
    tier0-node-01:
      ansible_host: 192.168.1.101
    tier0-node-02:
      ansible_host: 192.168.1.102
    # New nodes:
    tier0-node-05:
      ansible_host: 192.168.1.105
      hammerspace_volume_az_prefix: "AZ2:"
    tier0-node-06:
      ansible_host: 192.168.1.106
      hammerspace_volume_az_prefix: "AZ2:"
```

### 12.2 Install Offline Packages on New Nodes

```bash
ansible-playbook install_offline_packages.yml --limit "tier0-node-05,tier0-node-06"
```

### 12.3 Deploy to New Nodes Only

```bash
# Precheck first
ansible-playbook site.yml --tags precheck --limit "tier0-node-05,tier0-node-06"

# Full deployment
ansible-playbook site.yml --limit "tier0-node-05,tier0-node-06"
```

---

## 13. Troubleshooting

### Package Installation Issues (Offline)

**Problem:** `No match for argument: mdadm`
```bash
# Packages not in local repo. Install from downloaded RPMs:
dnf localinstall -y /tmp/tier0-packages/mdadm-*.rpm
```

**Problem:** Missing dependencies for DEB packages
```bash
# Fix broken dependencies
apt-get -f install -y
```

### SSH / Connectivity Issues

**Problem:** `Permission denied`
```bash
# Check SSH key
ssh -i /path/to/key -v root@192.168.1.101

# If using password auth, add to inventory:
#   ansible_ssh_pass: "your-password"
#   ansible_become_pass: "your-sudo-password"
```

**Problem:** `Connection timed out`
```bash
# Check network path
ping 192.168.1.101
traceroute 192.168.1.101

# Check firewall on target
iptables -L -n | head
```

### Storage Issues

**Problem:** HW RAID device not found
```bash
# Verify device exists
ls -la /dev/sda /dev/sdb

# Check controller
lspci | grep -i raid

# Rescan SCSI bus
echo "- - -" > /sys/class/scsi_host/host0/scan
```

**Problem:** Boot device included in RAID
```bash
# Check boot device
findmnt -n -o SOURCE /
lsblk -no PKNAME "$(findmnt -n -o SOURCE /)"

# Exclude it in vars/main.yml:
scsi_exclude_devices:
  - sda    # boot device name
```

**Problem:** `Drive already in use` error
```bash
# Check what's using the drive
lsblk /dev/sdb
mount | grep sdb
cat /proc/mdstat | grep sdb

# For re-runs, set in vars/main.yml:
fail_on_drives_in_use: false
```

### Hammerspace API Issues

**Problem:** `Failed to connect to Hammerspace API`
```bash
# Test API connectivity
curl -sk -u admin:password https://10.0.10.15:8443/mgmt/v1.2/rest/nodes

# Check firewall
nc -zv 10.0.10.15 8443

# If no curl/nc available, use Python:
python3 -c "
import urllib.request, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
req = urllib.request.Request('https://10.0.10.15:8443/mgmt/v1.2/rest/nodes')
req.add_header('Authorization', 'Basic YWRtaW46cGFzc3dvcmQ=')  # base64(admin:password)
print(urllib.request.urlopen(req, context=ctx).read()[:200])
"
```

### NFS Issues

**Problem:** NFS exports not visible
```bash
# Restart NFS
systemctl restart nfs-server

# Check exports
exportfs -ra
exportfs -v

# Check /etc/exports
cat /etc/exports
```

**Problem:** Client cannot mount
```bash
# Check firewall on server
firewall-cmd --list-all 2>/dev/null || iptables -L -n

# Verify NFS ports are open
ss -tlnp | grep -E '2049|111|20048'
```

---

## Quick Reference Card

| Task | Command |
|------|---------|
| Test connectivity | `ansible storage_servers -m ping` |
| Dry run | `ansible-playbook site.yml --check` |
| Precheck only | `ansible-playbook site.yml --tags precheck` |
| Full deploy | `ansible-playbook site.yml` |
| Deploy specific nodes | `ansible-playbook site.yml --limit "tier0-node-01,tier0-node-02"` |
| Discovery + precheck | `ansible-playbook site.yml --tags "discovery,precheck"` |
| Storage only | `ansible-playbook site.yml --tags "storage"` |
| NFS only | `ansible-playbook site.yml --tags "nfs"` |
| Hammerspace only | `ansible-playbook site.yml --tags "hammerspace"` |
| Skip Hammerspace | `ansible-playbook site.yml --skip-tags hammerspace` |
| Verbose output | `ansible-playbook site.yml -vvv` |
| Install offline pkgs | `ansible-playbook install_offline_packages.yml` |

### Example vars/main.yml Quick Settings

**HDD + Software RAID:**
```yaml
storage_type: "hdd"
use_raid: true
use_dynamic_discovery: true
raid_level: 0
mount_base_path: /hammerspace
```

**Hardware RAID:**
```yaml
use_raid: false
use_dynamic_discovery: true
hw_raid_devices:
  - /dev/sda
  - /dev/sdb
mount_base_path: /hammerspace
```

**SSD + Software RAID:**
```yaml
storage_type: "ssd"
use_raid: true
use_dynamic_discovery: true
raid_level: 0
mount_base_path: /hammerspace
```

---

## Support

For issues or questions:
- Check the main [README.md](README.md) for detailed configuration options
- Review `vars/main.yml` for all available settings
- See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for cloud/OCI deployments
- Contact Hammerspace support for cluster-related issues
