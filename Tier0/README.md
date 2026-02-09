# Ansible Automation for Hammerspace Tier 0 / LSS Storage Setup

This Ansible project automates the setup of RAID arrays, filesystems, NFS exports, and firewall configuration for **Hammerspace Tier 0** and **Linux Storage Server (LSS)** deployments, based on the [Hammerspace Tier 0 Deployment Guide v1.0](https://hammerspace.com).

## What is Tier 0?

Tier 0 transforms existing local NVMe storage on GPU servers into ultra-fast, persistent shared storage managed by Hammerspace. It delivers data directly to GPUs at local NVMe speeds, reducing checkpoint times and improving AI workload performance by up to 10x compared to networked storage.

## Features

### Storage & NFS
- **Dynamic NVMe Discovery**: Automatically discovers NVMe drives and groups them by NUMA node for optimal performance
- **Boot Drive Exclusion**: Automatically detects and excludes the boot drive from RAID configuration
- **Comprehensive Health Checks**: Validates drive count, mount status, NUMA balance, 4K sector size, MTU connectivity, and package availability
- **4K Sector Size Detection**: Identifies drives not using recommended 4096-byte sectors with optional formatting
- **RAID Configuration**: Creates mdadm arrays with Hammerspace-recommended settings (power-of-2 sizes, NUMA-aware grouping)
- **UUID-Based Mounts**: Uses filesystem UUIDs in `/etc/fstab` for reliable boot persistence
- **mdadm Persistence**: Generates `/etc/mdadm.conf` with proper settings and updates initramfs
- **Filesystem Setup**: Creates XFS filesystems with `agcount=512` per Hammerspace recommendations
- **NFS Server Configuration**: Deploys `/etc/nfs.conf` with 128 threads, NFSv4.2, and optional RDMA support
- **Export Management**: Configures exports with proper `no_root_squash` for Hammerspace nodes and `root_squash` for clients
- **Firewall Setup**: Opens required ports for NFS, including RDMA port 20049
- **iptables Flush**: Automatically flushes iptables rules at playbook start to prevent connectivity issues
- **Mount Point Protection**: systemd guard services and auto-remount watchdog to prevent accidental unmounts

### Hammerspace Integration
- **Node Registration**: Automatically registers storage servers via Anvil REST API
- **Volume Management**: Adds storage volumes with configurable thresholds and protection settings
- **Task Queue Throttling**: Prevents API overload by monitoring queued tasks (configurable min/max thresholds)
- **Volume Groups**: Creates volume groups for organizing volumes by AZ or location
- **Share Management**: Creates shares with configurable export options
- **Share Objectives**: Applies availability/durability objectives to shares
- **AZ Mapping**: Parses availability zone from node names and applies labels

### S3/Object Storage
- **S3 Node Integration**: Add AWS S3 or S3-compatible storage nodes
- **Object Storage Volumes**: Add S3 buckets as Hammerspace volumes
- **S3 Server**: Create internal S3 server for S3 protocol access
- **S3 Users**: Create and manage S3 users for authentication

### Cluster Configuration
- **DNS Configuration**: Update cluster DNS servers and search domains
- **Active Directory**: Join Hammerspace cluster to Active Directory
- **Site Name**: Configure cluster site name
- **Physical Location**: Set datacenter, room, rack, and position metadata
- **Prometheus Monitoring**: Enable Prometheus exporters for metrics collection

### Platform Support
- **Multi-Distribution Support**: Works on Debian, Ubuntu, RHEL, Rocky Linux, CentOS
- **Firewall Auto-Detection**: Automatically detects firewalld, UFW, or iptables

### Multi-Cloud Auto-Discovery
- **AWS EC2**: Dynamic inventory via `amazon.aws.aws_ec2` plugin
- **GCP Compute Engine**: Dynamic inventory via `google.cloud.gcp_compute` plugin
- **OCI**: Dynamic inventory via `oracle.oci.oci` plugin
- **Preflight Check**: Compares cloud inventory with Hammerspace to find new instances
- **Incremental Deployment**: Deploy only to instances not yet registered in Hammerspace

## Directory Structure

```
ansible-storage-setup/
├── ansible.cfg              # Ansible configuration
├── inventory.yml            # Static server inventory (manual)
├── inventory.oci.yml        # OCI dynamic inventory (auto-discovery)
├── inventory.aws.yml        # AWS EC2 dynamic inventory (auto-discovery)
├── inventory.gcp.yml        # GCP Compute Engine dynamic inventory (auto-discovery)
├── site.yml                 # Main playbook
├── preflight_check.yml      # Compare inventory with Hammerspace (find new instances)
├── deploy_new_instances.sh  # Automated deployment script
├── verify_nfs.yml           # NFS verification playbook
├── collect_gpu_fabric.yml   # Collect GPU fabric data from instances
├── cleanup_instance_nodes.py # Remove nodes/volumes from Hammerspace
├── assign_az_to_volumes.py  # Assign AZ prefix based on GPU fabric
├── DEPLOYMENT_GUIDE.md      # Step-by-step deployment guide for OCI
├── vars/
│   └── main.yml             # Main variables (customize this!)
└── roles/
    ├── nvme_discovery/          # Dynamic NVMe discovery by NUMA node
    ├── precheck/                # Pre-setup validation
    ├── raid_setup/              # RAID configuration with mdadm.conf persistence
    ├── filesystem_setup/        # Filesystem creation with UUID-based fstab
    ├── nfs_setup/               # NFS server configuration
    ├── firewall_setup/          # Firewall configuration (firewalld/ufw/iptables)
    └── hammerspace_integration/ # Anvil API integration
        ├── tasks/
        │   ├── main.yml             # Main integration orchestration
        │   ├── add_node.yml         # Register storage node
        │   ├── add_volume.yml       # Add storage volumes
        │   ├── create_share.yml     # Create shares
        │   ├── task_queue_wait.yml  # API throttling
        │   ├── volume_group_create.yml  # Volume groups
        │   ├── az_map.yml           # AZ label mapping
        │   ├── share_apply_objective.yml  # Share objectives
        │   ├── s3/                  # S3/Object storage tasks
        │   │   ├── add_s3_node.yml
        │   │   ├── add_object_storage_volume.yml
        │   │   ├── create_s3_server.yml
        │   │   └── create_s3_user.yml
        │   └── cluster/             # Cluster configuration tasks
        │       ├── dns_update.yml
        │       ├── ad_join.yml
        │       ├── change_site_name.yml
        │       ├── set_location.yml
        │       └── prometheus_enable.yml
        └── defaults/main.yml    # Default variables
```

## Quick Start

### 1. Prerequisites

Install Ansible on your control machine (laptop, workstation, or bastion):

```bash
# macOS
brew install ansible

# Linux/pip
pip install ansible --break-system-packages

# Install required collections
ansible-galaxy collection install -r requirements.yml
```

### 2. Configure Inventory

You can use **static inventory** (manual) or **dynamic inventory** for auto-discovery from cloud providers:

| Inventory Type | File | Use Case |
|----------------|------|----------|
| Static (Manual) | `inventory.yml` | On-premises, manual server list |
| OCI Dynamic | `inventory.oci.yml` | Oracle Cloud Infrastructure |
| AWS Dynamic | `inventory.aws.yml` | Amazon Web Services EC2 |
| GCP Dynamic | `inventory.gcp.yml` | Google Cloud Platform Compute Engine |

#### Option A: Static Inventory (Manual)

Edit `inventory.yml` to add your Tier 0 / LSS servers:

```yaml
all:
  children:
    storage_servers:
      hosts:
        tier0-node-01:
          ansible_host: 10.200.100.101
        tier0-node-02:
          ansible_host: 10.200.100.102
```

**Running locally on target server** (no SSH needed):
```yaml
all:
  children:
    storage_servers:
      hosts:
        localhost:
          ansible_connection: local
```

#### Option B: OCI Dynamic Inventory (Recommended for OCI)

Auto-discover instances from Oracle Cloud Infrastructure.

**1. Install OCI CLI:**
```bash
# Interactive install (prompts for directories)
sudo bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"

# Non-interactive install (uses defaults)
sudo bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)" -- --accept-all-defaults

# Verify installation
oci --version
```

**2. Install OCI Ansible collection and Python SDK:**
```bash
# Install OCI Ansible collection
ansible-galaxy collection install oracle.oci

# Install OCI Python SDK
pip3 install oci
```

**3. Configure OCI authentication:**
```bash
# Create OCI config directory
mkdir -p ~/.oci

# Interactive setup (creates config file and API key)
oci setup config

# Or copy existing config from another machine
# scp user@source:~/.oci/config ~/.oci/
# scp user@source:~/.oci/oci_api_key.pem ~/.oci/
```

**4. Edit `inventory.oci.yml`:**

Note: The file MUST be named with `.oci.yml` extension for the OCI plugin to recognize it.

```yaml
---
plugin: oracle.oci.oci
regions:
  - us-sanjose-1  # Your region
fetch_hosts_from_subcompartments: true

hostname_format_preferences:
  - "display_name"  # Use instance name as inventory_hostname
  - "private_ip"

# Filter to only running instances with specific shape
include_filters:
  - lifecycle_state: "RUNNING"
    shape: "VM.DenseIO.E5.Flex"  # Or BM.GPU.GB200-v3.4

# Create storage_servers group
groups:
  storage_servers: "'VM.DenseIO.E5.Flex' in shape"

# Set connection variables and OCI metadata
compose:
  ansible_host: private_ip
  ansible_user: "'ubuntu'"  # Quoted string literal for Jinja2
  ansible_python_interpreter: "'/usr/bin/python3'"
  ansible_become: true
  # OCI metadata as host variables
  oci_fault_domain: fault_domain
  oci_availability_domain: availability_domain
  # CPU and shape details
  oci_ocpus: shape_config.ocpus | default('')
  oci_memory_gb: shape_config.memory_in_gbs | default('')
  oci_processor_description: shape_config.processor_description | default('')
  oci_networking_bandwidth_gbps: shape_config.networking_bandwidth_in_gbps | default('')
  oci_local_disks: shape_config.local_disks | default('')
  oci_local_disks_total_gb: shape_config.local_disks_total_size_in_gbs | default('')
  # Hammerspace AZ prefix (disabled by default, configure in vars/main.yml)
  # Uncomment to enable auto-detection from fault domain:
  # hammerspace_volume_az_prefix: fault_domain | regex_replace('FAULT-DOMAIN-', 'AZ') ~ ":"

# Create groups based on fault domain and availability domain
keyed_groups:
  - key: fault_domain
    prefix: fd
    separator: "_"
  - key: availability_domain
    prefix: ad
    separator: "_"
```

**5. Update `ansible.cfg` to use dynamic inventory and SSH key:**
```ini
[defaults]
inventory = inventory.oci.yml
private_key_file = /home/ubuntu/.ssh/ansible_admin_key
```

**6. Test the inventory:**
```bash
# List discovered hosts
ansible-inventory -i inventory.oci.yml --list

# Show as graph
ansible-inventory -i inventory.oci.yml --graph

# Ping all storage servers
ansible storage_servers -m ping
```

**Find your compartment OCID:**
```bash
oci iam compartment list --query "data[].{name:name, id:id}" --output table
```

#### Option C: AWS Dynamic Inventory (EC2)

Auto-discover instances from Amazon Web Services EC2.

**1. Install AWS Ansible collection and Python SDK:**
```bash
# Install AWS Ansible collection
ansible-galaxy collection install amazon.aws

# Install AWS Python SDK
pip3 install boto3 botocore
```

**2. Configure AWS authentication:**
```bash
# Option 1: AWS CLI configuration (recommended)
aws configure

# Option 2: Environment variables
export AWS_ACCESS_KEY_ID='your-access-key'
export AWS_SECRET_ACCESS_KEY='your-secret-key'
export AWS_REGION='us-west-2'

# Option 3: IAM instance role (when running on EC2)
# No configuration needed - uses instance metadata
```

**3. Edit `inventory.aws.yml`:**

Update the regions and filters to match your environment:

```yaml
plugin: amazon.aws.aws_ec2
regions:
  - us-west-2

# Filter to only running instances with specific tags
filters:
  instance-state-name: running
  tag:Role: storage      # Adjust to your tagging convention
  # instance-type:
  #   - p4d.24xlarge     # GPU instances

# Create storage_servers group
groups:
  storage_servers: "'tier0' in (tags.Role | default(''))"

compose:
  ansible_host: private_ip_address
  ansible_user: "'ubuntu'"
  # Hammerspace AZ prefix from availability zone
  hammerspace_volume_az_prefix: >-
    "AZ" ~ ((placement.availability_zone[-1:] | lower | ord) - 96) ~ ":"
```

**4. Update `ansible.cfg` to use AWS inventory:**
```ini
[defaults]
inventory = inventory.aws.yml
```

**5. Test the inventory:**
```bash
# List discovered hosts
ansible-inventory -i inventory.aws.yml --list

# Show as graph
ansible-inventory -i inventory.aws.yml --graph

# Ping all storage servers
ansible storage_servers -m ping
```

**AWS Instance Metadata Exposed:**

| Variable | Description | Example |
|----------|-------------|---------|
| `aws_instance_id` | EC2 instance ID | "i-0abc123def456" |
| `aws_instance_type` | Instance type | "p4d.24xlarge" |
| `aws_availability_zone` | Availability zone | "us-west-2a" |
| `aws_vpc_id` | VPC ID | "vpc-12345678" |
| `aws_private_ip` | Private IP address | "10.0.1.100" |

**AWS AZ Mapping:**

| AWS Availability Zone | Hammerspace AZ Prefix |
|-----------------------|----------------------|
| us-west-2a | `AZ1:` |
| us-west-2b | `AZ2:` |
| us-west-2c | `AZ3:` |

#### Option D: GCP Dynamic Inventory (Compute Engine)

Auto-discover instances from Google Cloud Platform Compute Engine.

**1. Install GCP Ansible collection and Python SDK:**
```bash
# Install GCP Ansible collection
ansible-galaxy collection install google.cloud

# Install GCP Python SDK
pip3 install google-auth requests
```

**2. Configure GCP authentication:**
```bash
# Option 1: Service account file (recommended for automation)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Option 2: Application default credentials (for local development)
gcloud auth application-default login

# Option 3: Compute Engine default service account (when running on GCE)
# No configuration needed - uses instance metadata
```

**3. Edit `inventory.gcp.yml`:**

**Important:** Update the `projects` field with your GCP project ID:

```yaml
plugin: google.cloud.gcp_compute
projects:
  - your-gcp-project-id    # REQUIRED: Replace with your project ID

zones:
  - us-central1-a
  - us-central1-b
  - us-central1-c

auth_kind: application

# Filter to only running instances
filters:
  - status = RUNNING
  # - labels.role = storage    # Filter by label

# Create storage_servers group
groups:
  storage_servers: "'tier0' in (labels.role | default(''))"

compose:
  ansible_host: networkInterfaces[0].networkIP
  ansible_user: "'ubuntu'"
  # Hammerspace AZ prefix from zone
  hammerspace_volume_az_prefix: >-
    "AZ" ~ (((zone | basename)[-1:] | lower | ord) - 96) ~ ":"
```

**4. Update `ansible.cfg` to use GCP inventory:**
```ini
[defaults]
inventory = inventory.gcp.yml
```

**5. Test the inventory:**
```bash
# List discovered hosts
ansible-inventory -i inventory.gcp.yml --list

# Show as graph
ansible-inventory -i inventory.gcp.yml --graph

# Ping all storage servers
ansible storage_servers -m ping
```

**GCP Instance Metadata Exposed:**

| Variable | Description | Example |
|----------|-------------|---------|
| `gcp_instance_name` | Instance name | "tier0-node-01" |
| `gcp_machine_type` | Machine type | "a2-highgpu-8g" |
| `gcp_zone` | Zone | "us-central1-a" |
| `gcp_region` | Region | "us-central1" |
| `gcp_private_ip` | Internal IP | "10.128.0.10" |

**GCP Zone Mapping:**

| GCP Zone | Hammerspace AZ Prefix |
|----------|----------------------|
| us-central1-a | `AZ1:` |
| us-central1-b | `AZ2:` |
| us-central1-c | `AZ3:` |

#### Option E: Multi-Cloud Inventory

Use multiple cloud providers simultaneously by specifying multiple inventory files:

**1. Update `ansible.cfg`:**
```ini
[defaults]
# Combine all cloud inventories
inventory = inventory.oci.yml,inventory.aws.yml,inventory.gcp.yml
```

**2. Or specify at runtime:**
```bash
# Use all inventories
ansible-playbook -i inventory.oci.yml -i inventory.aws.yml -i inventory.gcp.yml site.yml

# Target specific cloud
ansible-playbook -i inventory.aws.yml site.yml

# View combined inventory
ansible-inventory -i inventory.oci.yml -i inventory.aws.yml -i inventory.gcp.yml --graph
```

**3. Target hosts by cloud provider:**

Each inventory creates groups by availability zone/region:
```bash
# OCI: Target fault domain 1
ansible fd_FAULT_DOMAIN_1 -m ping

# AWS: Target us-west-2a
ansible az_us-west-2a -m ping

# GCP: Target us-central1-a zone
ansible zone_us-central1-a -m ping
```

#### Hammerspace Volume AZ Prefix Configuration

The AZ (Availability Zone) prefix in volume names is **optional** and controlled via `vars/main.yml`. When enabled, volumes are named with an AZ prefix for multi-zone deployments.

| Configuration | Volume Name Example |
|---------------|---------------------|
| Prefix disabled (default) | `instance-name::/hammerspace/hsvol0` |
| Prefix enabled (AZ1) | `AZ1:instance-name::/hammerspace/hsvol0` |
| Prefix enabled (custom) | `WEST:instance-name::/hammerspace/hsvol0` |

**Configuration Options in `vars/main.yml`:**

```yaml
# Option 1: Disable AZ prefix (default)
hammerspace_volume_az_prefix_enabled: false

# Option 2: Enable with static prefix for all nodes
hammerspace_volume_az_prefix_enabled: true
hammerspace_volume_az_prefix_mode: "AZ1:"

# Option 3: Enable with auto-detection from OCI fault domain
# FAULT-DOMAIN-1 -> FD1:, FAULT-DOMAIN-2 -> FD2:, etc.
hammerspace_volume_az_prefix_enabled: true
hammerspace_volume_az_prefix_mode: "auto"

# Option 4: Direct override (ignores above settings)
hammerspace_volume_az_prefix: "WEST:"
```

**OCI Fault Domain Auto-Mapping:**

When using `hammerspace_volume_az_prefix_mode: "auto"` with OCI dynamic inventory:

| OCI Fault Domain | Hammerspace AZ Prefix | Volume Name Example |
|------------------|----------------------|---------------------|
| FAULT-DOMAIN-1 | `AZ1:` | `AZ1:instance-name::/hammerspace/hsvol0` |
| FAULT-DOMAIN-2 | `AZ2:` | `AZ2:instance-name::/hammerspace/hsvol0` |
| FAULT-DOMAIN-3 | `AZ3:` | `AZ3:instance-name::/hammerspace/hsvol0` |

**AZ Label Mapping for Nodes:**

Separately from volume naming, you can apply availability-zone labels to nodes in Hammerspace:

```yaml
# Enable AZ label mapping on nodes
hammerspace_enable_az_mapping: true
hammerspace_apply_az_labels: true

# Optional: Default AZ for nodes without OCI fault domain
hammerspace_default_az: "default"

# Optional: Explicit AZ per host (overrides auto-detection)
hammerspace_node_az: "FD1"
```

AZ detection priority for node labels:
1. Explicit `hammerspace_node_az` variable
2. OCI fault domain (auto-detected from dynamic inventory)
3. AZ prefix in node name (legacy format `AZ1:node-name`)
4. Default AZ (`hammerspace_default_az`)

**Verify fault domain mapping:**
```bash
# Check fault domain for all hosts
ansible-inventory --list | grep -E "oci_fault_domain"

# Or for a specific host
ansible-inventory --host <instance-name>
```

**Dynamic groups by fault domain:**

The inventory also creates groups based on fault domain:
```
@fd_FAULT_DOMAIN_1:
  |--instance1
  |--instance2
@fd_FAULT_DOMAIN_2:
  |--instance3
```

This allows targeting specific fault domains:
```bash
ansible fd_FAULT_DOMAIN_1 -m ping
```

#### OCI Instance Details (CPU, Memory, Storage)

The inventory automatically exposes instance shape details as host variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `oci_processor_description` | CPU type | "AMD EPYC 9J14 96-Core Processor" |
| `oci_ocpus` | Number of OCPUs | 32 |
| `oci_memory_gb` | Memory in GB | 512 |
| `oci_networking_bandwidth_gbps` | Network bandwidth | 200 |
| `oci_local_disks` | Number of local NVMe disks | 8 |
| `oci_local_disks_total_gb` | Total local disk size in GB | 51200 |

**Query instance details:**
```bash
# List CPU info for all hosts
ansible-inventory --list | jq '._meta.hostvars | to_entries[] | {host: .key, cpu: .value.oci_processor_description, ocpus: .value.oci_ocpus}'

# Full details for specific host
ansible-inventory --host <instance-name>
```

**Use in playbooks:**
```yaml
- name: Display instance specs
  debug:
    msg: |
      Host: {{ inventory_hostname }}
      CPU: {{ oci_processor_description }}
      OCPUs: {{ oci_ocpus }}
      Memory: {{ oci_memory_gb }} GB
      Network: {{ oci_networking_bandwidth_gbps }} Gbps
      Local Disks: {{ oci_local_disks }} ({{ oci_local_disks_total_gb }} GB total)
```

### 3. Configure Variables

Edit `vars/main.yml` to match your environment:

#### Dynamic Discovery (Recommended)

```yaml
# Enable automatic NVMe discovery grouped by NUMA node
use_dynamic_discovery: true

# RAID level (0=stripe, 1=mirror, 5=parity, 10=stripe+mirror)
raid_level: 0

# Mount point base path
mount_base_path: /hammerspace
```

With dynamic discovery enabled, the playbook will:
1. Discover all NVMe devices on the system
2. Identify and exclude the boot drive automatically
3. Group remaining drives by NUMA node
4. Create one RAID array per NUMA node for optimal performance

#### Hammerspace API Integration (Optional)

```yaml
# Anvil management IP (enables automatic cluster registration)
hammerspace_api_host: "10.1.2.3"
hammerspace_api_user: "admin"
hammerspace_api_password: "your_password"

# Node naming (use AZ prefix for availability zones)
hammerspace_node_name: "AZ1:tier0-node01"
```

#### Manual Configuration (Alternative)

```yaml
use_dynamic_discovery: false

raid_arrays:
  - name: md0
    device: /dev/md0
    level: 0
    drives:
      - /dev/nvme0n1
      - /dev/nvme1n1
      - /dev/nvme2n1
      - /dev/nvme3n1

mount_points:
  - path: /hammerspace/hsvol0
    device: /dev/md0
    fstype: xfs
    label: hammerspace-hsvol0
    mount_opts: defaults,nofail,discard
```

#### NFS Export Settings

```yaml
# Hammerspace Node IPs (require no_root_squash)
hammerspace_nodes:
  - "10.1.2.3"  # Anvil cluster management floating IP

mover_nodes:
  - "10.1.2.10"  # DI/Mover node IPs

# Client Subnets (use root_squash)
client_subnets:
  - "10.200.104.0/24"
  - "10.200.105.0/24"
```

### 4. Run the Playbook

```bash
# Discovery and pre-checks only (see what will be configured)
ansible-playbook site.yml --tags discovery,precheck

# Dry run (check mode)
ansible-playbook site.yml --check

# Full deployment (storage + NFS + optional Hammerspace integration)
ansible-playbook site.yml

# Run specific components
ansible-playbook site.yml --tags raid
ansible-playbook site.yml --tags nfs
ansible-playbook site.yml --tags firewall
ansible-playbook site.yml --tags hammerspace
```

### Preflight Check - Deploy Only New Instances

The preflight check compares your cloud inventory with Hammerspace registered nodes, identifying which instances need deployment. This is the **recommended approach** for production environments.

#### Quick Start

```bash
# 1. Run preflight check (compares inventory with Hammerspace)
ansible-playbook preflight_check.yml -i inventory.aws.yml

# 2. Deploy only to new instances (using the generated limit)
ansible-playbook site.yml --limit @.new_instances_limit
```

#### Using the Automated Script

```bash
# Interactive mode - prompts for confirmation
./deploy_new_instances.sh

# Specify inventory file
./deploy_new_instances.sh -i inventory.aws.yml

# Dry run mode
./deploy_new_instances.sh --check

# Auto-deploy without confirmation (for CI/CD)
./deploy_new_instances.sh --auto

# Run precheck only on new instances
./deploy_new_instances.sh --precheck-only
```

#### Preflight Check Output

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

ALREADY REGISTERED (will be skipped)
--------------------------------------------------------------------------------
- instance-001
- instance-002
- instance-003
...

NEW INSTANCES (need deployment)
--------------------------------------------------------------------------------
- instance-008
- instance-009
- instance-010

================================================================================
RECOMMENDED COMMANDS
================================================================================
# Deploy to new instances only:
ansible-playbook site.yml --limit "instance-008,instance-009,instance-010"
================================================================================
```

#### Generated Files

| File | Description |
|------|-------------|
| `.new_instances_limit` | Comma-separated list of new instance names (for `--limit @file`) |
| `preflight_report.txt` | Full preflight check report |

#### Multi-Cloud Preflight

```bash
# Check against each cloud provider's inventory
ansible-playbook preflight_check.yml -i inventory.aws.yml
ansible-playbook preflight_check.yml -i inventory.gcp.yml
ansible-playbook preflight_check.yml -i inventory.oci.yml

# Or check all combined
ansible-playbook preflight_check.yml -i inventory.oci.yml -i inventory.aws.yml -i inventory.gcp.yml
```

### Targeting Specific Hosts

Use the `--limit` option to filter hosts by name pattern. This is useful for targeting new nodes without affecting existing ones.

```bash
# Filter by instance name prefix (e.g., only nodes created on 2026-01-27)
ansible-playbook site.yml --limit "instance20260127*"

# Target only existing nodes (e.g., created on 2026-01-21)
ansible-playbook site.yml --limit "instance20260121*"

# Multiple patterns (OR logic)
ansible-playbook site.yml --limit "instance20260127*:instance20260128*"

# Exclude pattern (all EXCEPT matching hosts)
ansible-playbook site.yml --limit "all:!instance20260121*"

# Combine with tags
ansible-playbook site.yml --tags discovery,precheck --limit "instance20260127*"

# Dry run on specific hosts
ansible-playbook site.yml --check --limit "instance20260127*"
```

**Common scenarios:**

| Scenario | Command |
|----------|---------|
| Deploy to new nodes only | `ansible-playbook site.yml --limit "instance20260127*"` |
| Precheck existing nodes | `ansible-playbook site.yml --tags precheck --limit "instance20260121*"` |
| Skip problematic host | `ansible-playbook site.yml --limit "all:!instance20260127011850"` |
| Single host test | `ansible-playbook site.yml --limit instance20260127011850` |

## Running Ansible

You have several options for running this playbook:

| Method | Command | Use Case |
|--------|---------|----------|
| From workstation | `ansible-playbook -i inventory.yml site.yml` | Direct SSH access to targets |
| Locally on target | `ansible-playbook -i localhost, -c local site.yml` | No SSH, run on storage server |
| Via bastion/jump host | Configure `ansible_ssh_common_args` in inventory | Servers behind firewall |

### Using a Bastion Host

```yaml
# inventory.yml
storage_servers:
  hosts:
    plsm221h-01:
      ansible_host: 10.200.104.10
  vars:
    ansible_ssh_common_args: '-o ProxyJump=user@bastion.example.com'
```

## Dynamic NVMe Discovery

When `use_dynamic_discovery: true`, the playbook automatically:

1. **Detects boot device**: Uses `findmnt` to identify which NVMe contains the root filesystem
2. **Discovers NVMe devices**: Scans `/sys/class/nvme/` for all NVMe controllers
3. **Applies exclusion rules**: Filters out boot device and user-specified exclusions
4. **Groups by NUMA node**: Reads `/sys/class/nvme/nvmeX/device/numa_node` for each device
5. **Creates RAID arrays**: One array per NUMA node for optimal memory locality

### NVMe Device Exclusion

Exclude specific NVMe devices from RAID configuration. The boot device is always excluded automatically.

```yaml
# vars/main.yml

# Option 1: Exclude by device name
nvme_exclude_devices:
  - nvme0n1
  - nvme1n1

# Option 2: Exclude by device path
nvme_exclude_paths:
  - /dev/nvme0n1
  - /dev/nvme1n1

# Option 3: Exclude by serial number (consistent across reboots)
nvme_exclude_serials:
  - "S5XXXX0123456789"

# Option 4: Exclude by model name (exclude all drives of specific model)
nvme_exclude_models:
  - "Samsung SSD 980 PRO"
  - "Intel Optane"

# Option 5: Exclude by NUMA node
nvme_exclude_numa_nodes:
  - 0    # Exclude all NVMe on NUMA node 0

# Option 6: Exclude by PCIe address
nvme_exclude_pcie_addresses:
  - "0000:03:00.0"
  - "0000:04:00.0"

# Option 7: Exclude by PCIe bus prefix
nvme_exclude_pcie_prefixes:
  - "0000:03"   # Exclude all on bus 03
  - "0000:e"    # Exclude all on buses e1, e2, e3, e4
```

**Example output with exclusions:**
```
NVMe Exclusion Summary:
============================================
Boot device (always excluded): nvme0
Excluded by PCIe prefix: 0000:0

Devices excluded from RAID:
  - /dev/nvme0n1 (Micron_7450_MTFDKBG1T9TFR, Serial: 242649B2918C, NUMA: 0, PCIe: 0000:03:00.0)
  - /dev/nvme1n1 (Micron_7450_MTFDKBG1T9TFR, Serial: 242649B28F32, NUMA: 0, PCIe: 0000:04:00.0)
============================================

NVMe DISCOVERY RESULTS
============================================
Device Details:
  /dev/nvme2n1: ScaleFlux CSDU7UVG76 (Serial: UF2433C2412H, NUMA: 1, PCIe: 0000:e1:00.0)
  /dev/nvme3n1: ScaleFlux CSDU7UVG76 (Serial: UF2433C2315H, NUMA: 1, PCIe: 0000:e2:00.0)
  ...
============================================
```

**Use cases:**
- Reserve NVMe for OS/swap space
- Exclude slower or smaller drives
- Separate storage tiers (fast vs capacity)
- Exclude drives for other applications

### RAID Array Sizing Options

Control how drives are grouped into RAID arrays:

```yaml
# vars/main.yml

# Maximum drives per RAID array (0 = unlimited)
raid_max_drives_per_array: 0    # Default: all drives per NUMA node
raid_max_drives_per_array: 4    # Split into arrays of 4 drives
raid_max_drives_per_array: 8    # Split into arrays of 8 drives

# Minimum drives required (skip if fewer)
raid_min_drives_per_array: 2

# Grouping strategy
raid_grouping_strategy: numa      # Group by NUMA node (default)
raid_grouping_strategy: single    # One RAID across all drives
raid_grouping_strategy: per_drive # No RAID, individual mounts

# Round down to power of 2 (recommended for RAID 0)
raid_power_of_2_drives: false
```

**Example: 16 drives, max 4 per array, NUMA strategy**
```
NUMA 0 (8 drives): md0 (4 drives), md1 (4 drives)
NUMA 1 (8 drives): md2 (4 drives), md3 (4 drives)
Result: 4 RAID arrays, 4 mount points
```

**Example: 16 drives, single strategy**
```
All drives: md0 (16 drives)
Result: 1 RAID array, 1 mount point
```

**Example: 8 drives, per_drive strategy**
```
No RAID: 8 individual XFS mounts
Result: 0 RAID arrays, 8 mount points
```

**Power of 2 alignment:**
```yaml
# With 6 drives and power_of_2 enabled:
raid_power_of_2_drives: true
# Uses 4 drives (nearest power of 2), 2 drives leftover
```

**Leftover drives handling:**
```yaml
# What to do with drives that don't fit in full arrays
raid_leftover_drives: skip       # Ignore leftovers (default)
raid_leftover_drives: separate   # Create smaller separate array
raid_leftover_drives: add_last   # Add to last array (uneven size)
raid_leftover_drives: individual # Mount individually (no RAID)
```

| Drives | Max/Array | Leftover Option | Result |
|--------|-----------|-----------------|--------|
| 7 | 4 | skip | 1 array (4 drives), 3 unused |
| 7 | 4 | separate | 2 arrays (4+3 drives) |
| 7 | 4 | add_last | 1 array (7 drives) |
| 7 | 4 | individual | 1 array (4 drives) + 3 mounts |

### Example Discovery Output

```
Boot device (excluded): nvme8

Devices by NUMA node:
NUMA 0: /dev/nvme4n1, /dev/nvme5n1, /dev/nvme6n1, /dev/nvme7n1
NUMA 1: /dev/nvme0n1, /dev/nvme1n1, /dev/nvme2n1, /dev/nvme3n1

DYNAMIC RAID CONFIGURATION:
md0 (/dev/md0):
  NUMA Node: 0
  RAID Level: 0
  Drives (4): /dev/nvme4n1, /dev/nvme5n1, /dev/nvme6n1, /dev/nvme7n1

md1 (/dev/md1):
  NUMA Node: 1
  RAID Level: 0
  Drives (4): /dev/nvme0n1, /dev/nvme1n1, /dev/nvme2n1, /dev/nvme3n1
```

## CPU-Optimized RAID Configuration

When `cpu_optimized_raid: true` (default), the playbook automatically detects the CPU vendor and applies best-practice RAID settings for optimal NVMe performance.

### Supported CPU Profiles

**x86_64 Processors:**

| CPU | Profile | Chunk Size | Queue Depth | I/O Scheduler |
|-----|---------|------------|-------------|---------------|
| AMD EPYC Genoa (9004) | `amd_epyc_genoa` | 512KB | 1024 | none |
| AMD EPYC Milan/Rome | `amd_epyc` | 512KB | 512 | none |
| Intel Xeon Sapphire Rapids | `intel_xeon_sapphire` | 256KB | 512 | none |
| Intel Xeon (other) | `intel_xeon` | 256KB | 256 | mq-deadline |

**ARM (aarch64) Processors:**

| CPU | Profile | Chunk Size | Queue Depth | I/O Scheduler |
|-----|---------|------------|-------------|---------------|
| ARM Neoverse V2 (Graviton4) | `arm_neoverse_v2` | 512KB | 1024 | none |
| NVIDIA Grace | `arm_nvidia_grace` | 512KB | 1024 | none |
| Ampere Altra/Altra Max | `arm_ampere_altra` | 512KB | 1024 | none |
| AWS Graviton3 | `arm_graviton3` | 512KB | 512 | none |
| AWS Graviton2 | `arm_graviton2` | 512KB | 512 | none |
| ARM Generic | `arm_generic` | 512KB | 512 | none |

**Fallback:**

| CPU | Profile | Chunk Size | Queue Depth | I/O Scheduler |
|-----|---------|------------|-------------|---------------|
| Generic | `generic` | 512KB | 256 | none |

### Configuration

```yaml
# vars/main.yml

# Enable CPU-based auto-tuning (default: true)
cpu_optimized_raid: true

# Manual override: force specific profile
# cpu_vendor_profile: amd_epyc_genoa

# Manual override: specific settings (takes precedence over auto-detection)
# raid_chunk_size: 512      # KB
# nvme_queue_depth: 1024
# nvme_io_scheduler: none   # none, mq-deadline, kyber
```

### Example Output

```
CPU Detection Results:
============================================
Model: AMD EPYC 9J14 96-Core Processor
Vendor: AuthenticAMD
Cores: 192
NUMA Nodes: 2
Detected Profile: amd_epyc_genoa
OCI Processor: AMD EPYC 9J14 96-Core Processor
============================================

CPU-Optimized RAID Configuration:
============================================
AMD EPYC Genoa (9004 Series) Best Practices:
- RAID chunk size: 512KB (optimal for large sequential I/O)
- NUMA-aware RAID: One array per NUMA node
- XFS agcount: 512 (Hammerspace recommendation)
- I/O scheduler: none (NVMe native multiqueue)
- Queue depth: 1024 (high parallelism for many cores)

Applied Settings:
  RAID Chunk Size: 512KB
  NVMe Queue Depth: 1024
  I/O Scheduler: none
  XFS agcount: 512
============================================
```

### Best Practices Applied

**AMD EPYC Genoa (9004 Series):**
- 512KB chunk size for large sequential I/O (AI/ML workloads)
- High queue depth (1024) to leverage many cores
- NUMA-aware RAID grouping for memory locality
- Native NVMe multiqueue (no I/O scheduler overhead)

**Intel Xeon Sapphire Rapids:**
- 256KB chunk size for balanced mixed workloads
- Optimized queue depth for Intel architecture
- Support for Intel Volume Management Device (VMD) if available

**NVIDIA Grace:**
- 512KB chunk size optimized for GPU workloads
- High queue depth (1024) for 72-core processor
- NUMA-aware RAID aligned with NVLink/PCIe topology
- Optimized for DGX/HGX GPU systems

**Ampere Altra/Altra Max:**
- 512KB chunk size for high-bandwidth workloads
- High queue depth (1024) for 128 cores per socket
- NUMA-aware RAID for multi-socket configurations
- Native NVMe multiqueue

**AWS Graviton3/4:**
- 512KB chunk size for cloud-native workloads
- Queue depth tuned for Graviton architecture
- Optimized for EC2 NVMe instance storage

## Pre-Setup Validation (Health Checks)

The `precheck` role performs comprehensive environment validation per Hammerspace Tier 0 Deployment Guide recommendations. Run with `--tags precheck` to validate before deployment.

### Health Checks Performed

| Check | Description | Configurable |
|-------|-------------|--------------|
| **NVMe Drive Count** | Validates expected number of drives present | `expected_nvme_count`, `enforce_drive_count` |
| **Drive Status** | Ensures drives aren't already mounted/in RAID/LVM | `fail_on_drives_in_use` |
| **NUMA Balance** | Warns if drives are unevenly distributed across NUMA nodes | `warn_on_numa_imbalance` |
| **4K Sector Size** | Checks if drives use recommended 4096-byte sectors | `expected_sector_size`, `require_4k_sectors` |
| **MTU / Jumbo Frames** | Tests network connectivity with jumbo frames | `expected_mtu`, `network_test_targets` |
| **iperf Bandwidth** | Tests network bandwidth to Hammerspace nodes (iperf3 or iperf) | `iperf_test_enabled`, `iperf_version`, `iperf_test_targets` |
| **Package Availability** | Checks for mdadm, xfsprogs, nvme-cli, etc. | `fail_on_missing_packages` |

### Configuration Options

```yaml
# vars/main.yml

# --- NVMe Drive Count ---
expected_nvme_count: 9        # Expected drives (including boot)
enforce_drive_count: false    # Set true to fail on mismatch

# --- Drive Status ---
fail_on_drives_in_use: true   # Fail if drives already mounted

# --- NUMA Balance ---
warn_on_numa_imbalance: true  # Warn on imbalanced NUMA nodes

# --- 4K Sector Size (per Tier 0 Guide Page 14) ---
expected_sector_size: 4096    # Recommended by Hammerspace
require_4k_sectors: false     # Set true to enforce

# --- MTU Testing ---
expected_mtu: 9000
network_test_targets:
  - "10.1.2.3"                # Anvil IP
  - "10.200.100.101"          # Other Tier 0 nodes
enforce_mtu_test: false       # Set true to fail on MTU issues
```

### iperf Bandwidth Test (Optional)

Test network bandwidth between Tier 0 instances and Hammerspace nodes. Supports both `iperf3` (recommended) and `iperf` (legacy).

**Version Comparison:**

| Feature | iperf3 (recommended) | iperf (legacy) |
|---------|---------------------|----------------|
| Default port | 5201 | 5001 |
| Output format | JSON (reliable parsing) | Text (regex parsing) |
| Package name | iperf3 | iperf |
| Server command | `iperf3 -s -D` | `iperf -s -D` |

**Prerequisites:** Start iperf server on target Hammerspace nodes:
```bash
# For iperf3 (recommended)
iperf3 -s -D   # runs as daemon on port 5201

# For iperf (legacy)
iperf -s -D    # runs as daemon on port 5001
```

**Configuration:**
```yaml
# vars/main.yml

# Enable iperf bandwidth testing
iperf_test_enabled: true

# Choose iperf version: "iperf3" (recommended) or "iperf" (legacy)
iperf_version: "iperf3"

# iperf server targets (nodes running iperf server)
iperf_test_targets:
  - "10.0.13.213"    # Anvil node
  - "10.0.0.93"      # DSX/Mover node

# Test parameters (tuned for 200Gbps+ networks)
iperf_test_duration: 10          # Test duration in seconds
iperf_test_parallel: 16          # Parallel streams (16+ for 200Gbps)
iperf_min_bandwidth_mbps: 180000 # 90% of 200Gbps (Mbits/sec)
iperf_enforce_bandwidth: false   # Set true to fail if below minimum
```

**Example output (200Gbps network):**
```
IPERF3 Bandwidth Test Results:
============================================
Test parameters:
  Tool: iperf3 (port 5201)
  Duration: 10 seconds
  Parallel streams: 16
  Minimum expected: 180000 Mbits/sec

Results:
  10.0.13.213:
    Status: OK
    Bandwidth: 194320.45 Mbits/sec [OK]
  10.0.0.93:
    Status: OK
    Bandwidth: 191280.32 Mbits/sec [OK]
============================================
```

**Troubleshooting:**
- `Could not connect to iperf server` - Ensure server is running:
  - iperf3: `iperf3 -s -D`
  - iperf: `iperf -s -D`
- Low bandwidth - Check MTU settings, switch configuration, and NIC settings
- Connection refused - Check firewall allows the correct port:
  - iperf3: port 5201
  - iperf: port 5001

### 4K NVMe Sector Formatting

Per Hammerspace Tier 0 Deployment Guide (Page 14), NVMe drives should use 4096-byte sectors for optimal performance. The precheck role can optionally format drives:

```yaml
# Enable 4K formatting (DESTRUCTIVE!)
format_nvme_to_4k: true
nvme_format_confirm: "YES_I_UNDERSTAND_THIS_IS_DESTRUCTIVE"
```

**Warning**: This erases all data on the drives. Only use on new deployments.

### Example Precheck Output

```
PRE-SETUP VALIDATION SUMMARY
============================================
NVMe Drives:
  - Total found: 9
  - Expected: 9
  - In use (non-boot): 0
  - Boot device: nvme8

NUMA Balance:
  - NUMA 0: 4 drives
  - NUMA 1: 4 drives
  - Status: BALANCED

Sector Size:
  - Expected: 4096 bytes
  - Drives needing format: 0

Network:
  - MTU tests: 2/2 passed

Packages:
  - Missing: none
============================================
```

## Hammerspace API Integration

The playbook can automatically register the storage server with a Hammerspace cluster via the Anvil REST API. This eliminates the need for manual CLI commands.

Reference: [Hammerspace Ansible Examples](https://github.com/hammer-space/ansible)

### Enable API Integration

Add these variables to `vars/main.yml`:

```yaml
# Anvil management IP (required to enable integration)
hammerspace_api_host: "10.1.2.3"

# API credentials (admin role required)
hammerspace_api_user: "admin"
hammerspace_api_password: "your_secure_password"

# Skip SSL validation for self-signed certificates
hammerspace_api_validate_certs: false

# Node name (use AZ prefix for availability zones)
hammerspace_node_name: "AZ1:tier0-node01"
```

### Volume Settings

Configure volume thresholds and protection settings per Tier 0 Deployment Guide:

```yaml
# Threshold settings (values are decimals, e.g., 0.98 = 98%)
hammerspace_volume_high_threshold: 0.98    # utilizationThreshold - triggers evacuation
hammerspace_volume_low_threshold: 0.90     # utilizationEvacuationThreshold - target after evacuation

# Protection settings
hammerspace_volume_online_delay: 0         # --max-suspected-time (seconds)
hammerspace_volume_unavailable_multiplier: 1  # 0=--availability-drop-disabled, 1=--availability-drop-enabled
hammerspace_volume_availability: 2         # target availability level
hammerspace_volume_durability: 3           # target durability level
```

| Setting | CLI Equivalent | Description |
|---------|----------------|-------------|
| `hammerspace_volume_high_threshold` | `--high-threshold` | Utilization % that triggers data evacuation |
| `hammerspace_volume_low_threshold` | `--low-threshold` | Target utilization % after evacuation |
| `hammerspace_volume_online_delay` | `--max-suspected-time` | Seconds before volume goes suspected |
| `hammerspace_volume_unavailable_multiplier` | `--availability-drop-*` | 0=disabled, 1=enabled |

### What Gets Automated

When `hammerspace_api_host` is defined, the playbook will:

| Operation | API Endpoint | Description |
|-----------|--------------|-------------|
| Add Storage System | `POST /mgmt/v1.2/rest/nodes` | Registers server as type "OTHER" (NFS) |
| Add Storage Volumes | `POST /mgmt/v1.2/rest/storage-volumes` | Adds each mount point as a volume |
| Create Shares | `POST /mgmt/v1.2/rest/shares` | Optional: Creates Hammerspace shares |

### API Integration Output

```
HAMMERSPACE INTEGRATION COMPLETE
============================================
Anvil API: 10.1.2.3
Storage System: AZ1:tier0-node01
Volumes Added: 2
  - AZ1:tier0-node01::/hammerspace/hsvol0
  - AZ1:tier0-node01::/hammerspace/hsvol1

Verify in Hammerspace GUI or CLI:
  anvil> node-list
  anvil> storage-volume-list
============================================
```

### Creating Shares via API

To automatically create Hammerspace shares:

```yaml
hammerspace_create_shares: true

hammerspace_shares:
  - name: checkpoints
    path: /checkpoints
    export_options:
      - subnet: "10.200.104.0/24"
        accessPermissions: "RW"
        rootSquash: true

  - name: models
    path: /models
    export_options:
      - subnet: "*"
        accessPermissions: "RW"
        rootSquash: false
```

### Run Integration Only

If storage is already set up and you just need to register with Hammerspace:

```bash
ansible-playbook site.yml --tags hammerspace
```

## Persistence Configuration

### mdadm.conf

The playbook generates `/etc/mdadm.conf` with:

```conf
# mdadm.conf - Generated by Ansible
MAILADDR root
AUTO +all

ARRAY /dev/md0 metadata=1.2 UUID=abc123... name=server:md0
ARRAY /dev/md1 metadata=1.2 UUID=def456... name=server:md1
```

- Creates symlink `/etc/mdadm.conf` -> `/etc/mdadm/mdadm.conf` for RedHat compatibility
- Enables `mdmonitor` service for RAID health monitoring
- Updates initramfs to include RAID configuration

### fstab (UUID-based)

The playbook uses filesystem UUIDs in `/etc/fstab`:

```
UUID=a1b2c3d4-e5f6-7890-abcd-ef1234567890  /hammerspace/hsvol0  xfs  defaults,nofail,discard  0 0
UUID=f9e8d7c6-b5a4-3210-fedc-ba0987654321  /hammerspace/hsvol1  xfs  defaults,nofail,discard  0 0
```

UUID-based mounts ensure reliability even if device names change across reboots.

## Mount Point Protection

The playbook can deploy systemd-based mount protection to prevent accidental unmounting and ensure mounts automatically recover. Based on Hammerspace engineering guide "Protecting Linux Mount Points with systemd".

### Features

| Feature | Description |
|---------|-------------|
| **Boot Safety** | `nofail` and `x-systemd.automount` options ensure system boots even if storage is unavailable |
| **Guard Services** | Keeps a process with cwd on each mount point, preventing `umount` |
| **Auto-Remount Watchdog** | Timer checks every minute and remounts if accidentally unmounted |
| **RefuseManualStop** | Guard services cannot be stopped via `systemctl stop` |

### Enable Mount Protection

Add to `vars/main.yml`:

```yaml
# Enable all mount protection features
hammerspace_mount_protection: true

# Individual feature toggles (all default to true when protection is enabled)
hammerspace_mount_guard_enabled: true      # Guard services (busy-lock)
hammerspace_remount_watchdog_enabled: true # Auto-remount timer
hammerspace_remount_watchdog_interval: "1min"  # Check frequency
hammerspace_automount_timeout: 10          # Device timeout in seconds
```

### What Gets Deployed

When `hammerspace_mount_protection: true`:

**fstab options** (added automatically):
```
UUID=xxx  /hammerspace/hsvol0  xfs  defaults,nofail,x-systemd.automount,x-systemd.device-timeout=10  0 0
```

**systemd units**:
```
/etc/systemd/system/
├── hammerspace-guards.target           # Target for all guard services
├── hammerspace-hsvol0-guard.service    # Guard service per mount
├── hammerspace-hsvol1-guard.service
├── hammerspace-remount.service         # Remount check service
└── hammerspace-remount.timer           # Watchdog timer (runs every 1min)

/usr/local/bin/
└── hammerspace-remount-check.sh        # Script to check and remount
```

### How Guard Services Work

Each guard service runs `sleep infinity` with its working directory set to the mount point:

```ini
[Service]
ExecStart=/bin/bash -lc 'cd /hammerspace/hsvol0 && exec sleep infinity'
RefuseManualStop=yes
```

This makes the mount "busy" - attempting to unmount will fail:
```bash
$ umount /hammerspace/hsvol0
umount: /hammerspace/hsvol0: target is busy.
```

### Managing Protected Mounts

To intentionally unmount a protected mount point:

```bash
# 1. Stop the guard service (requires killing the process)
systemctl kill hammerspace-hsvol0-guard.service

# 2. Now unmount is possible
umount /hammerspace/hsvol0
```

To check protection status:
```bash
# View all guard services
systemctl list-units 'hammerspace-*-guard.service'

# Check watchdog timer
systemctl status hammerspace-remount.timer

# View recent remount activity
journalctl -t hammerspace-remount
```

## Hammerspace-Specific Configuration

### NFS Settings (per Tier 0 Deployment Guide)

The playbook configures `/etc/nfs.conf` with:

```ini
[nfsd]
threads=128
vers3=y
vers4.0=n
vers4.1=n
vers4.2=y
rdma=y          # If RDMA enabled
rdma-port=20049
```

### Export Options

Per Hammerspace recommendations:

| Client Type | Export Options |
|-------------|----------------|
| Hammerspace nodes (Anvil, DSX, Movers) | `rw,no_root_squash,sync,secure,mp,no_subtree_check` |
| Tier 0 / LSS clients | `rw,root_squash,sync,secure,mp,no_subtree_check` |

The `mp` (mountpoint) option prevents accidentally exporting empty directories if a filesystem isn't mounted.

### XFS Filesystem Options

```bash
mkfs.xfs -d agcount=512 -L <label> <device>
```

## Manual Hammerspace Integration

If not using API integration, you can manually register after running the playbook:

### 1. Add Node to Cluster

```bash
# From Hammerspace Anvil CLI
anvil> node-add --type OTHER --name AZ1:tier0-node01 --ip 10.200.100.101 --create-placement-objectives
```

### 2. Add Volumes

```bash
# Add each export as a volume (use AZ prefix for availability zones)
anvil> volume-add --name AZ1:tier0-node01::/hsvol0 \
  --node-name AZ1:tier0-node01 \
  --access-type read_write \
  --logical-volume-name /hsvol0 \
  --low-threshold 90 \
  --high-threshold 95 \
  --skip-performance-test
```

### 3. Create Shares

```bash
anvil> share-create --name checkpoints --path /checkpoints \
  --export-option "10.200.104.0/24,rw,root-squash"
```

### 4. Mount on Clients

```bash
mkdir /mnt/checkpoints
mount -o vers=4.2,nconnect=8,noatime <anvil_IP>:/checkpoints /mnt/checkpoints
```

## Availability Zones

For data protection, use the `AZx:` prefix naming convention:

```yaml
# Node and volume names should be prefixed with availability zone
hammerspace_node_name: "AZ1:tier0-node01"

# Results in volumes like:
# AZ1:tier0-node01::/hammerspace/hsvol0
# AZ1:tier0-node01::/hammerspace/hsvol1
```

Hammerspace recommends:
- Minimum 4 AZs when data is stored only on Tier 0
- 6 AZs recommended for optimal redundancy
- Symmetric design (same number of nodes/volumes per AZ)

## Troubleshooting

### Volume Goes "Suspected"

Common causes:
- Node was rebuilt
- NVMe reformatted or remounted to different path
- `mp` export option missing (exports empty directory)

Check:
```bash
# Verify exports
exportfs -v

# Check mount points
mount | grep hsvol

# Verify Hammerspace comb structure exists
ls -la /hsvol0/PrimaryData/
```

### RAID Array Not Assembling at Boot

```bash
# Check mdadm.conf exists and has correct UUIDs
cat /etc/mdadm.conf

# Verify arrays are defined
mdadm --detail --scan

# Regenerate initramfs
dracut --force        # RHEL/Rocky
update-initramfs -u   # Debian/Ubuntu
```

### Hammerspace API Errors

```bash
# Test API connectivity
curl -k -u admin:password https://10.1.2.3:8443/mgmt/v1.2/rest/nodes

# Check if node exists
curl -k -u admin:password https://10.1.2.3:8443/mgmt/v1.2/rest/nodes/AZ1%3Atier0-node01

# View API task status
curl -k -u admin:password https://10.1.2.3:8443/mgmt/v1.2/rest/tasks
```

### Mobility Failures

If file instances aren't being placed correctly:
- Verify DI/Mover nodes have `no_root_squash` access to all exports
- Check mover status in Hammerspace GUI
- Verify firewall ports 9095/9096 are open for DI nodes

## Requirements

- Ansible 2.9+
- Target servers running Debian/Ubuntu or RHEL/Rocky/CentOS
- SSH access with sudo privileges (or run locally with `ansible_connection: local`)
- Required collections: `ansible.posix`, `community.general`
- For API integration: Network access to Anvil management IP on port 8443

### Cloud Provider Requirements

| Provider | Collection | Python Dependencies | Authentication |
|----------|------------|---------------------|----------------|
| OCI | `oracle.oci` | `oci` | OCI CLI config (`~/.oci/config`) |
| AWS | `amazon.aws` | `boto3`, `botocore` | AWS credentials (`~/.aws/credentials` or env vars) |
| GCP | `google.cloud` | `google-auth`, `requests` | Service account or `gcloud auth` |

**Install all collections:**
```bash
ansible-galaxy collection install -r requirements.yml
```

**Install Python dependencies:**
```bash
# For OCI
pip3 install oci

# For AWS
pip3 install boto3 botocore

# For GCP
pip3 install google-auth requests
```

## Utility Scripts

### cleanup_instance_nodes.py

Removes nodes and their volumes from Hammerspace. Use before terminating instances.

```bash
# List all nodes
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin --password 'xxx' --list-nodes

# Delete nodes containing "bu-test" (dry run first)
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin --password 'xxx' \
  --contains "bu-test" --dry-run

# Delete specific nodes
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin --password 'xxx' \
  --node bu-test-01 --node bu-test-02

# Filter options: --prefix, --contains, --pattern (regex), --node (specific names)
```

### assign_az_to_volumes.py

Assigns AZ prefixes to Hammerspace volumes based on OCI GPU memory fabric.

```bash
# Collect GPU fabric data first
ansible-playbook collect_gpu_fabric.yml -i inventory.oci.yml

# Assign AZ prefixes (dry run)
python3 assign_az_to_volumes.py --host <ANVIL_IP> --user admin --password 'xxx' \
  --gpu-fabric-file gpu_fabric_data.txt --dry-run

# Apply AZ prefixes
python3 assign_az_to_volumes.py --host <ANVIL_IP> --user admin --password 'xxx' \
  --gpu-fabric-file gpu_fabric_data.txt
```

### collect_gpu_fabric.yml

Collects GPU memory fabric OCIDs from OCI instances for AZ mapping.

```bash
ansible-playbook collect_gpu_fabric.yml -i inventory.oci.yml
# Output: gpu_fabric_data.txt
```

## Customer Deployment Guide

For step-by-step deployment instructions, see **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)**.

## References

- [Hammerspace Tier 0 Deployment Guide](https://hammerspace.com)
- [Hammerspace Ansible Examples](https://github.com/hammer-space/ansible)
- [Hammerspace Objectives Guide](https://hammerspace.com)
- [Hammerspace Toolkit (HSTK)](https://github.com/hammer-space/hstk)
- [Hammerspace Grafana Dashboards](https://github.com/hammer-space/grafana-dashboards)

## License

MIT