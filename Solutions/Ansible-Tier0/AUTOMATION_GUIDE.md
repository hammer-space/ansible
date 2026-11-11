# Event-Driven Auto-Provisioning Guide

Automated Hammerspace Tier 0 & DI deployment on OCI GPU instances. Zero SSH, zero human intervention — instances self-provision on launch.

**Who this is for:** OCI customers whose GPU fleet grows and shrinks
automatically (node pools, autoscaling, frequent replacement) and who do not
want to run a playbook every time.
**What you need:** an OCI tenancy where you can create a Vault secret, a
Function and an Event rule; Terraform and Docker (or podman) on your
workstation; a working manual deployment first — this guide automates
[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md), it does not replace it.
**Time:** about an hour to set up; then nothing.

For the semi-automatic version — you run one command, the instances do the
rest, still no SSH — see [docs/ZERO_SSH_DEPLOYMENT.md](docs/ZERO_SSH_DEPLOYMENT.md).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Setup](#4-setup)
5. [How It Works (Detail)](#5-how-it-works-detail)
6. [Credential Security](#6-credential-security)
7. [GPU Fabric AZ Detection](#7-gpu-fabric-az-detection)
8. [DI Container Deployment](#8-di-container-deployment)
9. [Configuration Reference](#9-configuration-reference)
10. [Network Requirements](#10-network-requirements)
11. [Monitoring and Troubleshooting](#11-monitoring-and-troubleshooting)
12. [Scaling](#12-scaling)
13. [Day-2 Operations](#13-day-2-operations)
14. [Limitations](#14-limitations)

---

## 1. Overview

When a new GPU instance launches in OCI, it is automatically provisioned with Hammerspace Tier 0 storage and DI — no human intervention, no SSH keys, no manual playbook runs.

The system uses three OCI services:

- **OCI Events** — detects when a new instance reaches RUNNING state
- **OCI Functions** — validates the instance and triggers deployment
- **OCI Run Command** — executes the bootstrap script on the instance via the Cloud Agent

Each instance self-provisions by running Ansible locally. The process is idempotent, architecture-aware (x86_64/aarch64), and automatically assigns availability zones based on GPU memory fabric topology.

---

## 2. Architecture

```
                          OCI Cloud
┌─────────────┐     ┌──────────────────────────────────────────────┐
│             │     │                                              │
│  Instance   │     │  ┌─────────┐    ┌──────────┐    ┌────────┐  │
│  Provisioner│────►│  │ Compute │───►│  Events  │───►│Function│  │
│  (Terraform,│     │  │ Service │    │  Service │    │  (fn)  │  │
│   Console,  │     │  └─────────┘    └──────────┘    └───┬────┘  │
│   API)      │     │       │                             │       │
└─────────────┘     │       │ Instance                    │ OCI   │
                    │       │ reaches                     │ Run   │
                    │       │ RUNNING                     │Command│
                    │       ▼                             ▼       │
                    │  ┌──────────┐              ┌─────────────┐  │
                    │  │   GPU    │◄─────────────│ Cloud Agent │  │
                    │  │ Instance │  bootstrap.sh│  (pre-      │  │
                    │  │          │  runs locally │  installed) │  │
                    │  └────┬─────┘              └─────────────┘  │
                    │       │                                     │
                    └───────┼─────────────────────────────────────┘
                            │ Registers node + volumes
                            ▼
                    ┌──────────────┐
                    │ Hammerspace  │
                    │ Anvil Cluster│
                    │ (API 8443)   │
                    └──────────────┘
```

**The event chain:**

1. **Instance provisioned** — Terraform, OCI Console, or any tool creates a GPU instance
2. **Instance reaches RUNNING** — OCI fires `com.oraclecloud.computeapi.launchinstance.end`
3. **Event rule matches** — Filters by compartment and instance shape
4. **Function invoked** — Receives the event with instance OCID
5. **Function validates** — Checks shape, confirms RUNNING, waits for Cloud Agent
6. **Run Command sent** — Bootstrap script sent via OCI Run Command API (fire-and-forget)
7. **Cloud Agent executes** — Runs the bootstrap script on the instance
8. **OS readiness wait** — Waits for systemd, cloud-init, package manager
9. **Ansible installs and runs** — Clones repo, generates local inventory, runs `site.yml`
10. **GPU fabric AZ detection** — Queries instance metadata for GPU fabric, assigns AZ
11. **Tier 0 configures** — RAID, filesystems, NFS, Hammerspace node + volume registration
12. **DI deploys** — Container image loaded/built, pd-di registered as MOVER_EXT
13. **Done** — Node appears in Hammerspace with correct AZ prefix, DI running

Total time: ~10-15 minutes from instance launch to fully provisioned.

---

## 3. Prerequisites

### OCI Resources

| Resource | Required | Purpose |
|----------|:--------:|---------|
| Compartment | Yes | Scope for instances, function, event rule |
| VCN + Subnet | Yes | Network for function application |
| OCI Vault + Key | Yes | Securely store Hammerspace API password |
| Container Registry (OCIR) | Yes | Host the function container image |
| OCI CLI configured | Yes | For setup commands (`~/.oci/config`) |
| Terraform | Recommended | Deploy function infrastructure |
| Docker or Podman | Yes | Build the function image |

### Instance Requirements

| Requirement | Details |
|-------------|---------|
| Oracle Cloud Agent | Pre-installed on all OCI images (must be enabled) |
| Run Command plugin | Enabled in Cloud Agent (enabled by default) |
| Internet access | To clone git repo and install Ansible packages |
| Hammerspace API | Network access to Anvil on port 8443 |

### Hammerspace Cluster

| Requirement | Details |
|-------------|---------|
| Anvil cluster | Running with REST API v1.2 accessible |
| Admin credentials | API user with node/volume management permissions |
| Network | Port 8443 reachable from GPU instances |

### Software (on your workstation)

```bash
# Clone the repo
git clone https://github.com/hammerspace-solutions/Ansible-Tier0.git
cd Ansible-Tier0

# Pull large files (DI RPMs in payload/) — requires git-lfs
# Install git-lfs: brew install git-lfs (macOS) or apt install git-lfs (Linux)
git lfs pull

# Install OCI CLI (if not present)
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"
oci setup config

# Install Terraform
brew install hashicorp/tap/terraform    # macOS
# or: https://developer.hashicorp.com/terraform/downloads

# Install Docker (for building the function image)
# Docker Desktop or podman
```

> **Important:** The `payload/` directory contains DI RPMs tracked by Git LFS.
> After cloning, run `git lfs pull` to download the actual RPM files.
> Without this, container builds will fail with "Can not load RPM file" errors.

---

## 4. Setup

### Step 1: Store Hammerspace Password in OCI Vault

```bash
# Create a Vault (if you don't have one)
oci kms management vault create \
  --compartment-id <COMPARTMENT_OCID> \
  --display-name "tier0-vault" \
  --vault-type DEFAULT

# Create a key
oci kms management key create \
  --compartment-id <COMPARTMENT_OCID> \
  --display-name "tier0-key" \
  --key-shape '{"algorithm":"AES","length":32}' \
  --endpoint <VAULT_MANAGEMENT_ENDPOINT>

# Store the Hammerspace password as a secret
oci vault secret create-base64 \
  --compartment-id <COMPARTMENT_OCID> \
  --vault-id <VAULT_OCID> \
  --key-id <KEY_OCID> \
  --secret-name "hammerspace-api-password" \
  --secret-content-content "$(echo -n 'YourActualPassword' | base64)"
```

Note the returned **secret OCID** — you'll need it in step 3.

### Step 2: Build and Push the Function Image

```bash
cd oci-function

# Login to OCIR (OCI Container Registry)
docker login <region>.ocir.io
# Username: <tenancy-namespace>/your-email
# Password: OCI auth token (generate in OCI Console > Profile > Auth Tokens)

# Build and push
./build.sh <region> <tenancy-namespace> tier0-functions
# Example: ./build.sh us-sanjose-1 mytenancy tier0-functions
```

This builds the function container image (bundles `func.py` + `oci_deploy.py` + `tier0-bootstrap.sh`) and pushes it to OCIR.

### Step 3: Deploy Infrastructure with Terraform

```bash
cd oci-function/terraform

# Configure
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
# Required
compartment_id  = "ocid1.compartment.oc1..xxxxx"
vcn_id          = "ocid1.vcn.oc1..xxxxx"
subnet_id       = "ocid1.subnet.oc1..xxxxx"
function_image  = "us-sanjose-1.ocir.io/mytenancy/tier0-functions/tier0-auto-deploy:latest"
vault_secret_id = "ocid1.vaultsecret.oc1..xxxxx"    # From step 1
hs_host         = "10.0.10.15"                       # Anvil management IP

# Optional
shape_filter    = "BM.GPU.GB200-v3.4"               # Only deploy on this shape
deploy_mode     = "both"                              # tier0, di, or both
repo_url        = "https://github.com/YourOrg/Ansible-Tier0.git"
repo_branch     = "main"
```

```bash
# Deploy
terraform init
terraform plan      # Review
terraform apply     # Create all resources
```

**What gets created:**

| Resource | Name | Purpose |
|----------|------|---------|
| Dynamic Group | `tier0-auto-deploy-functions` | Identity for the function (Resource Principal) |
| IAM Policy | `tier0-auto-deploy-policy` | 7 permission statements |
| Function Application | `tier0-auto-deploy` | Environment config (compartment, vault, shape, host) |
| Function | `tier0-auto-deploy` | Docker image, 256MB, 300s timeout |
| Event Rule | `tier0-auto-deploy-on-launch` | Triggers on `launchinstance.end` |

### Step 4: Verify

```bash
# Check function
fn list functions tier0-auto-deploy

# Check event rule is active
oci events rule get --rule-id <RULE_OCID> --query 'data."is-enabled"'

# Test with manual invocation (optional — sends a fake event)
echo '{"eventType":"com.oraclecloud.computeapi.launchinstance.end",
  "data":{"resourceId":"<INSTANCE_OCID>",
  "compartmentId":"<COMPARTMENT_OCID>",
  "additionalDetails":{"shape":"BM.GPU.GB200-v3.4"}}}' \
  | fn invoke tier0-auto-deploy tier0-auto-deploy
```

### Step 5: Launch a GPU Instance

That's it. Launch a GPU instance in the compartment — it auto-provisions.

---

## 5. How It Works (Detail)

### Event Triggers

| OCI Event | Triggers Deployment | Why |
|-----------|:-------------------:|-----|
| `launchinstance.end` | Yes | New instance created and reached RUNNING |
| `instanceaction.end` (START) | No | Instance restarted — already provisioned |
| `instanceaction.end` (STOP) | No | Instance stopped |
| `instanceaction.end` (REBOOT) | No | Instance rebooted — already provisioned |
| `terminateinstance.begin` | No | Use `cleanup_instance_nodes.py` for cleanup |

Only the **initial launch** triggers deployment. Reboots, stop/start cycles, and terminations do not re-trigger.

### Function Handler (func.py)

The function receives the OCI Event payload and:

1. **Extracts** instance OCID, compartment, shape from the event
2. **Shape filter** — skips if shape doesn't match `SHAPE_FILTER` (returns `SKIPPED_SHAPE`)
3. **Verifies** instance is RUNNING via Compute API
4. **Waits** for Cloud Agent "Run Command" plugin to be ready (up to 10 retries × 15s = 150s)
5. **Builds** the bootstrap script payload with injected config (vault secret OCID, HS host, deploy mode)
6. **Sends** OCI Run Command — fire-and-forget (does not wait for completion)
7. **Returns** `{status: "COMMAND_SENT", command_id: "ocid1..."}` 

The function exits in seconds. The actual deployment (10-15 min) runs on the instance via Cloud Agent.

### Bootstrap Script (tier0-bootstrap.sh)

The script is embedded inline in the Run Command payload (not fetched from a URL). It runs on the GPU instance:

```
tier0-bootstrap.sh
│
├── [0/5] Wait for OS readiness
│   ├── systemd reaches 'running' or 'degraded' (up to 5 min)
│   ├── cloud-init finishes (if running)
│   └── Package manager lock released (up to 5 min)
│
├── [1/5] Install Ansible
│   ├── dnf install ansible-core python3-pip git (RHEL/Rocky)
│   │   or apt install ansible python3-pip git (Ubuntu)
│   ├── pip3 install jmespath requests
│   └── ansible-galaxy collection install ansible.posix community.general
│
├── [2/5] Clone Ansible-Tier0 repo
│   └── git clone --branch main <REPO_URL> /opt/ansible-tier0
│
├── [3/5] Generate local inventory
│   ├── Detects hostname and IP automatically
│   ├── Creates inventory_local.yml (ansible_connection: local)
│   └── Adds host to both storage_servers and di_nodes groups
│
├── [4/5] Configure
│   ├── Fetches password from OCI Vault (instance principal auth)
│   ├── Generates vars/vault.yml
│   ├── Sets hammerspace_api_host, deploy_di, di_deployment_type
│   └── Enables GPU fabric AZ detection
│
└── [5/5] Run Ansible playbook
    └── ansible-playbook site.yml -i inventory_local.yml --connection local
        │
        ├── Tier 0: RAID → Filesystem → NFS → Hammerspace registration
        │   └── GPU fabric AZ detection → volume names: AZ1:node01::/hsvol0
        │
        ├── NFS export auto-wiring for DI
        │
        └── DI: Container build/load → Start → Register → Enable pd-di
```

Full log: `/var/log/tier0-bootstrap.log`

---

## 6. Credential Security

The Hammerspace API password never appears in plaintext in the function, the event, or the Run Command payload.

### Credential Flow

```
1. Password stored in OCI Vault (encrypted at rest, IAM-controlled)
         │
2. Function receives VAULT_SECRET_ID (just an OCID, not the password)
         │
3. Function embeds VAULT_SECRET_ID in the bootstrap script
   sent via Run Command (OCID only — no password in the payload)
         │
4. On the GPU instance, bootstrap script calls:
   oci secrets secret-bundle get --secret-id $OCI_VAULT_SECRET_OCID
   --auth instance_principal
         │
5. Instance uses its own identity (instance principal) to
   retrieve the password from OCI Vault at runtime
         │
6. Password used locally for ansible-playbook
   (environment variable only, never written to persistent storage)
```

### Required IAM for Instances

Instances need permission to read the specific vault secret:

```
Allow dynamic-group gpu-instances to read secret-bundles in compartment <compartment>
  where target.secret.id = '<SECRET_OCID>'
```

Dynamic group for instances:
```
ALL {instance.compartment.id = '<compartment_ocid>'}
```

---

## 7. GPU Fabric AZ Detection

Availability zones are automatically assigned based on GPU memory fabric topology. Instances sharing the same GPU fabric get the same AZ number.

### How It Works

```
Instance boots
    │
    ▼
Query instance metadata for GPU fabric OCID
    http://169.254.169.254/opc/v2/host/rdmaTopologyData/customerGpuMemoryFabric
    → "ocid1.computegpumemoryfabric.oc1...slutj7sca"
    │
    ▼
Query Hammerspace for existing volume names
    GET /storage-volumes → ["AZ1:node01::/hsvol0", "AZ2:node03::/hsvol0"]
    │
    ▼
Read gpu_fabric_data.txt for fabric→node mapping
    ocid1...slutj7sca  node01  10.0.1.1  (fabric A → AZ1)
    ocid1...xk8m2pqrs  node03  10.0.1.3  (fabric B → AZ2)
    │
    ▼
Cross-reference: this fabric already mapped → reuse AZ
    OR: new fabric → assign next available AZ number
    │
    ▼
Set hammerspace_volume_az_prefix: "AZ1:"
    → Volume names: AZ1:node05::/hammerspace/hsvol0
```

### AZ Assignment Rules

| Scenario | Result |
|----------|--------|
| GPU fabric matches existing fabric in Hammerspace volumes | Reuse same AZ (e.g., AZ1) |
| GPU fabric is new (first time seen) | Assign next available AZ number |
| GPU fabric metadata not available (non-GPU instance) | Falls back to fault domain or inventory groups |
| No existing volumes in Hammerspace (first deployment) | First fabric = AZ1, second = AZ2, etc. |

### Mapping Persistence

The fabric→AZ mapping is learned from two sources and persisted:

1. **Hammerspace volumes** — existing volume names carry the AZ prefix (e.g., `AZ1:node01::/hsvol0`), so the mapping is derived from the cluster state
2. **gpu_fabric_data.txt** — each node appends its fabric OCID to this file on the controller, building a persistent mapping file over time

This means the mapping survives cluster changes and new deployments can learn from existing assignments.

### Enable

GPU fabric AZ is enabled by default in the bootstrap script (`GPU_FABRIC_AZ=true`). For manual Ansible runs:

```yaml
# vars/main.yml
hammerspace_gpu_fabric_az: true
hammerspace_volume_az_prefix_enabled: true
```

---

## 8. DI Container Deployment

The DI (Data Instantiator / NFS Mover) deploys as a container alongside Tier 0 storage on the same instance.

### Image Sources

| Source | Setting | Speed | Use Case |
|--------|---------|-------|----------|
| Build on instance | `di_image_source: "build"` | 2-5 min | First deployment |
| Pre-built tar | `di_image_source: "local"` | 10-30 sec | Multi-node rollout |
| Download tar | `di_image_source: "url"` | Network-dependent | Centralized hosting |
| Container registry | `di_image_source: "registry"` | Network-dependent | Registry available |

For fastest deployment at scale, pre-build the image and use `di_image_source: "local"`.

### Container Startup Order

pd-di requires `/opt/pd/di` (UUID file created by `add_node.py` during registration) before it can start. The automation enforces:

```
1. Start container (lttng services start, pd-di NOT enabled)
2. Run add_node.py → creates /opt/pd/di
3. systemctl enable --now pd-di → starts cleanly (NRestarts=0)
```

### Resource Limits

```yaml
di_container_memory: "4g"    # Default: 4GB (not 64GB of the GPU node)
di_container_cpus: ""        # Default: unlimited
```

### Multi-Architecture

Both x86_64 and aarch64 are supported. The playbook auto-detects `ansible_architecture` and:
- Selects correct RPMs from `payload/` directory
- Uses `--platform linux/arm64` or `--platform linux/amd64` for container builds

---

## 9. Configuration Reference

### Function Environment Variables

Set in Terraform (`main.tf`) or OCI Console (Functions > Application > Configuration):

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPARTMENT_ID` | *(required)* | OCI compartment for Run Command scope |
| `VAULT_SECRET_ID` | *(required)* | OCI Vault secret OCID with Hammerspace password |
| `SHAPE_FILTER` | `"BM.GPU.GB200-v3.4"` | Comma-separated shapes (empty = all) |
| `DEPLOY_MODE` | `"both"` | `tier0`, `di`, or `both` |
| `REPO_URL` | GitHub URL | Ansible-Tier0 git repo (HTTPS) |
| `REPO_BRANCH` | `"main"` | Git branch |
| `HS_HOST` | *(required)* | Hammerspace Anvil IP |
| `HS_USER` | `"admin"` | Hammerspace API user |
| `COMMAND_TIMEOUT` | `"3600"` | Max Run Command time (seconds) |
| `LOG_LEVEL` | `"INFO"` | Function logging level |

### Ansible Variables (vars/main.yml)

Key variables auto-configured by the bootstrap script:

| Variable | Bootstrap Default | Description |
|----------|-------------------|-------------|
| `hammerspace_api_host` | From `HS_HOST` | Anvil management IP |
| `deploy_di` | `true` if mode=both/di | Enable DI deployment |
| `di_deployment_type` | `"container"` | Container mode for DI |
| `hammerspace_gpu_fabric_az` | `true` | Auto-detect AZ from GPU fabric |
| `hammerspace_volume_az_prefix_enabled` | `true` | Enable AZ prefix in volume names |
| `hammerspace_serial` | `0` | Parallel node processing (set >0 for large scale) |

### IAM Policies

```hcl
# Function permissions
Allow dynamic-group tier0-auto-deploy-functions to inspect instances in compartment <X>
Allow dynamic-group tier0-auto-deploy-functions to use instance-agent-command-family in compartment <X>
Allow dynamic-group tier0-auto-deploy-functions to inspect instance-agent-plugins in compartment <X>
Allow dynamic-group tier0-auto-deploy-functions to inspect vnics in compartment <X>
Allow dynamic-group tier0-auto-deploy-functions to inspect vnic-attachments in compartment <X>
Allow dynamic-group tier0-auto-deploy-functions to read secret-bundles in compartment <X>
Allow service cloudevents to use functions-family in compartment <X>

# Instance permissions (for vault secret access)
Allow dynamic-group gpu-instances to read secret-bundles in compartment <X>
```

---

## 10. Network Requirements

### Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 8443 | TCP | Instance → Anvil | Hammerspace REST API |
| 111 | TCP/UDP | Client → Instance | NFS portmapper |
| 2049 | TCP/UDP | Client → Instance | NFS |
| 20048 | TCP/UDP | Client → Instance | NFS mountd |
| 20049 | TCP | Client → Instance | NFS RDMA |
| 9095 | TCP | Anvil → Instance | DI communication |
| 9096 | TCP | Anvil → Instance | DI communication |
| 443 | TCP | Instance → Internet | Git clone, package install |

**No SSH (port 22) required.** All deployment communication uses the OCI control plane.

### Split Networks

If management and data networks are separate, configure both IPs:

```yaml
hammerspace_api_host: "10.0.3.2"           # Management (Ansible controller)
hammerspace_cluster_mgmt_ip: "10.0.4.2"    # Data (reachable from GPU instances)
```

The DI roles use `hammerspace_cluster_mgmt_ip` for API calls, falling back to `hammerspace_api_host`.

---

## 11. Monitoring and Troubleshooting

### Function Logs

```bash
# Recent invocations
fn list calls tier0-auto-deploy tier0-auto-deploy --last 10

# Specific call output
fn get call tier0-auto-deploy tier0-auto-deploy <CALL_ID>

# OCI Console: Observability > Logging > tier0-auto-deploy
```

### Run Command Output

```bash
# List commands for an instance
oci instance-agent command list --compartment-id <OCID>

# Get command output
oci instance-agent command-execution get \
  --command-id <COMMAND_OCID> \
  --instance-id <INSTANCE_OCID>
```

### Instance Bootstrap Log

```bash
# Read the log via Run Command (no SSH needed)
oci instance-agent command create \
  --compartment-id <OCID> \
  --target '{"instanceId": "<INSTANCE_OCID>"}' \
  --content '{"source":{"sourceType":"TEXT","text":"tail -100 /var/log/tier0-bootstrap.log"}}'
```

### Common Issues

| Symptom | Cause | Resolution |
|---------|-------|------------|
| Function not triggered | Event rule disabled or wrong compartment | `oci events rule get --rule-id <OCID>` |
| Function returns `SKIPPED_SHAPE` | Shape doesn't match `SHAPE_FILTER` | Update filter or set empty for all shapes |
| Function returns `SKIPPED_AGENT_NOT_READY` | Cloud Agent not started yet | Bare-metal can take 2+ min; function retries 10x |
| Bootstrap: "Install Ansible" fails | No internet access | Pre-install Ansible in the OS image |
| Bootstrap: "Clone repo" fails | Git URL unreachable | Check network/firewall; consider private repo with token |
| Hammerspace registration fails | Wrong API host or password | Check `hammerspace_api_host` and vault secret content |
| DI container: platform mismatch | Wrong base image pulled on ARM | Fixed: `--platform linux/arm64` auto-detected |
| DI container: pd-di restart loop | pd-di enabled before registration | Fixed: pd-di enabled after `add_node.py` |
| AZ not detected | No GPU fabric metadata | Falls back to fault domain or defaults |
| Duplicate events | OCI at-least-once delivery | Safe: bootstrap is idempotent |

### Verify Deployment

```bash
# From the Anvil CLI
anvil> node-list
anvil> volume-list
anvil> node-list --name <hostname>

# Via Hammerspace API
curl -sk -u admin:pass https://<ANVIL>:8443/mgmt/v1.2/rest/nodes | python3 -m json.tool
curl -sk -u admin:pass https://<ANVIL>:8443/mgmt/v1.2/rest/storage-volumes | python3 -m json.tool
```

---

## 12. Scaling

| Scale | Recommendations |
|-------|----------------|
| 1-10 instances | Default config works. Each provisions independently. |
| 10-50 instances | Set `hammerspace_serial: 5` to batch Hammerspace API calls. |
| 50+ instances | Stagger launches over time. Monitor Anvil task queue. |
| Mixed x86_64 + aarch64 | Works automatically — each node detects its own architecture. |
| Multiple AZs | GPU fabric AZ detection assigns AZ numbers automatically. |
| Multiple GPU fabrics | Consistent AZ mapping maintained via `gpu_fabric_data.txt` and volume names. |

---

## 13. Day-2 Operations

| Operation | How |
|-----------|-----|
| Update Ansible config | Push to git repo — next new instance gets the update |
| Update existing instances | `python3 oci_deploy.py --compartment-id <OCID> --instance-id <OCID> --yes` |
| Replace a failed instance | Terminate + re-launch — event auto-provisions the replacement |
| Decommission a DI node | `ansible-playbook decommission_di.yml --limit <node> -e hammerspace_api_password=xxx` |
| Decommission a Tier 0 node | `python3 cleanup_instance_nodes.py --host <ANVIL> --user admin --password-file ~/.hs_password --node <name>` |
| Full host reset | `ansible-playbook reset-tier0-host.yml --limit <node> -e reset_confirm=true` |
| Change Hammerspace password | Update OCI Vault secret — next deployment uses the new password |
| Change deployment config | Update function env vars in OCI Console or re-run `terraform apply` |
| Disable auto-provisioning | `oci events rule update --rule-id <OCID> --is-enabled false` |
| Re-enable auto-provisioning | `oci events rule update --rule-id <OCID> --is-enabled true` |
| View deployment status | `fn list calls tier0-auto-deploy tier0-auto-deploy --last 20` |

---

## 14. Limitations

| Limitation | Impact | Status |
|-----------|--------|--------|
| Requires internet access on instances | Bootstrap needs git clone + package install | Pre-install in OS image for air-gapped |
| OCI-specific | OCI Events + Functions + Run Command | Not portable to AWS/GCP |
| Inline script size: 64KB | Bootstrap script must be compact | Current script is well under limit |
| Cloud Agent startup delay | 30-90s after instance reaches RUNNING | Function retries 10x × 15s |
| At-least-once event delivery | Duplicate function invocations possible | Bootstrap is idempotent |
| Function timeout: 300s | Function must send Run Command within 5 min | Fire-and-forget; actual deploy runs on instance |
| Container requires `--privileged` | DI container needs NFS + LTTng access | Cannot be avoided |
| Container requires `--network host` | No network isolation for DI | Required for ports 9095/9096 |
| pd-di RPMs are el9-only | Cannot install DI natively on Ubuntu | Container mode works on any Linux |
| `ansible.builtin.uri` SSL issues | Hangs with self-signed certs on Python 3.12+ | DI roles always use `curl` |
| `vars/vault.yml` must be optional | Hard loading fails if encrypted | All playbooks load vault optionally |
| Split management/data networks | API host may differ per context | `hammerspace_cluster_mgmt_ip` for DI, `hammerspace_api_host` for Tier 0 |

---

---

## File Reference

| File | Purpose |
|------|---------|
| `oci-function/func.py` | OCI Function handler (event → Run Command) |
| `oci-function/Dockerfile` | Function container image |
| `oci-function/build.sh` | Build + push to OCIR |
| `oci-function/terraform/main.tf` | Event Rule + Function + IAM (Terraform) |
| `oci-function/terraform/variables.tf` | Terraform input variables |
| `oci_deploy.py` | Manual OCI Run Command orchestrator (alternative to events) |
| `cloud-init/tier0-bootstrap.sh` | Bootstrap script (embedded in Run Command) |
| `site.yml` | Main Ansible playbook (Tier 0 + DI) |
| `roles/hammerspace_integration/tasks/gpu_fabric_az.yml` | GPU fabric AZ detection |
| `roles/di/` | Consolidated DI role |
| `container/Containerfile` | DI container image definition |
| `payload/` | DI RPMs + add_node.py (per architecture) |
| `decommission_di.yml` | DI node decommission |
| `reset-tier0-host.yml` | Full Tier 0 host reset |
| `cleanup_instance_nodes.py` | API-only Hammerspace node/volume cleanup |
| `vars/main.yml` | All configuration variables |
| `vars/vault.yml` | Encrypted Hammerspace credentials |
