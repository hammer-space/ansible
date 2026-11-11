# Inventory Setup

**Who this is for:** anyone deploying Tier 0 who needs to tell Ansible *which servers* to configure.
**Time:** 5 minutes for a static list, 15–20 minutes for cloud auto-discovery.

An *inventory* is the list of servers Ansible will configure. You can write the
list by hand (static inventory) or let Ansible discover the servers from your
cloud provider (dynamic inventory).

| If your servers are… | Use this file | Jump to |
|---|---|---|
| On-premises, or you want to list them yourself | `inventory.yml` | [Static inventory](#static-inventory) |
| Oracle Cloud (OCI) | `inventory.oci.yml` | [OCI](#oci-dynamic-inventory) |
| Amazon Web Services (EC2) | `inventory.aws.yml` | [AWS](#aws-dynamic-inventory) |
| Google Cloud (Compute Engine) | `inventory.gcp.yml` | [GCP](#gcp-dynamic-inventory) |
| Microsoft Azure | `inventory.azure.yml` | [Azure](#azure-dynamic-inventory) |
| Spread across several clouds | several files at once | [Multi-cloud](#multi-cloud-inventory) |
| On-premises, pre-grouped by availability zone | `inventory.az.yml` | [Static inventory](#static-inventory) (note: **not** Azure — "az" here means availability zone) |

Whichever you choose, the playbook only ever touches hosts in the
**`storage_servers`** group (and, optionally, `di_nodes`). Everything else in
the inventory is ignored.

> **Check before you deploy.** Whatever lands in `storage_servers` gets its
> data disks RAIDed and formatted. Always run
> `ansible-inventory -i <file> --graph` and read the list before the first run.

---

## Static inventory

Edit `inventory.yml` and list your Tier 0 servers:

```yaml
all:
  children:
    storage_servers:
      hosts:
        tier0-node-01:
          ansible_host: 10.200.100.101
        tier0-node-02:
          ansible_host: 10.200.100.102
      vars:
        ansible_user: ubuntu                      # SSH user on the servers
        ansible_ssh_private_key_file: ~/.ssh/id_ed25519
```

**Servers in several availability zones (on-prem multi-AZ)**

Nest hosts under groups named `AZ1`, `AZ2`, `AZ3`, … The playbook recognises
these names and uses them as the volume-name prefix automatically (turn it on
with `hammerspace_volume_az_prefix_enabled: true` in `vars/main.yml`):

```yaml
all:
  children:
    storage_servers:
      children:
        AZ1:
          hosts:
            node101: { ansible_host: 10.200.101.216 }
            node102: { ansible_host: 10.200.101.182 }
        AZ2:
          hosts:
            node201: { ansible_host: 10.200.103.188 }
            node202: { ansible_host: 10.200.103.228 }
        AZ3:
          hosts:
            node301: { ansible_host: 10.200.100.135 }
            node302: { ansible_host: 10.200.103.24 }
```

This produces volume names like `AZ1:node101::/hammerspace/hsvol0`. No
per-host variables are needed.

**Running Ansible directly on the storage server (no SSH)**

```yaml
all:
  children:
    storage_servers:
      hosts:
        localhost:
          ansible_connection: local
```

**Servers behind a bastion / jump host**

```yaml
storage_servers:
  hosts:
    tier0-node-01:
      ansible_host: 10.200.104.10
  vars:
    ansible_ssh_common_args: '-o ProxyJump=user@bastion.example.com'
```

**Test it**

```bash
ansible-inventory -i inventory.yml --graph      # shows the group tree
ansible -i inventory.yml storage_servers -m ping # every host should answer "pong"
```

---

## OCI dynamic inventory

Discovers instances from Oracle Cloud Infrastructure.

**1. Install the OCI CLI**

```bash
sudo bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)" -- --accept-all-defaults
oci --version
```

**2. Install the OCI Ansible collection and Python SDK**

```bash
ansible-galaxy collection install oracle.oci
pip3 install oci
```

**3. Configure OCI authentication**

```bash
mkdir -p ~/.oci
oci setup config          # interactive: user OCID, tenancy OCID, region, API key
```

Or copy an existing `~/.oci/config` and `~/.oci/oci_api_key.pem` from another
machine.

**4. Edit `inventory.oci.yml`**

The file **must** keep the `.oci.yml` suffix — the plugin only recognises that
name.

```yaml
---
plugin: oracle.oci.oci
regions:
  - us-sanjose-1                    # <-- your region
fetch_hosts_from_subcompartments: true

hostname_format_preferences:
  - "display_name"                  # instance name becomes the inventory hostname
  - "private_ip"

# Only running instances of the shape you deploy Tier 0 on
include_filters:
  - lifecycle_state: "RUNNING"
    shape: "BM.GPU.GB200-v3.4"      # <-- your shape

groups:
  storage_servers: "'BM.GPU.GB200-v3.4' in shape"

compose:
  ansible_host: private_ip
  ansible_user: "'ubuntu'"          # quoted twice on purpose (Jinja string literal)
  ansible_python_interpreter: "'/usr/bin/python3'"
  ansible_become: true
  oci_fault_domain: fault_domain
  oci_availability_domain: availability_domain
  oci_ocpus: shape_config.ocpus | default('')
  oci_memory_gb: shape_config.memory_in_gbs | default('')
  oci_processor_description: shape_config.processor_description | default('')
  oci_networking_bandwidth_gbps: shape_config.networking_bandwidth_in_gbps | default('')
  oci_local_disks: shape_config.local_disks | default('')
  oci_local_disks_total_gb: shape_config.local_disks_total_size_in_gbs | default('')
  # Uncomment to derive the volume AZ prefix from the fault domain:
  # hammerspace_volume_az_prefix: fault_domain | regex_replace('FAULT-DOMAIN-', 'AZ') ~ ":"

keyed_groups:
  - key: fault_domain
    prefix: fd
    separator: "_"
  - key: availability_domain
    prefix: ad
    separator: "_"
```

Find your compartment OCID with:

```bash
oci iam compartment list --query "data[].{name:name, id:id}" --output table
```

**5. Point `ansible.cfg` at it**

```ini
[defaults]
inventory = inventory.oci.yml
```

Supply the SSH key through `~/.ssh/config`, `ssh-agent`, or
`ANSIBLE_PRIVATE_KEY_FILE` rather than hard-coding a path in `ansible.cfg`.

**6. Test**

```bash
ansible-inventory -i inventory.oci.yml --graph
ansible -i inventory.oci.yml storage_servers -m ping
```

**Extra host variables OCI provides**

| Variable | Example |
|---|---|
| `oci_fault_domain` | `FAULT-DOMAIN-1` |
| `oci_availability_domain` | `xxxx:US-SANJOSE-1-AD-1` |
| `oci_processor_description` | `AMD EPYC 9J14 96-Core Processor` |
| `oci_ocpus` | `32` |
| `oci_memory_gb` | `512` |
| `oci_networking_bandwidth_gbps` | `200` |
| `oci_local_disks` | `8` |
| `oci_local_disks_total_gb` | `51200` |

```bash
ansible-inventory --host <instance-name>          # everything known about one host
ansible-inventory --list | grep oci_fault_domain  # fault domain of every host
ansible fd_FAULT_DOMAIN_1 -m ping                 # target one fault domain
```

---

## AWS dynamic inventory

Discovers EC2 instances.

**1. Install the collection and SDK**

```bash
ansible-galaxy collection install amazon.aws
pip3 install boto3 botocore
```

**2. Authenticate** (pick one)

```bash
aws configure                                    # simplest
# or
export AWS_ACCESS_KEY_ID='...' AWS_SECRET_ACCESS_KEY='...' AWS_REGION='us-west-2'
# or run from an EC2 instance with an IAM role — nothing to configure
```

**3. Edit `inventory.aws.yml`**

```yaml
plugin: amazon.aws.aws_ec2
regions:
  - us-west-2

filters:
  instance-state-name: running
  tag:Role: storage              # <-- match your tagging convention
  # instance-type:
  #   - p4d.24xlarge

groups:
  storage_servers: "'tier0' in (tags.Role | default(''))"

compose:
  ansible_host: private_ip_address
  ansible_user: "'ubuntu'"
  # Volume AZ prefix from the availability zone letter: us-west-2a -> AZ1:
  hammerspace_volume_az_prefix: >-
    "AZ" ~ ((placement.availability_zone[-1:] | lower | ord) - 96) ~ ":"
```

**4. Point `ansible.cfg` at it and test**

```ini
[defaults]
inventory = inventory.aws.yml
```

```bash
ansible-inventory -i inventory.aws.yml --graph
ansible -i inventory.aws.yml storage_servers -m ping
```

| Host variable | Example |
|---|---|
| `aws_instance_id` | `i-0abc123def456` |
| `aws_instance_type` | `p4d.24xlarge` |
| `aws_availability_zone` | `us-west-2a` |
| `aws_vpc_id` | `vpc-12345678` |
| `aws_private_ip` | `10.0.1.100` |

AZ mapping: `us-west-2a` → `AZ1:`, `us-west-2b` → `AZ2:`, `us-west-2c` → `AZ3:`.
Target one zone with `ansible az_us-west-2a -m ping`.

---

## GCP dynamic inventory

Discovers Compute Engine instances.

**1. Install the collection and SDK**

```bash
ansible-galaxy collection install google.cloud
pip3 install google-auth requests
```

**2. Authenticate** (pick one)

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json   # automation
gcloud auth application-default login                                 # interactive
# or run on GCE with the default service account — nothing to configure
```

**3. Edit `inventory.gcp.yml`** — the `projects` value is required:

```yaml
plugin: google.cloud.gcp_compute
projects:
  - your-gcp-project-id            # <-- REQUIRED
zones:
  - us-central1-a
  - us-central1-b
  - us-central1-c
auth_kind: application

filters:
  - status = RUNNING
  # - labels.role = storage

groups:
  storage_servers: "'tier0' in (labels.role | default(''))"

compose:
  ansible_host: networkInterfaces[0].networkIP
  ansible_user: "'ubuntu'"
  # Volume AZ prefix from the zone letter: us-central1-a -> AZ1:
  hammerspace_volume_az_prefix: >-
    "AZ" ~ (((zone | basename)[-1:] | lower | ord) - 96) ~ ":"
```

**4. Point `ansible.cfg` at it and test**

```ini
[defaults]
inventory = inventory.gcp.yml
```

```bash
ansible-inventory -i inventory.gcp.yml --graph
ansible -i inventory.gcp.yml storage_servers -m ping
```

| Host variable | Example |
|---|---|
| `gcp_instance_name` | `tier0-node-01` |
| `gcp_machine_type` | `a2-highgpu-8g` |
| `gcp_zone` | `us-central1-a` |
| `gcp_region` | `us-central1` |
| `gcp_private_ip` | `10.128.0.10` |

AZ mapping: `us-central1-a` → `AZ1:`, `-b` → `AZ2:`, `-c` → `AZ3:`.
Target one zone with `ansible zone_us-central1-a -m ping`.

---

## Azure dynamic inventory

Discovers Azure virtual machines. This section covers the inventory only —
the complete Azure walkthrough (VM sizing, disks, networking, AZ mapping) is
[DEPLOYMENT_GUIDE_AZURE.md](../DEPLOYMENT_GUIDE_AZURE.md).

**1. Install the collection and SDK**

```bash
ansible-galaxy collection install azure.azcollection
pip3 install -r ~/.ansible/collections/ansible_collections/azure/azcollection/requirements.txt
```

**2. Authenticate** (pick one)

```bash
az login && az account set --subscription <subscription-id>     # interactive
# or a service principal:
export AZURE_SUBSCRIPTION_ID='...' AZURE_CLIENT_ID='...' AZURE_SECRET='...' AZURE_TENANT='...'
# or managed identity: set  auth_source: msi  in inventory.azure.yml
#    when the Ansible controller is itself an Azure VM
```

**3. Edit `inventory.azure.yml`**

Set the resource group(s) that hold the Tier 0 VMs and select them by VM size:

```yaml
plugin: azure.azcollection.azure_rm

include_vm_resource_groups:
  - tier0-rg

plain_host_names: true
auth_source: auto

conditional_groups:
  storage_servers: >-
    virtual_machine_size is defined and
    virtual_machine_size in ['Standard_L16s_v3', 'Standard_L32s_v3']
```

Two things to know:

- **The file name matters.** The plugin only recognises files ending in
  `azure_rm.yml` or `azure.yml`. Keep the name `inventory.azure.yml`.
- **A broad filter is dangerous.** Every VM that matches gets its disks
  RAIDed and formatted. Check with `--graph` before the first run.

**4. Test and deploy**

```bash
ansible-inventory -i inventory.azure.yml --graph
ansible -i inventory.azure.yml storage_servers -m ping
./scripts/run.sh playbook site.yml -i inventory.azure.yml
```

**Host variables Azure provides**

| Variable | Source | Note | Example |
|---|---|---|---|
| `azure_zone` | inventory + IMDS | Availability zone, **1-based**; empty on non-zonal VMs | `"2"` |
| `azure_fault_domain` | IMDS only | Platform fault domain, **0-based** | `"1"` |
| `azure_location` | inventory + IMDS | Region | `"westus2"` |
| `azure_vm_size` | inventory + IMDS | VM SKU | `"Standard_L16s_v3"` |
| `azure_resource_group` | inventory + IMDS | Resource group | `"tier0-rg"` |

AZ mapping: `zone "1"` → `AZ1` (used as-is); `platformFaultDomain 0` → `AZ1`
(0-based, so +1). Zone wins when both exist. Override per host with
`hammerspace_node_az`.

**VMs in an availability set instead of zones**

The inventory plugin never reports the fault domain, so every VM would land in
the same AZ. Turn on metadata detection in `vars/main.yml` so each VM reports
its own placement:

```yaml
hammerspace_enable_az_mapping: true
hammerspace_azure_imds_az: true
```

This is safe to leave on for non-Azure hosts — the request times out quickly
and is ignored.

**The Azure temporary disk**

Azure VMs have a temporary *resource disk* (usually `/dev/sdb`, mounted at
`/mnt`) that is wiped whenever the VM is deallocated. The playbook recognises
it as a system disk and never includes it in a RAID set. Confirm with a dry
run before the first deployment:

```bash
./scripts/run.sh playbook site.yml -i inventory.azure.yml --check --tags discovery
```

and look for the resource disk under **STRICT protected** in the "Disk
classification" output.

Set `storage_type` to match the VM family:

| Azure VM family | Local storage | `storage_type` |
|---|---|---|
| Lsv3 / Lasv3 (storage-optimised) | Local NVMe | `nvme` |
| ND / NC (GPU) | Local NVMe | `nvme` |
| Premium SSD v2 / Ultra Disk attached | Managed disks as `/dev/sd*` | `scsi` |

---

## Multi-cloud inventory

Use several inventories at once — either in `ansible.cfg`:

```ini
[defaults]
inventory = inventory.oci.yml,inventory.aws.yml,inventory.gcp.yml,inventory.azure.yml
```

or on the command line:

```bash
ansible-playbook -i inventory.oci.yml -i inventory.aws.yml site.yml   # both
ansible-playbook -i inventory.aws.yml site.yml                        # one cloud only
ansible-inventory -i inventory.oci.yml -i inventory.aws.yml --graph  # see the combined list
```

Each provider creates groups by zone / fault domain, so you can target one
failure domain at a time:

```bash
ansible fd_FAULT_DOMAIN_1 -m ping     # OCI
ansible az_us-west-2a -m ping         # AWS
ansible zone_us-central1-a -m ping    # GCP
ansible az1 -m ping                   # Azure
```

---

## Availability-zone prefixes on volume names

For multi-zone deployments Hammerspace uses a prefix on the volume name
(`AZ1:node::/hammerspace/hsvol0`) to know which zone a copy lives in. The
prefix is **off by default**.

| Setting in `vars/main.yml` | Volume name |
|---|---|
| Prefix disabled (default) | `instance-name::/hammerspace/hsvol0` |
| Prefix enabled, `AZ1` | `AZ1:instance-name::/hammerspace/hsvol0` |
| Prefix enabled, custom | `WEST:instance-name::/hammerspace/hsvol0` |

```yaml
# Option 1 — off (default)
hammerspace_volume_az_prefix_enabled: false

# Option 2 — same prefix on every node
hammerspace_volume_az_prefix_enabled: true
hammerspace_volume_az_prefix_mode: "AZ1:"

# Option 3 — detect from the cloud (OCI fault domain, Azure zone, ...)
hammerspace_volume_az_prefix_enabled: true
hammerspace_volume_az_prefix_mode: "auto"

# Option 4 — override everything with a fixed value
hammerspace_volume_az_prefix: "WEST:"

# Option 5 — from inventory group names AZ1 / AZ2 / AZ3 (on-prem)
hammerspace_volume_az_prefix_enabled: true     # nothing else needed
```

With `auto` on OCI: `FAULT-DOMAIN-1` → `AZ1:`, `FAULT-DOMAIN-2` → `AZ2:`, and so on.

**AZ labels on nodes** (separate from volume naming) tell Hammerspace which
zone each *node* is in:

```yaml
hammerspace_enable_az_mapping: true
hammerspace_apply_az_labels: true
hammerspace_default_az: "AZ1"       # used when nothing else matches
# hammerspace_node_az: "AZ2"        # per-host override, set in the inventory
```

Detection order: explicit `hammerspace_node_az` → OCI fault domain → Azure
zone → Azure fault domain → `AZ1:` prefix already in the node name →
inventory group named `AZ<N>` → `hammerspace_default_az`.

For OCI GPU clusters where the *GPU memory fabric* defines the failure
domain, see [DEPLOYMENT_GUIDE.md § 9](../DEPLOYMENT_GUIDE.md#9-availability-zone-az-configuration-with-gpu-fabric).
