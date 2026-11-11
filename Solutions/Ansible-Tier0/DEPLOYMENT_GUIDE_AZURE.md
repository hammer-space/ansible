# Hammerspace Tier 0 Deployment Guide for Azure

Step-by-step guide for deploying Hammerspace Tier 0 storage on Microsoft Azure
Virtual Machines.

**Who this is for:** the person doing the first Tier 0 deployment on Azure,
and anyone adding VMs later. No prior Ansible experience is assumed.

**Time:** about an hour for the control-machine setup and first deployment;
5–10 minutes per VM after that.

**What you will end up with:** every VM matching your filter running Tier 0
storage on its local NVMe, registered in Hammerspace, with the availability
zone (or fault domain) recorded on each node and volume.

> **Companion guides:** [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) (OCI) is the
> reference walkthrough. This guide mirrors it, replacing sections 3 (auth), 4
> (inventory) and 9 (AZ) with Azure equivalents and adding the Azure-only disk
> and network considerations. Sections that are identical across clouds — DI
> deployment, decommissioning, the Python operator scripts — point back to the
> OCI guide rather than being duplicated here.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Control Machine Setup](#2-control-machine-setup)
3. [Azure Authentication Setup](#3-azure-authentication-setup)
4. [Configure Inventory](#4-configure-inventory)
5. [Azure VM Sizing and Disk Layout](#5-azure-vm-sizing-and-disk-layout)
6. [Configure Variables](#6-configure-variables)
7. [Run Preflight Check](#7-run-preflight-check)
8. [Deploy](#8-deploy)
9. [Verify Deployment](#9-verify-deployment)
10. [Availability Zone (AZ) Configuration on Azure](#10-availability-zone-az-configuration-on-azure)
11. [Adding New VMs (Future Deployments)](#11-adding-new-vms-future-deployments)
12. [Data Instantiator (DI) Deployment](#12-data-instantiator-di-deployment)
13. [Troubleshooting](#13-troubleshooting)
14. [Decommissioning VMs](#14-decommissioning-vms)
15. [Quick Reference Card](#quick-reference-card)

---

## 1. Prerequisites

| Requirement | Description |
|-------------|-------------|
| Azure Subscription | Access to a subscription with VMs running |
| VM SKU | A size with local NVMe or attached data disks — see [section 5](#5-azure-vm-sizing-and-disk-layout) |
| SSH Access | SSH key configured for VM access (default user is usually `azureuser`) |
| Hammerspace Cluster | Anvil management IP and admin credentials |
| Network | VMs can reach the Hammerspace Anvil on port 8443, and Hammerspace/DI nodes can reach the VMs on 2049 |
| Locale (control host) | UTF-8 locale required. Non-English systems (e.g. Korean `ko_KR`) must `export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8` before running Ansible, or use `./scripts/run.sh`, which sets it automatically. |

> **Single-source the Anvil IP.** Set `hammerspace_api_host` once in
> `vars/main.yml`. `hammerspace_nodes`, `network_test_targets` and
> `hammerspace_cluster_mgmt_ip` default to references back to it — no need to
> repeat the IP in three places.

> **API preflight runs first.** The playbook tests reachability and credentials
> against the Hammerspace API in the first 30 seconds and fails fast with a
> specific message (401, 403, TCP failure, etc.) before any RAID / filesystem /
> NFS work begins.

### Azure-specific things to know before you start

Three Azure behaviours have no OCI equivalent and cause most first-run
surprises. Each is covered in detail later, listed here so nothing is a
surprise mid-deployment:

| # | Behaviour | Where covered |
|---|-----------|---------------|
| 1 | The **ephemeral resource disk** (usually `/dev/sdb`, mounted at `/mnt`) is wiped on deallocate/redeploy and must never be RAIDed | [Section 5.3](#53-the-ephemeral-resource-disk) |
| 2 | Azure has **two** placement concepts — `zone` (1-based) and `platformFaultDomain` (0-based) — and the inventory plugin only exposes the first | [Section 10](#10-availability-zone-az-configuration-on-azure) |
| 3 | **Accelerated Networking** is off by default on some deployment paths and roughly halves achievable NFS throughput when missing | [Section 5.4](#54-accelerated-networking) |

---

## 2. Control Machine Setup

Run these on your control machine (laptop, bastion host, or workstation).

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

### 2.2 Clone the Repository

```bash
git clone <repository-url> ansible-tier0
cd ansible-tier0

# Pull large files (DI RPMs in payload/) — requires git-lfs
# Install git-lfs: brew install git-lfs (macOS) or apt install git-lfs (Linux)
git lfs pull
```

### 2.3 Install Ansible Collections

```bash
ansible-galaxy collection install -r requirements.yml
```

**Expected output includes:**
```
Installing 'ansible.posix:>=1.4.0' ...
Installing 'community.general:>=6.0.0' ...
Installing 'azure.azcollection:>=2.3.0' ...
```

### 2.4 Install the Azure Python SDK

The Azure collection ships its own Python requirements file, and it is large
(dozens of `azure-mgmt-*` packages). Install it into a virtualenv if you want
to keep it isolated from the rest of your system Python:

```bash
pip3 install -r ~/.ansible/collections/ansible_collections/azure/azcollection/requirements.txt
```

If `ansible-galaxy` installed the collection somewhere else, find it with:

```bash
ansible-galaxy collection list azure.azcollection
```

---

## 3. Azure Authentication Setup

### 3.1 Install the Azure CLI (if not installed)

```bash
# macOS
brew install azure-cli

# Ubuntu/Debian
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# RHEL/Rocky
sudo rpm --import https://packages.microsoft.com/keys/microsoft.asc
sudo dnf install -y azure-cli
```

### 3.2 Configure Azure Authentication

Pick **one** of the three options. The inventory ships with
`auth_source: auto`, which tries environment variables, then a credential
profile, then the CLI, then managed identity.

**Option 1 — Azure CLI (simplest for interactive use):**
```bash
az login
az account set --subscription <subscription-id>
```

**Option 2 — Service principal (for CI / automation):**
```bash
# Create the service principal (needs Owner or User Access Administrator)
az ad sp create-for-rbac --name ansible-tier0 \
    --role Reader \
    --scopes /subscriptions/<subscription-id>/resourceGroups/tier0-rg

# Export the values it prints
export AZURE_SUBSCRIPTION_ID='...'
export AZURE_CLIENT_ID='...'      # "appId" from the output
export AZURE_SECRET='...'         # "password" from the output
export AZURE_TENANT='...'         # "tenant" from the output
```

`Reader` on the resource group is sufficient for inventory. The playbook itself
talks to the VMs over SSH and to Hammerspace over HTTPS — it makes no Azure
control-plane writes.

**Option 3 — Managed identity (control host is itself an Azure VM):**

Assign a system-assigned or user-assigned identity with `Reader` on the target
resource group, then set `auth_source: msi` in `inventory.azure.yml`.

### 3.3 Verify Azure Connectivity

```bash
# Confirm which subscription you are on
az account show -o table

# List VMs in the resource group, with power state and IPs
az vm list -d -g tier0-rg -o table

# Confirm the zone each VM landed in (empty column = non-zonal)
az vm list -g tier0-rg --query "[].{name:name, zone:zones[0], size:hardwareProfile.vmSize}" -o table
```

Note that last command's output — whether the `zone` column is populated
decides which AZ path you take in [section 10](#10-availability-zone-az-configuration-on-azure).

---

## 4. Configure Inventory

### 4.1 Update ansible.cfg

Edit `ansible.cfg` to use the Azure dynamic inventory and your SSH key:

```ini
[defaults]
inventory = inventory.azure.yml
remote_user = azureuser
private_key_file = /path/to/your/ssh/key    # <-- Path to your SSH private key
```

> **Do not confuse `inventory.azure.yml` with `inventory.az.yml`.**
> `inventory.az.yml` is a **static** inventory pre-grouped by availability
> zone — the `az` is "availability zone", not "Azure". It predates Azure
> support and is unrelated.

> **Filename matters.** The `azure_rm` plugin only claims files ending in
> `azure_rm.yml` or `azure.yml`. `inventory.azure.yml` satisfies the second
> rule. Renaming it to something like `inventory-azure.yml` silently breaks
> discovery with "Unable to parse inventory".

### 4.2 Configure the Azure Inventory

Edit `inventory.azure.yml`. At minimum, set the resource group and confirm the
`storage_servers` grouping matches your VM naming:

```yaml
plugin: azure.azcollection.azure_rm

# REQUIRED: which resource group(s) hold the Tier 0 VMs — see 4.3 to find it.
# Remove this key entirely to scan the whole subscription (much slower).
include_vm_resource_groups:
  - tier0-rg

# Bare VM name as the Ansible hostname, instead of "name_(resourcegroup)".
plain_host_names: true

auth_source: auto

# Only running, successfully-provisioned VMs.
exclude_host_filters:
  - "powerstate != 'running'"
  - "provisioning_state != 'succeeded'"

hostvar_expressions:
  ansible_host: private_ipv4_addresses[0]
  azure_zone: availability_zone | default(None)
  azure_vm_size: virtual_machine_size | default(None)
  azure_location: location | default(None)
  azure_resource_group: resource_group | default(None)

# Which VMs become storage_servers — see 4.4.
conditional_groups:
  storage_servers: >-
    virtual_machine_size is defined and
    virtual_machine_size in ['Standard_L16s_v3', 'Standard_L32s_v3']

keyed_groups:
  - key: availability_zone
    prefix: az
    separator: ''
  - key: virtual_machine_size
    prefix: size
  - key: location
    prefix: loc

# conditional_groups expressions fail SILENTLY by default. Set true to debug.
strict: false
```

### 4.3 Finding Your Resource Group

`include_vm_resource_groups` is the one value you cannot guess. To find it:

```bash
# Which subscriptions can you see?
az account list -o table
az account set --subscription <id>

# Every VM with its resource group. No -d: resourceGroup is in the basic
# payload and --show-details is dramatically slower on large subscriptions.
az vm list --query "[].{name:name, rg:resourceGroup, size:hardwareProfile.vmSize}" -o table

# Just the distinct resource groups that contain VMs
az vm list --query "[].resourceGroup" -o tsv | sort -u
```

Narrow it if you already know something about the nodes:

```bash
# By name fragment
az vm list --query "[?contains(name,'tier0')].{name:name, rg:resourceGroup}" -o table

# By tag
az vm list --query "[?tags.role=='tier0'].{name:name, rg:resourceGroup}" -o table

# By a private IP you already have (from the Anvil, or an existing export)
az network nic list \
  --query "[?ipConfigurations[0].privateIPAddress=='10.0.14.101'].{nic:name, rg:resourceGroup}" -o table
```

Then choose the scope:

| Situation | Setting |
|-----------|---------|
| All Tier 0 VMs in one resource group | List that one. Fastest, narrowest permissions. |
| Spread across a few groups | List them all. |
| Spread unpredictably, or you don't control placement | Delete the key and rely on `conditional_groups`. Scans the whole subscription — slow, and needs `Reader` at **subscription** scope rather than resource-group scope. |
| VMs live in a scale set | Use `include_vmss_resource_groups` — `include_vm_resource_groups` will not find them. |

**The two filters do different jobs**, and conflating them is the usual cause
of an empty inventory:

- `include_vm_resource_groups` — what is **fetched from Azure**. A cost and
  permissions boundary. Set it too narrow and no `conditional_groups`
  expression can recover the missing hosts.
- `conditional_groups` — what, of the fetched set, lands in the group the plays
  target. Too broad here costs only API time; the group filter still applies.

### 4.4 Choosing the storage_servers Filter

> **This filter is destructive if it is too broad.** Whatever lands in
> `storage_servers` gets its disks discovered, RAIDed and mkfs'd. A VM that
> matches by accident loses its data. Always confirm with
> `ansible-inventory -i inventory.azure.yml --graph` before the first run
> against a new environment.

In Azure, "size" and "SKU" are the same thing — `Standard_L16s_v3` — exposed to
the inventory as `virtual_machine_size`. Three ways to use it, in increasing
order of breadth:

**Option 1 — exact sizes (default, tightest).** Tier 0 fleets are normally
homogeneous, so an explicit list is precise and self-documenting:

```yaml
conditional_groups:
  storage_servers: >-
    virtual_machine_size is defined and
    virtual_machine_size in ['Standard_L16s_v3', 'Standard_L32s_v3']
```

**Option 2 — whole SKU family.** Survives adding a node of a different size in
the same family, at the cost of sweeping in any same-family VM in the resource
group. Matches storage-optimized L-family (Lsv2/Lsv3/Lasv3/Lsv4) and the NC/ND
GPU families:

```yaml
conditional_groups:
  storage_servers: >-
    virtual_machine_size is defined and
    virtual_machine_size | regex_search('^Standard_(L[0-9]+a?s_v[0-9]+|N[CD][0-9]+)')
```

Verified against `Standard_L{8,16,80}s_v{2,3,4}`, `Standard_L{8,16}as_v3`,
`Standard_NC{6s_v3,24ads_A100_v4,40ads_H100_v5}`, `Standard_ND96{asr_v4,isr_H100_v5}`;
correctly rejects the D, E, F, B and M families.

**Option 3 — tag, optionally AND-ed with the SKU (safest).** Expresses intent
explicitly rather than inferring it from hardware, so a same-SKU VM added for
some other purpose cannot join:

```bash
az vm update -g <rg> -n <vm-name> --set tags.role=tier0
```

```yaml
conditional_groups:
  storage_servers: >-
    tags.role | default('') == 'tier0' and
    virtual_machine_size | default('') | regex_search('^Standard_L')
```

**Why not match on the name?** A name fragment like `'tier0' in name` breaks
the moment someone renames a VM or spins one up off-convention, and it fails
*open* in the dangerous direction — a test VM named `tier0-scratch` would be
swept in and wiped.

**Debugging:** `conditional_groups` expressions fail **silently** — a typo
drops hosts with no error and `storage_servers` simply comes back empty. Set
`strict: true` in the inventory to turn those into hard errors while you are
getting the expression right.

### 4.4b Local NVMe arrives pre-mounted (HB/HC/L-series, CycleCloud)

Azure auto-mounts local NVMe as scratch. On an HB120rs_v3 provisioned by
CycleCloud the 894 GB NVMe comes up already in use:

```
NAME     MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS
sda      8:0     0    64G   0 disk
├─sda2   8:2     0   200M   0 part /boot/efi
├─sda3   8:3     0     1G   0 part /boot
└─sda4   8:4     0  62.8G   0 part /
sdb      8:16    0    64G   0 disk
└─sdb1   8:17    0    64G   0 part /mnt          ← Azure resource disk
nvme0n1  259:0   0 894.3G   0 disk /tmp
                                   /nvme         ← the Tier 0 candidate
```

`/tmp` is a SYSTEM mountpoint, so protected-disk detection correctly classifies
`nvme0n1` as untouchable and discovery reports **`Discovery produced 0 RAID
arrays`**. That is not a bug — the disk holds a live filesystem, and `mkfs`
would fail `EBUSY` regardless.

Three things must line up on this shape of host:

| Symptom | Cause | Fix |
|---|---|---|
| `NVMe scan ran?: NO` | `storage_type` is `ssd`/`hdd`/`scsi` — those scan only `/dev/sd*` | `storage_type: nvme` |
| `nvme0n1` in the protected list | it is mounted at `/tmp` and `/nvme` | `release_ephemeral_mountpoints` (below) |
| 0 arrays with 1 disk found | `raid_min_drives_per_array: 2` skips a single-drive NUMA group | see "Single local NVMe" below |

**Releasing the scratch mounts.** Opt-in, off by default:

```yaml
# vars/main.yml
storage_type: nvme
release_ephemeral_mountpoints:
  - /tmp
  - /nvme
```

This unmounts both and removes their `/etc/fstab` entries *before* discovery,
so the disk is an ordinary candidate by the time classification runs. Nothing
is reformatted by that step, and the usual safety gates still apply afterwards.

A mountpoint whose disk also backs `/`, `/boot`, `/usr`, `/var`, `/etc`,
`/home` or active swap is **refused outright** — that list cannot be
overridden.

> **`/tmp` will usually fail to unmount on the first run.** It is nearly always
> busy on a running system (systemd, logind, user sessions). The fstab entry is
> removed regardless, so the sequence is: run once, reboot the node, run again.
> A lazy unmount is deliberately not used — it reports success while the
> filesystem is still referenced, and a later `mkfs` can then hit a live device.

Check afterwards that nothing will remount them:

```bash
ansible -i inventory.azure.yml storage_servers -b \
  -a 'findmnt -n -o TARGET,SOURCE /tmp /nvme; grep -E "/tmp|/nvme" /etc/fstab'
```

**Single local NVMe.** HB120rs_v3 has one NVMe, so there is nothing to stripe.
Skip mdadm rather than forcing a one-member array:

```yaml
use_raid: false
hw_raid_devices:
  - /dev/nvme0n1
```

That path `stat`s the device, formats it XFS and mounts it at
`/hammerspace/hsvol0` directly. It still requires the disk to be unmounted
first, so `release_ephemeral_mountpoints` applies either way.

### 4.5 Scale Sets (VMSS)

Relevant if the Tier 0 nodes are provisioned as a scale set — the normal case
for CycleCloud/HPC execute nodes.

**Scale-set instances are not fetched by default.** `include_vm_resource_groups`
returns **standalone VMs only**; VMSS fetch is a separate option that defaults
to off. If `az vmss list` shows nodes that never appear in
`ansible-inventory --list`, this is why:

```yaml
include_vmss_resource_groups:
  - '*'          # every resource group, or list specific ones
```

**Check your orchestration mode first** — it changes what appears where:

```bash
az vmss list -g <rg> --query "[].{name:name, mode:orchestrationMode}" -o table
```

| Mode | Behaviour |
|------|-----------|
| **Uniform** | Instances are not standalone VM resources. They appear **only** via `include_vmss_resource_groups`. |
| **Flexible** | Instances **are** standard VM resources, so they may appear via `include_vm_resource_groups` too. Enabling both can surface the same node twice under different names. |

**Filtering on scale-set membership.** The `vmss` hostvar is a dict
`{id, name}` for a scale-set instance and an **empty dict `{}`** for a
standalone VM — so it is *always defined*. Test its length; `vmss is defined`
is true for both and silently matches everything:

| Intent | Expression |
|--------|-----------|
| Only scale-set instances | `vmss \| default({}) \| length > 0` |
| Only standalone VMs | `vmss \| default({}) \| length == 0` |
| One named scale set | `(vmss \| default({})).get('name', '') == 'execute'` |
| Scale set **and** SKU | `(vmss \| default({})).get('name', '') == 'execute' and virtual_machine_size \| default('') == 'Standard_L16s_v3'` |

`resource_type` is an independent second signal, handy for cross-checking:
`Microsoft.Compute/virtualMachines` versus
`Microsoft.Compute/virtualMachineScaleSets/virtualMachines`.

Putting it together — Tier 0 nodes are the `L16s_v3` members of the `tier0`
scale set, and nothing else in the resource group qualifies:

```yaml
include_vm_resource_groups:
  - tier0-rg
include_vmss_resource_groups:
  - tier0-rg

conditional_groups:
  storage_servers: >-
    (vmss | default({})).get('name', '') == 'tier0' and
    virtual_machine_size | default('') == 'Standard_L16s_v3'
```

The inventory also groups by scale set via `keyed_groups`, giving
`vmss_<name>` (and `vmss_standalone` for non-VMSS VMs), so you can target one
scale set directly:

```bash
ansible-playbook site.yml -i inventory.azure.yml --limit vmss_tier0
```

> **Scale sets and Tier 0 are an awkward fit.** Scale sets exist to make
> instances disposable and interchangeable; Tier 0 nodes hold RAIDed local
> NVMe registered as named Hammerspace volumes. A scale-in event destroys a
> registered storage node and its data. If the Tier 0 nodes are in a scale
> set, disable autoscale on it, or accept that recovery means re-running the
> deployment and cleaning the orphaned volumes out of Hammerspace with
> `cleanup_instance_nodes.py`.

### 4.6 Test Inventory Discovery

```bash
# List all discovered hosts
ansible-inventory -i inventory.azure.yml --list

# Show as a graph — confirms storage_servers and the az1/az2/az3 groups
ansible-inventory -i inventory.azure.yml --graph

# Ping all storage servers
ansible -i inventory.azure.yml storage_servers -m ping
```

**Expected graph:**
```
@all:
  |--@storage_servers:
  |  |--tier0-node-01
  |  |--tier0-node-02
  |--@az1:
  |  |--tier0-node-01
  |--@az2:
  |  |--tier0-node-02
```

> The inventory caches results for 300 seconds
> (`cache_connection: /tmp/ansible_azure_inventory_cache`). After creating or
> destroying VMs, either wait it out or clear the cache:
> `rm -rf /tmp/ansible_azure_inventory_cache`.

**Reading an empty result:**

| Symptom | Cause |
|---------|-------|
| Hosts under `@all`, but `storage_servers` empty | Resource group is right; the `conditional_groups` expression is wrong. Set `strict: true` and re-run to see the error. |
| `@all` empty too | Resource group, subscription or auth scope is wrong — or every VM was dropped by `exclude_host_filters` (not running / not succeeded). |
| Hosts appear but with no `azure_zone` | Expected on non-zonal VMs. See [section 10](#10-availability-zone-az-configuration-on-azure). |

---

## 5. Azure VM Sizing and Disk Layout

This section has no OCI counterpart and is where most Azure-specific effort
goes. Read it before the first run.

### 5.1 Choosing a VM SKU

| SKU family | Local storage | Set `storage_type` |
|------------|---------------|--------------------|
| **Lsv3 / Lasv3** (storage optimized) | Local NVMe | `nvme` |
| **Lsv2** (storage optimized, older) | Local NVMe | `nvme` |
| **ND / NC** (GPU) | Local NVMe on most sizes | `nvme` |
| **Any SKU + attached Premium SSD v2 / Ultra Disk** | Managed data disks as `/dev/sd*` | `scsi` (not `ssd` — Azure reports managed disks as rotational, so `ssd` finds nothing) |

The storage-optimized L-family is the natural fit for Tier 0: local NVMe with
no per-IO billing and no network hop. Managed disks work but are billed on
provisioned IOPS/throughput and are slower for this workload.

### 5.2 Confirm the Actual Device Layout

**Do not assume the device naming — check it.** Azure's newer "NVMe-enabled"
VM generations expose the OS disk and attached data disks as `/dev/nvme*`
rather than `/dev/sd*`, so the same SKU family can present differently
depending on generation and image.

```bash
ansible -i inventory.azure.yml storage_servers -m shell \
    -a 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL' -b
```

Read the output and decide:

- Local NVMe drives shown as `nvme0n1`, `nvme1n1`, … with no mountpoint →
  `storage_type: nvme`
- Attached data disks shown as `sdc`, `sdd`, … with no mountpoint →
  `storage_type: scsi`
- The disk holding `/` → protected automatically, never touched
- A disk mounted at `/mnt` → the resource disk, see next section

### 5.3 The Ephemeral Resource Disk

Azure VMs get a temporary **resource disk** — typically `/dev/sdb`, mounted at
`/mnt` (some images use `/mnt/resource`) by cloud-init or the Azure Linux
Agent. It is **local, unbacked and wiped on deallocate/redeploy**. Putting it
in a Tier 0 RAID set produces an array that silently loses a member the first
time the VM is stopped.

Disk discovery protects it automatically: `/mnt` and `/mnt/resource` are
treated as system mountpoints, so the resource disk is classified as protected
and excluded from discovery.

**Verify this before the first real run** — one dry run, and read the
classification output:

```bash
./scripts/run.sh playbook site.yml -i inventory.azure.yml --check --tags discovery
```

Look for the "Disk classification (boot-drive safety)" task and confirm the
resource disk appears under **STRICT protected**:

```
Disk classification (boot-drive safety):
  STRICT protected (boot / mounted FS / swap — NEVER touched):
    - /dev/sda        <-- OS disk
    - /dev/sdb        <-- resource disk, mounted at /mnt
  Existing md array members (will be ADOPTED, not recreated):
    (none)
  Primary boot disk: sda
```

If the resource disk is **not** listed, stop. Either it is mounted somewhere
unexpected, or it is unmounted entirely (some custom images disable it). Check
where it actually is:

```bash
ansible -i inventory.azure.yml storage_servers -m shell \
    -a 'findmnt -n -o TARGET,SOURCE /mnt /mnt/resource 2>/dev/null; \
        grep -i ResourceDisk /etc/waagent.conf' -b
```

An **unmounted** resource disk is genuinely indistinguishable from a blank data
disk by mountpoint alone. If your image leaves it unmounted, exclude it
explicitly rather than relying on mount detection. The exclusion variable
depends on how the disk presents:

```yaml
# vars/main.yml
# Resource disk presenting as /dev/sdb (the usual case)
scsi_exclude_devices:
  - sdb

# Resource disk presenting as an NVMe namespace
nvme_exclude_devices:
  - nvme1n1
```

Excluding by **serial** is more durable than by name — Linux device names can
shift across reboots, serials cannot:

```yaml
scsi_exclude_serials:
  - "<serial from lsblk -o NAME,SERIAL>"
```

Note this only matters when `storage_type` is `ssd` / `hdd` / `scsi` / `all`.
With `storage_type: nvme` the SCSI discovery path never runs, so a `/dev/sdb`
resource disk is never a candidate in the first place.

See [VARIABLE_REFERENCE.md](VARIABLE_REFERENCE.md) for the full set of
`nvme_exclude_*` / `scsi_exclude_*` filters (name, path, serial, model, NUMA
node, PCIe address, PCIe prefix).

### 5.4 Accelerated Networking

Accelerated Networking (SR-IOV) is required for Tier 0 throughput targets. It
is enabled by default on most current sizes and images, but not on every
deployment path — and when it is missing, NFS throughput is roughly halved with
no error anywhere.

**Check:**
```bash
az network nic list -g tier0-rg \
    --query "[].{nic:name, accelerated:enableAcceleratedNetworking}" -o table
```

**Enable (requires the VM to be deallocated):**
```bash
az vm deallocate -g tier0-rg -n <vm-name>
az network nic update -g tier0-rg -n <nic-name> --accelerated-networking true
az vm start -g tier0-rg -n <vm-name>
```

Confirm from inside the VM — with Accelerated Networking the Mellanox VF is
visible alongside the synthetic interface:

```bash
ansible -i inventory.azure.yml storage_servers -m shell \
    -a 'lspci | grep -i mellanox; ethtool -i eth0 | head -3' -b
```

The `perf_tuning` role's NIC IRQ pinning targets the real interface, so this
should be settled before deployment rather than after.

### 5.5 Network Security Groups

Azure's default NSG rules allow inbound traffic **within the VNet**
(`AllowVnetInBound`), so if the Anvil, DI nodes and Tier 0 VMs share a VNet,
NFS works with no NSG changes. Rules are needed when traffic crosses a VNet
boundary or when a restrictive NSG has replaced the defaults.

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 22 | TCP | Inbound | SSH from the control host |
| 2049 | TCP | Inbound | NFS from Hammerspace + DI nodes |
| 8443 | TCP | Outbound | Hammerspace API to the Anvil |

```bash
az network nsg rule create -g tier0-rg --nsg-name tier0-nsg \
    --name allow-nfs --priority 1000 \
    --source-address-prefixes 10.241.0.0/24 \
    --destination-port-ranges 2049 --protocol Tcp --access Allow
```

The `firewall_setup` role handles the in-guest firewall (firewalld / UFW /
iptables). NSGs are a separate layer and are **not** managed by this playbook.

### 5.6 Placement

For multi-node Tier 0 deployments, placement determines both latency and
failure isolation, and the two goals conflict:

| Goal | Mechanism | Effect on AZ mapping |
|------|-----------|----------------------|
| Lowest latency between nodes | **Proximity Placement Group** | All nodes in one zone → single AZ |
| Fault isolation | **Availability Zones** (1, 2, 3) | `azure_zone` populated → AZ per zone |
| Fault isolation, no zone support in region | **Availability Set** | `azure_zone` empty → needs IMDS, see [section 10](#10-availability-zone-az-configuration-on-azure) |

Zones are the right default for Tier 0 — they map cleanly onto Hammerspace AZs
and give real redundancy. Reach for a proximity placement group only when a
measured latency requirement demands it, and accept that all nodes then share
one AZ.

---

## 6. Configure Variables

### 6.1 Edit vars/main.yml

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
```

#### Storage Configuration

```yaml
# Dynamic discovery (recommended)
use_dynamic_discovery: true

# Match the SKU — see section 5.1. "nvme" for Lsv3/ND local NVMe,
# "ssd" for attached managed disks presenting as /dev/sd*.
storage_type: "nvme"

# RAID level (0 for Tier 0)
raid_level: 0

# Mount point base path
mount_base_path: /hammerspace
```

#### Azure AZ Configuration

```yaml
# Enable AZ mapping (needed for any multi-AZ deployment)
hammerspace_enable_az_mapping: true

# Query Azure IMDS for zone / platformFaultDomain.
# REQUIRED for non-zonal VMs (availability sets) — see section 10.
hammerspace_azure_imds_az: true
```

See [VARIABLE_REFERENCE.md](VARIABLE_REFERENCE.md) for the complete list.

---

## 7. Run Preflight Check

The preflight check compares your Azure inventory with Hammerspace to identify
VMs that still need deployment.

```bash
ansible-playbook preflight_check.yml -i inventory.azure.yml
```

**Example output:**
```
================================================================================
PREFLIGHT CHECK REPORT
================================================================================
Hammerspace API: 10.241.0.105

SUMMARY
--------------------------------------------------------------------------------
Inventory hosts (storage_servers): 6
Hammerspace registered nodes:      4
Already registered:                4
New instances to deploy:           2

NEW INSTANCES (need deployment)
--------------------------------------------------------------------------------
- tier0-node-05
- tier0-node-06
================================================================================
```

| Output file | Description |
|-------------|-------------|
| `.new_instances_limit` | List of new VM names for `--limit` |
| `preflight_report.txt` | Full report saved to disk |

---

## 8. Deploy

### Option A: Deployment Script (Recommended)

```bash
# Dry run first (recommended)
./deploy_new_instances.sh -i inventory.azure.yml --check

# Interactive — prompts for confirmation
./deploy_new_instances.sh -i inventory.azure.yml

# Auto mode (no confirmation)
./deploy_new_instances.sh -i inventory.azure.yml --auto
```

### Option B: Manual Commands

```bash
# Step 1: Dry run
./scripts/run.sh playbook site.yml -i inventory.azure.yml --limit @.new_instances_limit --check

# Step 2: Precheck only
./scripts/run.sh playbook site.yml -i inventory.azure.yml --limit @.new_instances_limit --tags precheck

# Step 3: Full deployment
./scripts/run.sh playbook site.yml -i inventory.azure.yml --limit @.new_instances_limit
```

### Option C: Specific VMs

```bash
# Single VM
ansible-playbook site.yml -i inventory.azure.yml --limit "tier0-node-05"

# One availability zone (uses the keyed_groups from inventory.azure.yml)
ansible-playbook site.yml -i inventory.azure.yml --limit "az2"

# One VM size
ansible-playbook site.yml -i inventory.azure.yml --limit "size_Standard_L16s_v3"
```

### Option D: Throttled Deployment (Large Clusters)

```bash
# Process 2 nodes at a time
ansible-playbook site.yml -i inventory.azure.yml -e hammerspace_serial=2
```

### Deployment Progress

| Step | Role | Description |
|------|------|-------------|
| 1 | `nvme_discovery` | Discover drives, group by NUMA, classify protected disks |
| 2 | `precheck` | Validate drives, network, packages |
| 3 | `raid_setup` | Create mdadm RAID arrays |
| 4 | `filesystem_setup` | Create XFS filesystems |
| 5 | `nfs_setup` | Configure NFS server and exports |
| 6 | `perf_tuning` | Apply Hammerspace BKMs (sunrpc slots, NFSD direct I/O, read-ahead, NIC IRQ pinning, TCP sysctl) |
| 7 | `firewall_setup` | Open NFS and RDMA ports (in-guest only — NSGs are separate) |
| 8 | `hammerspace_integration` | Register node and volumes via API |

---

## 9. Verify Deployment

### 9.1 On the VMs

```bash
# RAID arrays
ansible -i inventory.azure.yml storage_servers -m shell -a 'cat /proc/mdstat' -b

# Mounts
ansible -i inventory.azure.yml storage_servers -m shell -a 'df -h | grep hammerspace' -b

# NFS exports
ansible -i inventory.azure.yml storage_servers -m shell -a 'exportfs -v' -b

# Confirm the resource disk was NOT consumed — /mnt must still be its own
# filesystem on its own device, not part of an md array.
ansible -i inventory.azure.yml storage_servers -m shell \
    -a 'findmnt -n -o TARGET,SOURCE /mnt; cat /proc/mdstat' -b
```

That last check is the Azure-specific one worth running every time: if `/mnt`
has vanished or its device now appears in `/proc/mdstat`, the resource disk was
absorbed into an array and the data on it will not survive a deallocate.

### 9.2 In Hammerspace

```bash
# List all nodes
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/nodes | jq '.[].name'

# Confirm AZ labels landed as expected
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/storage-volumes \
    | jq -r '.[].name' | sort
```

Volume names carry the AZ prefix, so this output is the fastest way to confirm
the AZ mapping worked: expect `AZ1:tier0-node-01::/hammerspace/hsvol0`, not
`tier0-node-01::/hammerspace/hsvol0` (no prefix) or `AZ0:` / `AZ:` (broken
mapping — see [section 10.5](#105-troubleshooting-az-mapping)).

### 9.3 Verification Playbook

```bash
ansible-playbook verify_nfs.yml -i inventory.azure.yml --limit @.new_instances_limit
```

### 9.4 Instance Report

Each run writes `instance_report.csv` at the repo root:

```csv
display_name,fault_domain,az
tier0-node-01,azure-zone-1,AZ1
tier0-node-02,azure-zone-2,AZ2
tier0-node-03,azure-fd-0,AZ1
```

The `fault_domain` column is self-describing on Azure: `azure-zone-N` means the
AZ came from an availability zone, `azure-fd-N` means it came from a platform
fault domain. `N/A` means neither was available and the node fell back to
`hammerspace_default_az`.

---

## 10. Availability Zone (AZ) Configuration on Azure

Hammerspace uses AZ prefixes (`AZ1:`, `AZ2:`) to place data across failure
domains. On Azure this is more subtle than on OCI, because Azure exposes **two**
placement values with different bases and different availability.

### 10.1 The Two Placement Values

| Value | Source | Base | Present when |
|-------|--------|------|--------------|
| `zone` | Inventory plugin **and** IMDS | **1-based** (`"1"`, `"2"`, `"3"`) | VM deployed into an availability zone. **Empty string** otherwise — not absent |
| `platformFaultDomain` | **IMDS only** | **0-based** (`"0"`, `"1"`, `"2"`) | Always |

This produces the mapping:

| Azure placement | Hammerspace AZ | Why |
|-----------------|----------------|-----|
| `zone` = `"1"` | `AZ1` | 1-based, used verbatim |
| `zone` = `"2"` | `AZ2` | |
| `zone` = `"3"` | `AZ3` | |
| `platformFaultDomain` = `0` | `AZ1` | **0-based — mapped as +1** |
| `platformFaultDomain` = `1` | `AZ2` | |
| `platformFaultDomain` = `2` | `AZ3` | |

Zone wins when both are present.

### 10.2 Full AZ Detection Priority

The same detection is used for node labelling, volume naming and the instance
report:

1. `hammerspace_node_az` — explicit per-host override
2. `oci_fault_domain` — OCI only
3. `azure_zone` — 1-based, used verbatim
4. `azure_fault_domain` — 0-based, `+1`
5. `AZ1:` prefix in the node name
6. Inventory group matching `^AZ[0-9]+$`
7. `hammerspace_default_az` (default `AZ1`)

### 10.3 Zonal VMs — Inventory Alone Is Enough

If `az vm list --query "[].zones"` returned values, the inventory plugin
supplies `azure_zone` and no extra configuration is needed:

```yaml
# vars/main.yml
hammerspace_enable_az_mapping: true
```

### 10.4 Non-Zonal VMs — IMDS Required

If the VMs are in an **availability set**, or the region has no zone support,
`zone` is an empty string for every VM. The inventory plugin exposes
`availability_zone` but **never** the fault domain, so inventory alone collapses
every node into `hammerspace_default_az` — all data in one AZ, no redundancy,
and no error to tell you.

Enable IMDS detection so the fault domain is read from each VM directly:

```yaml
# vars/main.yml
hammerspace_enable_az_mapping: true
hammerspace_azure_imds_az: true
```

This queries `http://169.254.169.254/metadata/instance/compute` on each host
during the `hammerspace_integration` role. It is safe to leave enabled
everywhere — the request has a 5-second timeout, failure is tolerated, and the
AZ chain simply falls through on non-Azure hosts. Leaving it on is the better
default if your fleet spans both zonal and non-zonal VMs.

Check what IMDS reports before relying on it:

```bash
ansible -i inventory.azure.yml storage_servers -m shell -a \
  'curl -s -H "Metadata: true" --noproxy 169.254.169.254 \
   "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01" \
   | grep -oE "\"(zone|platformFaultDomain|vmSize)\":\"[^\"]*\""'
```

**Expected on a zonal VM:** `"zone":"2"` and `"platformFaultDomain":"0"`.
**Expected on an availability-set VM:** `"zone":""` and `"platformFaultDomain":"1"`.

If the request times out, IMDS is being blocked — check for a proxy
(`http_proxy` picked up by curl) or an NSG/iptables rule intercepting
link-local traffic. The playbook's own IMDS task sets `use_proxy: false` for
this reason.

### 10.5 Troubleshooting AZ Mapping

| Symptom | Cause | Fix |
|---------|-------|-----|
| Every volume is `AZ1:` | Non-zonal VMs, IMDS disabled | Set `hammerspace_azure_imds_az: true` |
| Volume named `AZ0:...` or `AZ:...` (no number) | Detection bug | Should not happen on a current version — update, and report it to Hammerspace support with the volume name |
| Volume has no AZ prefix at all | `hammerspace_volume_az_prefix_enabled` is false, or mode is not `auto` | Check both in `vars/main.yml` |
| AZ correct in logs, wrong in Hammerspace | `hammerspace_apply_az_labels` is false | Set it true to push labels via the API |

Per-host override when automatic detection cannot work — for example VMs spread
across regions rather than zones:

```yaml
# In the inventory, or as a host_var
tier0-node-01:
  hammerspace_node_az: "AZ1"
tier0-node-02:
  hammerspace_node_az: "AZ2"
```

### 10.6 Verify AZ Assignment

```bash
# Volume names carry the AZ prefix
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/storage-volumes \
    | jq -r '.[].name' | sort

# Node labels (requires hammerspace_apply_az_labels: true)
curl -sk -u admin:password https://10.241.0.105:8443/mgmt/v1.2/rest/nodes \
    | jq -r '.[] | "\(.name)  \(.labels // [] | map(.value) | join(","))"'

# Or read the CSV the run produced
column -s, -t instance_report.csv
```

---

## 11. Adding New VMs (Future Deployments)

```bash
# 1. Clear the inventory cache so new VMs are discovered
rm -rf /tmp/ansible_azure_inventory_cache

# 2. Verify they appear
ansible-inventory -i inventory.azure.yml --graph

# 3. Preflight — identifies which are not yet in Hammerspace
ansible-playbook preflight_check.yml -i inventory.azure.yml

# 4. Review preflight_report.txt

# 5. Deploy only the new ones
./deploy_new_instances.sh -i inventory.azure.yml
```

Step 1 is the Azure-specific one. The inventory caches for 300 seconds, so a VM
created in the last few minutes will not appear and preflight will report
nothing to do.

---

## 12. Data Instantiator (DI) Deployment

DI deployment is cloud-agnostic. Follow
[DEPLOYMENT_GUIDE.md § 11](DEPLOYMENT_GUIDE.md#11-data-instantiator-di-deployment)
in full — host mode, container mode, Kubernetes mode, pre-deploy/activate-later
and decommissioning all work identically on Azure.

Two Azure notes:

**Inventory.** The `di_nodes` group is not produced by `inventory.azure.yml`'s
`conditional_groups`. Either add a second conditional group, or list DI nodes in
a static inventory alongside the dynamic one:

```yaml
# inventory.azure.yml
conditional_groups:
  storage_servers: "tags.role | default('') == 'tier0'"
  di_nodes: "tags.role | default('') == 'di'"
```

```bash
# Or combine a static DI list with the dynamic Azure inventory
ansible-playbook site.yml -i inventory.azure.yml -i inventory.yml \
    --tags di -e deploy_di=true
```

**Registry.** `di_k8s_registry` defaults to an OCIR path
(`{{ oci_region }}.ocir.io`). On Azure, point it at your Azure Container
Registry:

```yaml
di_k8s_registry: "myregistry.azurecr.io"
di_k8s_registry_namespace: "hammerspace"
di_k8s_registry_username: "<service-principal-app-id>"
di_k8s_registry_password: "{{ vault_acr_password }}"
```

---

## 13. Troubleshooting

### Azure Inventory Issues

**Problem:** `Failed to import the required Python library (azure-*)`
```bash
pip3 install -r ~/.ansible/collections/ansible_collections/azure/azcollection/requirements.txt
```

**Problem:** `Unable to parse inventory` / plugin not recognised
```bash
# The filename MUST end in azure_rm.yml or azure.yml.
# inventory.azure.yml is correct; inventory-azure.yml is not.

# Verify auth works independently of Ansible
az account show

# Test the inventory directly, with full errors
ansible-inventory -i inventory.azure.yml --list -vvv
```

**Problem:** No hosts discovered
```bash
# Confirm VMs exist and are running — exclude_host_filters drops
# anything not running/succeeded
az vm list -d -g tier0-rg -o table

# Confirm the resource group name matches include_vm_resource_groups
az group list -o table

# Confirm the conditional_groups expression matches your VM names.
# This lists hosts with NO grouping applied:
ansible-inventory -i inventory.azure.yml --list | jq '._meta.hostvars | keys'
```

**Problem:** Stale results after creating or deleting VMs
```bash
rm -rf /tmp/ansible_azure_inventory_cache
```

**Problem:** `The subscription is not registered to use namespace 'Microsoft.Compute'`
```bash
az provider register --namespace Microsoft.Compute
```

### SSH Connection Issues

**Problem:** `Permission denied (publickey)`
```bash
# Azure's default admin user is usually azureuser, not ubuntu/opc.
# Confirm what the VM was created with:
az vm show -g tier0-rg -n <vm-name> --query "osProfile.adminUsername" -o tsv

# Then set it in ansible.cfg (remote_user) or the inventory
ssh -i /path/to/key azureuser@<private-ip>
```

**Problem:** `Connection timed out`
```bash
# ansible_host is the PRIVATE IP (private_ipv4_addresses[0]), so the control
# host must be on the VNet — via VPN, ExpressRoute, a bastion, or by running
# Ansible from a VM in the same VNet.

# To use public IPs instead, change inventory.azure.yml:
#   ansible_host: public_ipv4_addresses[0]
# and confirm the NSG allows 22 from your address.
az network nsg rule list -g tier0-rg --nsg-name tier0-nsg -o table
```

### Azure Disk Issues

**Problem:** Resource disk was absorbed into a RAID array

Symptom: `/mnt` is gone after deployment, or its device appears in
`/proc/mdstat`. This means the resource disk was unmounted at discovery time,
so mountpoint-based protection could not see it.

```bash
# Confirm
findmnt -n -o TARGET,SOURCE /mnt
cat /proc/mdstat

# Fix: exclude it explicitly, then reset the host and redeploy
# vars/main.yml:
#   scsi_exclude_devices: [sdb]        # or nvme_exclude_devices if it is NVMe
ansible-playbook reset-tier0-host.yml -i inventory.azure.yml \
    --limit "<vm-name>" -e reset_confirm=true
```

**Problem:** `Discovery produced 0 RAID arrays`
```bash
# Usually storage_type does not match the actual device naming.
ansible -i inventory.azure.yml storage_servers -m shell \
    -a 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL' -b

# Local NVMe as nvme*n1  -> storage_type: nvme
# Data disks as sdc, sdd -> storage_type: ssd
```

### "Hammerspace API configuration missing" from preflight_check.yml

**Problem:** `ansible-playbook preflight_check.yml -i inventory.azure.yml`
fails immediately with `assertion: hammerspace_api_password is defined` /
`evaluated_to: false`, telling you to set values in `vars/main.yml` that are
already set.

The inventory is irrelevant here — the failing play is `hosts: localhost`, so
it fails the same way with any `-i`. The cause is the password coming from
`vars/vault.yml`: `vars/main.yml` maps it as

```yaml
hammerspace_api_password: "{{ vault_hammerspace_api_password }}"
```

so if the vault file is not loaded, the key exists but its value references an
undefined variable, and `is defined` evaluates false.

**Fixed in the repo** — `preflight_check.yml` now loads the vault in
`pre_tasks`. If you still see it, check in order:

```bash
# 1. Does the vault file define the variable — uncommented?
grep -n "vault_hammerspace_api_password" vars/vault.yml
#    A fresh checkout ships it COMMENTED OUT as an example:
#      # vault_hammerspace_api_password: "PASSWORD"

# 2. If encrypted, pass the password
ansible-playbook preflight_check.yml -i inventory.azure.yml --ask-vault-pass

# 3. Or bypass the vault entirely for a one-off
ansible-playbook preflight_check.yml -i inventory.azure.yml \
    -e hammerspace_api_password='...'
```

See [DEPLOYMENT_GUIDE.md § 12](DEPLOYMENT_GUIDE.md#12-troubleshooting) for the
cloud-agnostic failures — locale errors, `Device or resource busy` on re-run,
boot-drive safety gate, `repo_root` path errors, Hammerspace API preflight
failures.

### Azure AZ Issues

See [section 10.5](#105-troubleshooting-az-mapping). The quickest check is to
compare what Azure reports for a VM with what ended up in Hammerspace:

```bash
ansible -i inventory.azure.yml storage_servers -m shell \
    -a 'curl -s -H Metadata:true "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01" | jq "{zone, platformFaultDomain}"'
```

---

## 14. Decommissioning VMs

Decommissioning is cloud-agnostic — follow
[DEPLOYMENT_GUIDE.md § 13](DEPLOYMENT_GUIDE.md#13-decommissioning-instances).
`cleanup_instance_nodes.py` removes nodes and volumes from Hammerspace by name
pattern and does not care which cloud they came from.

```bash
# List what Hammerspace currently has
python3 cleanup_instance_nodes.py --host 10.241.0.105 --user admin \
    --password-file ~/.hs_password --list-nodes

# Dry run
python3 cleanup_instance_nodes.py --host 10.241.0.105 --user admin \
    --password-file ~/.hs_password --prefix "tier0-node" --dry-run

# Execute
python3 cleanup_instance_nodes.py --host 10.241.0.105 --user admin \
    --password-file ~/.hs_password --prefix "tier0-node"
```

To wipe a host's Tier 0 configuration but keep the VM:

```bash
ansible-playbook reset-tier0-host.yml -i inventory.azure.yml \
    --limit "tier0-node-05" -e reset_confirm=true
```

Note there is **no Azure equivalent of `rename_oci_instances_az.py`** — that
script talks to the OCI compute API to rename instances and is OCI-only. Rename
Azure VMs through the portal or `az` CLI if needed.

---

## Quick Reference Card

| Task | Command |
|------|---------|
| Azure login | `az login && az account set --subscription <id>` |
| List VMs + zones | `az vm list -g tier0-rg --query "[].{name:name,zone:zones[0],size:hardwareProfile.vmSize}" -o table` |
| Check Accelerated Networking | `az network nic list -g tier0-rg --query "[].{nic:name,accelerated:enableAcceleratedNetworking}" -o table` |
| Test inventory | `ansible-inventory -i inventory.azure.yml --graph` |
| Clear inventory cache | `rm -rf /tmp/ansible_azure_inventory_cache` |
| Ping all hosts | `ansible -i inventory.azure.yml storage_servers -m ping` |
| Check disk layout | `ansible -i inventory.azure.yml storage_servers -m shell -a 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL' -b` |
| Dry-run discovery (verify resource-disk protection) | `./scripts/run.sh playbook site.yml -i inventory.azure.yml --check --tags discovery` |
| Preflight check | `ansible-playbook preflight_check.yml -i inventory.azure.yml` |
| Deploy new VMs | `./deploy_new_instances.sh -i inventory.azure.yml` |
| Dry run | `ansible-playbook site.yml -i inventory.azure.yml --check` |
| Full deploy | `./scripts/run.sh playbook site.yml -i inventory.azure.yml` |
| Deploy one zone | `ansible-playbook site.yml -i inventory.azure.yml --limit "az2"` |
| Throttled deploy | `ansible-playbook site.yml -i inventory.azure.yml -e hammerspace_serial=2` |
| Update NFS exports only | `ansible-playbook site.yml -i inventory.azure.yml --tags nfs-exports` |
| Retune perf only | `ansible-playbook site.yml -i inventory.azure.yml --tags perf` |
| Verify NFS | `ansible-playbook verify_nfs.yml -i inventory.azure.yml` |
| Verify resource disk survived | `ansible -i inventory.azure.yml storage_servers -m shell -a 'findmnt -n -o TARGET,SOURCE /mnt; cat /proc/mdstat' -b` |
| Check IMDS placement | `ansible -i inventory.azure.yml storage_servers -m shell -a 'curl -s -H Metadata:true "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01"' ` |
| Read instance report | `column -s, -t instance_report.csv` |
| Reset Tier 0 host | `ansible-playbook reset-tier0-host.yml -i inventory.azure.yml --limit "tier0-node-05" -e reset_confirm=true` |
| Cleanup (dry run) | `python3 cleanup_instance_nodes.py --host <IP> --user admin --password-file ~/.hs_password --contains "tier0" --dry-run` |

---

## Support

- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — platform-independent symptoms, causes and fixes
- [docs/INVENTORY_SETUP.md](docs/INVENTORY_SETUP.md#azure-dynamic-inventory) — Azure inventory reference
- [VARIABLE_REFERENCE.md](VARIABLE_REFERENCE.md) — every variable, including the Azure AZ chain
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — the OCI guide; DI and decommissioning sections apply here too
- [DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md](DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md) — VMs whose NVMe is already formatted and mounted
- Hammerspace support for cluster-side issues — attach the log from `~/logs/ansible-<timestamp>.log`
