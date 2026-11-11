# Hammerspace Tier 0 — Ansible Automation

Turn the local NVMe drives in your GPU servers into Hammerspace **Tier 0**
storage with one command.

Tier 0 lets Hammerspace use the NVMe already inside your GPU servers as a
shared, persistent storage tier. Data is served to the GPUs at local-NVMe
speed, checkpoints finish faster, and the servers stay part of the global
namespace. This repository automates everything that has to happen on each
server — RAID, filesystems, NFS, tuning, firewall — and registers the result
with your Hammerspace cluster.

It also covers the **Linux Storage Server (LSS)** use case (the same setup on
storage servers without GPUs) and can deploy Hammerspace **Data Instantiator
(mover)** nodes alongside.

---

## Which guide do I need?

| Your situation | Start with |
|---|---|
| First time here, want to see it work | [Quick start](#quick-start) below (10 minutes, one server) |
| GPU instances on **Oracle Cloud (OCI)** | [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) |
| VMs on **Microsoft Azure** | [DEPLOYMENT_GUIDE_AZURE.md](DEPLOYMENT_GUIDE_AZURE.md) |
| Azure VMs whose NVMe is **already** formatted and mounted (CycleCloud) | [DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md](DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md) |
| Instances on **AWS** or **Google Cloud** | [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) with the AWS/GCP inventory from [docs/INVENTORY_SETUP.md](docs/INVENTORY_SETUP.md) |
| **On-premises** servers, internet available | [Quick start](#quick-start), then [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) from step 5 |
| **Air-gapped** site, or HDD / hardware RAID | [DEPLOYMENT_GUIDE_DARKSITE_HDD.md](DEPLOYMENT_GUIDE_DARKSITE_HDD.md) |
| Deployed already — going to production | [CLEANUP_PLAN.md](CLEANUP_PLAN.md) (production readiness checklist) |
| Maintenance, drive swaps, removing nodes | [RACK_OPERATIONS_RUNBOOK.md](RACK_OPERATIONS_RUNBOOK.md) |
| Want new OCI instances to provision themselves | [AUTOMATION_GUIDE.md](AUTOMATION_GUIDE.md) |
| Something went wrong | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |

---

## What you get

On every server in your inventory the playbook:

1. **Finds the data drives** and protects the OS disk. It will not format
   anything until it has positively identified which disk holds the operating
   system.
2. **Checks the environment** — drive count, 4K sectors, NUMA balance, jumbo
   frames to the Anvil, required packages — and tells you what is off before
   changing anything.
3. **Builds RAID 0 arrays**, one per NUMA node, with chunk size and queue depth
   tuned for the CPU it finds.
4. **Formats and mounts** them as XFS under `/hammerspace/hsvol0`, `hsvol1`, …
   by UUID, with a systemd guard that stops accidental unmounts and remounts
   automatically.
5. **Configures NFS** exactly as the Hammerspace Tier 0 Deployment Guide
   specifies (128 threads, NFSv4.2, RDMA, correct root-squash rules) and opens
   the firewall.
6. **Applies Hammerspace performance tuning** — RPC slots, NFSD block size,
   read-ahead, NIC IRQ pinning, TCP buffers.
7. **Registers the node and its volumes** with your Hammerspace cluster, joins
   them to a volume group, and can create shares and apply the placement
   objective that makes new files land on Tier 0.

Running it again is safe: existing arrays, filesystems and registrations are
detected and reused, never rebuilt.

**Platforms:** Ubuntu, Debian, RHEL, Rocky, CentOS · x86_64 and ARM ·
on-premises, OCI, AWS, GCP, Azure (with automatic server discovery on each
cloud).

---

## Before you start

| You need | Details |
|---|---|
| A control machine | Your laptop, a bastion or a scheduler node — Linux or macOS, with Ansible 2.14 or newer |
| SSH to the servers | A user with `sudo`. (Or run the playbook on the server itself — no SSH needed.) |
| The Hammerspace Anvil | Its management IP, an **admin** user and password, and port 8443 reachable from the control machine |
| Servers with empty data drives | The OS disk is detected and protected automatically; every other NVMe drive will be used |
| Jumbo frames | MTU 9000 between the servers and the Hammerspace nodes (checked, not required) |

---

## Quick start

Ten minutes, one server, on-premises. Cloud deployments use the same steps
with auto-discovered inventory — follow the guide for your cloud instead.

### 1. Install

```bash
# Ansible
brew install ansible                      # macOS
sudo apt install -y ansible git-lfs       # Ubuntu / Debian
sudo dnf install -y ansible git-lfs       # RHEL / Rocky

git clone https://github.com/hammerspace-solutions/Ansible-Tier0.git
cd Ansible-Tier0
git lfs pull                              # fetches the DI packages in payload/
ansible-galaxy collection install -r requirements.yml
```

### 2. List your servers

Edit `inventory.yml`:

```yaml
all:
  children:
    storage_servers:
      hosts:
        tier0-node-01:
          ansible_host: 10.200.100.101
      vars:
        ansible_user: ubuntu
        ansible_ssh_private_key_file: ~/.ssh/id_ed25519
```

```bash
ansible -i inventory.yml storage_servers -m ping     # every host should answer "pong"
```

### 3. Point it at your Hammerspace cluster

Pass the Anvil IP on the command line — once. `vars/main.yml` leaves it empty
on purpose, and the run stops immediately, naming the variable, if it is not set:

```bash
./scripts/run.sh playbook site.yml -i inventory.yml -e hammerspace_api_host=10.1.2.3
```

To keep it out of your command history, put site values in a file and pass
`-e @site-overrides.yml`:

```yaml
hammerspace_api_host: "10.1.2.3"
mover_nodes: ["10.1.2.20", "10.1.2.21"]   # optional: data-mover / DSX data-path IPs
```

The API user defaults to `admin`; change `hammerspace_api_user` in
`vars/main.yml` if yours differs.

Put the password in `vars/vault.yml` and encrypt the file:

```yaml
vault_hammerspace_api_password: "your-password"
```

```bash
ansible-vault encrypt vars/vault.yml
```

Everything else has a working default. If you want to check, the settings
most sites change are listed at the top of
[VARIABLE_REFERENCE.md](VARIABLE_REFERENCE.md#start-here--the-settings-most-sites-change).

### 4. Look before you leap

```bash
./scripts/run.sh playbook site.yml -i inventory.yml --ask-vault-pass --tags discovery,precheck
```

This changes nothing. Read the output: it lists the drives it will use, the
disk it will protect, and any check that did not pass.

### 5. Deploy

```bash
./scripts/run.sh playbook site.yml -i inventory.yml --ask-vault-pass
```

A first run on a server with eight NVMe drives takes about five minutes. It
ends with a summary like:

```
HAMMERSPACE INTEGRATION COMPLETE
============================================
Anvil API: 10.1.2.3
Storage System: tier0-node-01
Volumes Added: 2
  - tier0-node-01::/hammerspace/hsvol0
  - tier0-node-01::/hammerspace/hsvol1
============================================
```

### 6. Verify

On the server:

```bash
cat /proc/mdstat                 # arrays, all [UU]
df -h /hammerspace/hsvol*        # mounted
exportfs -v                      # exported
```

In Hammerspace: the node appears under **Storage Systems** and its volumes
under **Storage Volumes**, or from the Anvil CLI:

```
anvil> node-list
anvil> volume-list
```

> **Why `./scripts/run.sh` instead of `ansible-playbook`?** It picks a UTF-8
> locale (Ansible will not start without one on some non-English systems) and
> writes a log of every run to `~/logs/`. Plain `ansible-playbook` works too.

---

## Everyday commands

```bash
# Dry run — show what would change
./scripts/run.sh playbook site.yml -i inventory.yml --check

# Only one part
./scripts/run.sh playbook site.yml -i inventory.yml --tags precheck
./scripts/run.sh playbook site.yml -i inventory.yml --tags nfs-exports    # push export changes, no restart
./scripts/run.sh playbook site.yml -i inventory.yml --tags perf           # re-apply tuning
./scripts/run.sh playbook site.yml -i inventory.yml --tags hammerspace    # register only

# Only some servers
./scripts/run.sh playbook site.yml -i inventory.yml --limit tier0-node-02
./scripts/run.sh playbook site.yml -i inventory.yml --limit "rack3-*"

# Find and deploy only servers that are not yet in Hammerspace
ansible-playbook preflight_check.yml -i inventory.yml
./scripts/run.sh playbook site.yml -i inventory.yml --limit @.new_instances_limit
```

---

## After deployment

| Task | How |
|---|---|
| Add servers | Add them to the inventory, run `preflight_check.yml`, deploy with `--limit @.new_instances_limit` |
| Planned maintenance on a node | Disable availability-drop first so Hammerspace waits instead of re-replicating — [RACK_OPERATIONS_RUNBOOK.md](RACK_OPERATIONS_RUNBOOK.md) |
| Remove a node from Hammerspace, keep its data | `decommission_tier0.yml` |
| Wipe a node back to bare metal | `reset-tier0-host.yml -e reset_confirm=true` |
| Make new files land on Tier 0 first | `hammerspace_tier0_place_on_group: true` — [docs/HAMMERSPACE_INTEGRATION.md](docs/HAMMERSPACE_INTEGRATION.md#making-new-files-land-on-tier-0) |
| Pick the volume group per run, or per AZ | `-e hammerspace_volume_group=...`, or `group_vars/AZ1` — [docs/HAMMERSPACE_INTEGRATION.md](docs/HAMMERSPACE_INTEGRATION.md#choosing-the-group-per-run-or-per-az) |
| Deploy mover (DI) nodes | [docs/DATA_INSTANTIATOR.md](docs/DATA_INSTANTIATOR.md) |
| Harden for production | [CLEANUP_PLAN.md](CLEANUP_PLAN.md) |

All operator playbooks and scripts are described in
[docs/OPERATOR_SCRIPTS.md](docs/OPERATOR_SCRIPTS.md).

---

## Documentation

**Deployment guides** — step by step, per platform

- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — OCI (also the reference walkthrough for AWS, GCP and on-prem)
- [DEPLOYMENT_GUIDE_AZURE.md](DEPLOYMENT_GUIDE_AZURE.md) — Azure
- [DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md](DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md) — Azure, pre-mounted NVMe
- [DEPLOYMENT_GUIDE_DARKSITE_HDD.md](DEPLOYMENT_GUIDE_DARKSITE_HDD.md) — air-gapped, HDD, hardware RAID
- [AUTOMATION_GUIDE.md](AUTOMATION_GUIDE.md) — OCI instances that provision themselves on launch

**Operations**

- [CLEANUP_PLAN.md](CLEANUP_PLAN.md) — production readiness checklist
- [RACK_OPERATIONS_RUNBOOK.md](RACK_OPERATIONS_RUNBOOK.md) — bring-down, bring-up, drive replacement, node removal
- [docs/OPERATOR_SCRIPTS.md](docs/OPERATOR_SCRIPTS.md) — every playbook and script for day-2 work
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — symptoms, causes, fixes

**Reference** — how it works and every option

- [docs/INVENTORY_SETUP.md](docs/INVENTORY_SETUP.md) — static, OCI, AWS, GCP, Azure and multi-cloud inventories; availability zones
- [docs/STORAGE_CONFIGURATION.md](docs/STORAGE_CONFIGURATION.md) — drive selection, RAID sizing, prechecks, mounts, NFS, tuning
- [docs/HAMMERSPACE_INTEGRATION.md](docs/HAMMERSPACE_INTEGRATION.md) — registration, volume groups, shares, placement, S3, cluster settings
- [docs/DATA_INSTANTIATOR.md](docs/DATA_INSTANTIATOR.md) — DI on hosts, in containers, or on Kubernetes
- [docs/ZERO_SSH_DEPLOYMENT.md](docs/ZERO_SSH_DEPLOYMENT.md) — OCI Run Command deployment
- [docs/REPOSITORY_LAYOUT.md](docs/REPOSITORY_LAYOUT.md) — what each file is for
- [VARIABLE_REFERENCE.md](VARIABLE_REFERENCE.md) — every setting with its default

**For engineers extending the automation**

- [tests/integration/README.md](tests/integration/README.md) — the regression test suite (`./tests/run_all.sh`)
- [TEST_SCENARIOS.md](TEST_SCENARIOS.md) — manual test plan
- [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) — design review

---

## Getting help

1. Check [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for the message you saw.
2. Re-run with `-vvv` if you need more detail.
3. Contact Hammerspace support with the log from `~/logs/ansible-<timestamp>.log`
   and the output of `ansible-inventory -i <file> --graph`.

Related Hammerspace resources: the [Hammerspace Tier 0 Deployment Guide](https://hammerspace.com),
[Ansible examples](https://github.com/hammer-space/ansible),
[Hammerspace Toolkit (HSTK)](https://github.com/hammer-space/hstk),
[Grafana dashboards](https://github.com/hammer-space/grafana-dashboards).

## License

MIT
