# Test Scenarios — Ansible-Tier0

> **Audience: Hammerspace engineering and QA.** Manual test plan for the
> automation itself. If you are deploying Tier 0, start at [README.md](README.md).

Comprehensive test plan covering Tier 0 storage, DI deployment, OCI Run Command, and event-driven auto-provisioning.

---

## Table of Contents

1. [Tier 0 Storage Setup](#1-tier-0-storage-setup)
2. [Hammerspace Integration](#2-hammerspace-integration)
3. [AZ Prefix and Volume Naming](#3-az-prefix-and-volume-naming)
4. [NFS Export Management](#4-nfs-export-management)
5. [DI Deployment — Host Mode](#5-di-deployment--host-mode)
6. [DI Deployment — Container Mode](#6-di-deployment--container-mode)
7. [DI Pre-deploy / Activate Later](#7-di-pre-deploy--activate-later)
8. [DI Pre-built Image (Tar)](#8-di-pre-built-image-tar)
9. [DI AZ Distribution](#9-di-az-distribution)
10. [DI Auto-Export](#10-di-auto-export)
11. [DI Decommission](#11-di-decommission)
12. [Architecture-Aware Package Selection](#12-architecture-aware-package-selection)
13. [Anvil Overload Protection](#13-anvil-overload-protection)
14. [Tier 0 Host Reset](#14-tier-0-host-reset)
15. [OCI Run Command Deployment](#15-oci-run-command-deployment)
16. [OCI Events + Functions (Auto-Provisioning)](#16-oci-events--functions-auto-provisioning)
17. [Co-located Tier 0 + DI](#17-co-located-tier-0--di)
18. [Idempotency](#18-idempotency)
19. [Edge Cases and Failure Scenarios](#19-edge-cases-and-failure-scenarios)
20. [Data Mobility and Integrity](#20-data-mobility-and-integrity)

---

## 1. Tier 0 Storage Setup

### T1.1 — Dynamic NVMe Discovery and RAID

**Objective:** Verify NVMe drives are discovered, grouped by NUMA, and RAID arrays created.

**Prerequisites:** Bare-metal instance with multiple NVMe drives.

**Steps:**
1. Set `use_dynamic_discovery: true`, `use_raid: true`, `raid_level: 0` in `vars/main.yml`
2. Run: `ansible-playbook site.yml -i inventory.yml --tags discovery,raid`
3. Verify:
   - `cat /proc/mdstat` — RAID arrays exist, correct drive count
   - `lsblk` — boot drive excluded
   - Arrays grouped by NUMA node

**Expected:** One RAID array per NUMA node, boot drive automatically excluded.

### T1.2 — Filesystem and Mount Points

**Objective:** Verify XFS filesystems created with agcount=512, UUID-based fstab entries.

**Steps:**
1. Run: `ansible-playbook site.yml -i inventory.yml --tags filesystem`
2. Verify:
   - `df -h | grep hammerspace` — mount points exist
   - `xfs_info /hammerspace/hsvol0` — agcount=512
   - `cat /etc/fstab | grep UUID` — UUID-based entries with `nofail`

**Expected:** XFS filesystems mounted at `/hammerspace/hsvol0`, `/hammerspace/hsvol1`, etc.

### T1.3 — NFS Server Configuration

**Objective:** Verify NFS server configured with 128 threads, NFSv4.2, exports created.

**Steps:**
1. Run: `ansible-playbook site.yml -i inventory.yml --tags nfs`
2. Verify:
   - `cat /etc/nfs.conf` — threads=128, vers4.2=y
   - `exportfs -v` — all mount points exported
   - `showmount -e localhost` — exports visible

**Expected:** NFS server running with Hammerspace-recommended settings.

### T1.4 — Mount Point Protection

**Objective:** Verify systemd guard services prevent accidental unmount.

**Prerequisites:** `hammerspace_mount_protection: true`

**Steps:**
1. Run full deployment
2. Try: `umount /hammerspace/hsvol0`
3. Verify: `target is busy` error
4. Check: `systemctl list-units 'hammerspace-*'` — guard services active
5. Check: `systemctl status hammerspace-remount.timer` — watchdog running

**Expected:** Mounts protected, unmount blocked, watchdog active.

---

## 2. Hammerspace Integration

### T2.1 — Node Registration

**Objective:** Verify storage node registered in Hammerspace via API.

**Steps:**
1. Run: `ansible-playbook site.yml -i inventory.yml --tags hammerspace`
2. Verify in Hammerspace: `anvil> node-list`
3. Verify API: `curl -sk -u admin:pass https://<ANVIL>:8443/mgmt/v1.2/rest/nodes/<hostname>`

**Expected:** Node appears as type OTHER.

### T2.2 — Volume Registration

**Objective:** Verify volumes added with correct thresholds and protection settings.

**Steps:**
1. Run full deployment
2. Verify: `anvil> volume-list --node-name <hostname>`
3. Check thresholds: high=0.98, low=0.90
4. Check: `skipPerfTest` is in URL params (not JSON body)

**Expected:** All mount points registered as volumes with correct settings.

### T2.3 — Re-run Idempotency

**Objective:** Re-running on an already-registered node should skip, not fail.

**Steps:**
1. Run full deployment (first time)
2. Run again (second time)
3. Verify: node shows "already exists (skipped)", no errors

**Expected:** No duplicate registrations, no failures.

---

## 3. AZ Prefix and Volume Naming

### T3.1 — AZ from Inventory Groups (On-Prem)

**Objective:** Verify AZ prefix auto-detected from inventory group names.

**Prerequisites:** `hammerspace_volume_az_prefix_enabled: true`

**Inventory:**
```yaml
storage_servers:
  children:
    AZ1:
      hosts:
        node101:
          ansible_host: 10.200.101.216
    AZ2:
      hosts:
        node201:
          ansible_host: 10.200.103.188
```

**Steps:**
1. Run: `ansible-playbook site.yml -i inventory.yml`
2. Verify volume names: `AZ1:node101::/hammerspace/hsvol0`

**Expected:** Volume names prefixed with correct AZ from group name.

### T3.2 — AZ from OCI Fault Domain

**Objective:** Verify AZ prefix derived from OCI fault domain.

**Prerequisites:** OCI dynamic inventory with `hammerspace_volume_az_prefix` compose variable.

**Steps:**
1. Run with OCI inventory
2. Verify: FAULT-DOMAIN-1 → AZ1, FAULT-DOMAIN-2 → AZ2

**Expected:** Volume names match fault domain mapping.

### T3.3 — Mixed AZ Sources

**Objective:** Verify AZ detection priority: explicit > fault domain > group name > default.

**Steps:**
1. Set `hammerspace_node_az: "AZ5"` on one host
2. Put same host under AZ1 group
3. Run deployment
4. Verify: AZ5 takes precedence (explicit wins)

**Expected:** Explicit variable overrides group name.

---

## 4. NFS Export Management

### T4.1 — Update Exports Only (nfs-exports tag)

**Objective:** Verify `--tags nfs-exports` updates `/etc/exports` without restarting NFS.

**Steps:**
1. Add a new IP to `mover_nodes` in `vars/main.yml`
2. Run: `ansible-playbook site.yml -i inventory.yml --tags nfs-exports`
3. Verify:
   - `exportfs -v` — new IP appears with `no_root_squash`
   - NFS service was NOT restarted (no client disruption)
   - `systemctl show nfs-server --property=ActiveEnterTimestamp` — unchanged

**Expected:** Exports updated live via `exportfs -ra`, no service restart.

### T4.2 — DI Auto-Export

**Objective:** Verify `di_auto_export: true` adds DI node IPs to Tier 0 NFS exports.

**Prerequisites:** `deploy_di: true`, `di_auto_export: true`, DI nodes in inventory.

**Steps:**
1. Add DI nodes to `di_nodes` group with IPs not in `mover_nodes`
2. Run: `ansible-playbook site.yml -i inventory.yml -e deploy_di=true`
3. Verify on Tier 0 nodes: `exportfs -v` — DI IPs added with `no_root_squash`

**Expected:** DI node IPs automatically added to exports before DI deployment runs.

---

## 5. DI Deployment — Host Mode

### T5.1 — Full Host-Mode Deployment

**Objective:** Install pd-di natively via RPM and register with Hammerspace.

**Prerequisites:** `deploy_di: true`, `di_deployment_type: "host"`, RPMs in `payload/`.

**Steps:**
1. Run: `ansible-playbook site.yml -i inventory.yml --tags di -e deploy_di=true`
2. Verify on DI node:
   - `rpm -q pd-di` — installed
   - `systemctl status pd-di` — active
   - `systemctl status lttng-sessiond` — active
   - `firewall-cmd --list-all` — ports 9095/9096 open
3. Verify in Hammerspace: `anvil> node-list --name <di_node_name>`

**Expected:** pd-di installed, running, registered as MOVER_EXT.

### T5.2 — EPEL and Dependencies

**Objective:** Verify EPEL + dependencies install correctly (jemalloc, lttng, babeltrace, jmespath).

**Steps:**
1. Start with a clean RHEL/Rocky node (no EPEL)
2. Run DI deployment
3. Verify: `rpm -q epel-release jemalloc lttng-tools babeltrace`
4. Verify: `pip3 show jmespath` on both target and controller

**Expected:** No "No package available" errors.

---

## 6. DI Deployment — Container Mode

### T6.1 — Container Build and Deploy

**Objective:** Build pd-di container image and run it.

**Prerequisites:** `di_deployment_type: "container"`, `di_image_source: "build"`

**Steps:**
1. Run: `ansible-playbook site.yml --tags di -e deploy_di=true -e di_deployment_type=container`
2. Verify:
   - `podman ps` — container `pd-di` running
   - `podman exec pd-di systemctl status pd-di` — active
   - `podman exec pd-di systemctl status lttng-sessiond` — active
   - No restart loop: `podman exec pd-di systemctl show pd-di -p NRestarts` — NRestarts=0

**Expected:** Container running, pd-di active on first start (no restart loop).

### T6.2 — Container Startup Order

**Objective:** Verify register-before-pd-di ordering eliminates restart loop.

**Steps:**
1. Deploy in container mode
2. Check: `podman exec pd-di ls /opt/pd/di` — UUID file exists (created by add_node.py)
3. Check: `podman exec pd-di systemctl show pd-di -p NRestarts` — value is 0
4. Check container logs: `podman logs pd-di` — no "failed to start" messages before registration

**Expected:** pd-di starts cleanly after registration creates `/opt/pd/di`.

### T6.3 — Container with Pre-built Tar

**Objective:** Load image from tar instead of building.

**Prerequisites:** Pre-built tar in `payload/hammerspace-di-<arch>.tar`

**Steps:**
1. Build tar: `ansible-playbook build_di_image.yml --limit <build-host>`
2. Verify: `ls payload/hammerspace-di-*.tar`
3. Deploy: `ansible-playbook site.yml --tags di -e deploy_di=true -e di_deployment_type=container -e di_image_source=local -e di_image_local_path=payload/hammerspace-di-aarch64.tar`
4. Verify: no `podman build` in ansible output, only `podman load`

**Expected:** Image loaded from tar, no build step, faster deployment.

---

## 7. DI Pre-deploy / Activate Later

### T7.1 — Pre-deploy (Install Only)

**Objective:** Install DI without starting services or registering.

**Steps:**
1. Run: `ansible-playbook site.yml --tags di -e deploy_di=true -e di_activate=false`
2. Verify on DI node:
   - `rpm -q pd-di` — installed (host mode) OR `podman images` shows image (container mode)
   - `systemctl status pd-di` — inactive/dead (NOT running)
   - Hammerspace: node NOT registered
3. Verify output: "PRE-DEPLOYED (not activated)" message with activation command

**Expected:** Software installed, nothing started, not registered.

### T7.2 — Activate Pre-deployed Node

**Objective:** Activate a previously pre-deployed DI node.

**Prerequisites:** T7.1 completed.

**Steps:**
1. Run: `ansible-playbook site.yml --tags di-activate -e deploy_di=true -e di_activate=true --limit <di-node>`
2. Verify:
   - `systemctl status pd-di` — active (host mode)
   - OR `podman exec pd-di systemctl status pd-di` — active (container mode)
   - Hammerspace: node registered
3. Verify: install steps were NOT re-run (only services + registration)

**Expected:** Services started, node registered, no reinstall.

---

## 8. DI Pre-built Image (Tar)

### T8.1 — Build Image Tar

**Objective:** Build and export DI container image as architecture-specific tar.

**Steps:**
1. Run on x86_64 host: `ansible-playbook build_di_image.yml --limit <x86-host>`
2. Verify: `ls -lh payload/hammerspace-di-x86_64.tar` — file exists, reasonable size
3. Run on aarch64 host: `ansible-playbook build_di_image.yml --limit <arm-host>`
4. Verify: `ls -lh payload/hammerspace-di-aarch64.tar`

**Expected:** Architecture-specific tar files created in `payload/`.

### T8.2 — Deploy from Tar to Multiple Nodes

**Objective:** Load pre-built tar on multiple nodes (fast, no build).

**Steps:**
1. Set `di_image_source: "local"` and `di_image_local_path` in vars
2. Run: `ansible-playbook site.yml --tags di -e deploy_di=true`
3. Verify ansible output: `podman load` ran, `podman build` did NOT run
4. Verify all nodes: `podman images` shows the image

**Expected:** All nodes loaded image from tar, no build on any node.

---

## 9. DI AZ Distribution

### T9.1 — AZ Detection from Inventory Groups

**Objective:** Verify DI nodes detect AZ from inventory group names.

**Inventory:**
```yaml
di_nodes:
  children:
    AZ1:
      hosts:
        mover101: ...
    AZ2:
      hosts:
        mover201: ...
```

**Steps:**
1. Run with `di_min_az_count: 2`
2. Verify precheck output: "Unique AZs: 2 — AZ1, AZ2"

**Expected:** AZs correctly detected, distribution check passes.

### T9.2 — AZ Distribution Warning

**Objective:** Verify warning when DI nodes are in fewer AZs than required.

**Steps:**
1. Put all DI nodes under `AZ1` only
2. Set `di_min_az_count: 2`, `di_enforce_az_distribution: false`
3. Run deployment
4. Verify: WARNING message about insufficient AZ spread
5. Deployment continues (warning only)

**Expected:** Warning displayed, deployment not blocked.

### T9.3 — AZ Distribution Enforcement

**Objective:** Verify playbook fails when AZ enforcement is enabled and not met.

**Steps:**
1. Put all DI nodes under `AZ1` only
2. Set `di_min_az_count: 2`, `di_enforce_az_distribution: true`
3. Run deployment
4. Verify: playbook FAILS with clear error message

**Expected:** Playbook fails, no deployment.

---

## 10. DI Auto-Export

### T10.1 — New DI IPs Added to Exports

**Objective:** DI node IPs automatically added to Tier 0 NFS exports.

**Steps:**
1. Add DI nodes to `di_nodes` group (IPs NOT in `mover_nodes`)
2. Set `di_auto_export: true`
3. Run: `ansible-playbook site.yml -e deploy_di=true`
4. Verify on each Tier 0 node: `exportfs -v | grep <di_node_ip>` — present with `no_root_squash`

**Expected:** DI IPs added to exports before DI deployment.

### T10.2 — Existing IPs Skipped

**Objective:** DI IPs already in `mover_nodes` are not duplicated.

**Steps:**
1. Add DI node IP to both `mover_nodes` and `di_nodes`
2. Run deployment
3. Verify: "All DI node IPs are already in mover_nodes" message
4. Verify: no duplicate entries in `/etc/exports`

**Expected:** No duplicate exports, clean idempotent run.

---

## 11. DI Decommission

### T11.1 — Graceful Decommission

**Objective:** Decommission a DI node: evacuate volumes, remove from cluster, stop services.

**Steps:**
1. Deploy a DI node first
2. Run: `ansible-playbook decommission_di.yml -i inventory.yml --limit <di-node>`
3. Verify:
   - Hammerspace: node no longer in `node-list`
   - DI node: `systemctl status pd-di` — inactive
   - Volumes evacuated (if `di_decommission_evacuate_data: true`)

**Expected:** Node cleanly removed from Hammerspace, services stopped.

### T11.2 — Decommission Dry Run

**Steps:**
1. Run: `ansible-playbook decommission_di.yml --limit <di-node> --check`
2. Verify: no changes made, plan displayed

**Expected:** Dry run shows what would happen without executing.

---

## 12. Architecture-Aware Package Selection

### T12.1 — x86_64 Package Selection

**Objective:** Only x86_64 RPMs are copied to an x86_64 target.

**Prerequisites:** Both x86_64 and aarch64 RPMs in `payload/`.

**Steps:**
1. Deploy to an x86_64 DI node
2. Verify: only `*.x86_64.rpm` and `*.noarch.rpm` files copied to target
3. Verify: no `*.aarch64.rpm` files on target

**Expected:** Correct architecture packages selected.

### T12.2 — aarch64 Package Selection

**Steps:**
1. Deploy to an aarch64 DI node
2. Verify: only `*.aarch64.rpm` and `*.noarch.rpm` files copied
3. Verify: `ansible_architecture` reported as `aarch64` in precheck output

**Expected:** ARM packages selected, x86 packages ignored.

### T12.3 — Mixed Architecture Fleet

**Objective:** Deploy to x86_64 and aarch64 nodes in the same run.

**Steps:**
1. Add both x86_64 and aarch64 nodes to `di_nodes`
2. Have both architecture RPMs in `payload/`
3. Run deployment
4. Verify: each node got the correct architecture packages

**Expected:** Architecture auto-detected per node, correct packages deployed.

---

## 13. Anvil Overload Protection

### T13.1 — Serial Play

**Objective:** `hammerspace_serial: 2` processes 2 nodes at a time.

**Steps:**
1. Set `hammerspace_serial: 2` with 6+ nodes
2. Run deployment with `-vv`
3. Observe ansible output: nodes processed in batches of 2

**Expected:** Hammerspace integration runs on 2 nodes at a time, not all at once.

### T13.2 — Task Queue Throttling

**Objective:** Verify task queue monitoring prevents overwhelming Anvil.

**Steps:**
1. Deploy 10+ nodes simultaneously (serial=0)
2. Monitor Hammerspace task queue: `curl -sk -u admin:pass https://<ANVIL>:8443/mgmt/v1.2/rest/tasks?spec=status%3Deq%3DQUEUED`
3. Verify: playbook pauses when queue exceeds `hammerspace_max_queued_tasks`

**Expected:** Volume adds pause when queue is full, resume when drained.

---

## 14. Tier 0 Host Reset

### T14.1 — Full Host Reset

**Objective:** Verify `reset-tier0-host.yml` returns a node to bare-metal state.

**Prerequisites:** Fully deployed Tier 0 node.

**Steps:**
1. Run: `ansible-playbook reset-tier0-host.yml --limit <node> -e reset_confirm=true`
2. Verify:
   - Hammerspace: node and volumes removed
   - `cat /proc/mdstat` — no RAID arrays
   - `mount | grep hammerspace` — no mounts
   - `exportfs -v` — no Tier 0 exports
   - `systemctl list-units 'hammerspace-*'` — no guard services

**Expected:** Host completely reset, ready for re-provisioning.

### T14.2 — Reset with blkdiscard

**Steps:**
1. Run: `ansible-playbook reset-tier0-host.yml --limit <node> -e reset_confirm=true -e reset_run_blkdiscard=true`
2. Verify: drives fully blanked (fastest re-provision)

**Expected:** NVMe drives discarded in addition to superblock wipe.

### T14.3 — Reset Without Hammerspace

**Objective:** Reset local storage when Anvil is unreachable.

**Steps:**
1. Run: `ansible-playbook reset-tier0-host.yml --limit <node> -e reset_confirm=true -e reset_hammerspace_cleanup=false`
2. Verify: local teardown completes, Hammerspace API not contacted

**Expected:** Local cleanup succeeds without Anvil access.

### T14.4 — Re-provision After Reset

**Objective:** Verify a reset node can be fully re-provisioned.

**Steps:**
1. Reset node (T14.1)
2. Re-deploy: `ansible-playbook site.yml --limit <node>`
3. Verify: full Tier 0 setup completes, node re-registered in Hammerspace

**Expected:** Clean re-provision after reset.

---

## 15. OCI Run Command Deployment

### T15.1 — Instance Discovery

**Objective:** `oci_deploy.py` discovers GPU instances in a compartment.

**Steps:**
1. Run: `python3 oci_deploy.py --compartment-id <OCID> --dry-run`
2. Verify: table of instances with names, IPs, shapes, agent status

**Expected:** All running instances listed with correct details.

### T15.2 — Shape Filter

**Steps:**
1. Run: `python3 oci_deploy.py --compartment-id <OCID> --shape "BM.GPU.GB200-v3.4" --dry-run`
2. Verify: only matching shape instances shown

**Expected:** Non-matching shapes filtered out.

### T15.3 — Skip Registered Instances

**Steps:**
1. Register some instances in Hammerspace first
2. Run: `python3 oci_deploy.py --compartment-id <OCID> --hs-host <ANVIL> --hs-password-file ~/.hs_password --skip-registered --dry-run`
3. Verify: registered instances show "HS Reg: Yes" and are excluded from targets

**Expected:** Only unregistered instances targeted.

### T15.4 — Single Instance Deployment

**Objective:** Deploy to one specific instance via Run Command.

**Steps:**
1. Run: `python3 oci_deploy.py --compartment-id <OCID> --instance-id <OCID> --vault-secret-id <SECRET> --yes`
2. Wait for completion (polls automatically)
3. Verify on instance: `/var/log/tier0-bootstrap.log` — successful run
4. Verify in Hammerspace: node registered, volumes added

**Expected:** Single instance fully provisioned via Run Command, no SSH.

### T15.5 — Parallel Deployment

**Steps:**
1. Run: `python3 oci_deploy.py --compartment-id <OCID> --parallel 5 --yes`
2. Verify: up to 5 instances deploying concurrently
3. Verify summary: SUCCESS/FAILED count for each instance

**Expected:** Parallel execution, all instances provisioned.

### T15.6 — OCI Vault Credentials

**Objective:** Verify password fetched from OCI Vault (not in Run Command payload).

**Steps:**
1. Store password in OCI Vault
2. Run: `python3 oci_deploy.py --compartment-id <OCID> --vault-secret-id <SECRET> --instance-id <OCID> --yes`
3. Verify: Run Command payload contains `OCI_VAULT_SECRET_OCID` but NOT the actual password
4. Verify: bootstrap log shows "Fetching Hammerspace password from OCI Vault"

**Expected:** Password never in Run Command payload, fetched at runtime on instance.

---

## 16. OCI Events + Functions (Auto-Provisioning)

### T16.1 — Terraform Deployment

**Objective:** Deploy the event-driven infrastructure.

**Steps:**
1. `cd oci-function/terraform`
2. `cp terraform.tfvars.example terraform.tfvars` and customize
3. `terraform init && terraform plan`
4. `terraform apply`
5. Verify in OCI Console:
   - Function Application exists with correct env vars
   - Function deployed with correct image
   - Event Rule active with correct condition

**Expected:** All OCI resources created.

### T16.2 — Event-Triggered Deployment

**Objective:** Launching a GPU instance auto-triggers Tier 0 + DI deployment.

**Steps:**
1. Launch a new GPU instance in the monitored compartment
2. Wait 5-10 minutes
3. Check function logs (OCI Logging or `fn invoke`)
4. Verify function log: "Run Command sent to <instance>"
5. Verify on instance: `/var/log/tier0-bootstrap.log` — deployment completed
6. Verify in Hammerspace: node registered, volumes added

**Expected:** Fully automated — no human intervention from instance launch to Hammerspace registration.

### T16.3 — Shape Filter (Event-Driven)

**Objective:** Non-GPU instances in the same compartment are ignored.

**Steps:**
1. Set `SHAPE_FILTER=BM.GPU.GB200-v3.4` in function config
2. Launch a non-GPU instance (e.g., VM.Standard.E4.Flex) in the same compartment
3. Check function logs

**Expected:** Function log shows "SKIPPED_SHAPE" — no Run Command sent.

### T16.4 — Instance Reboot Does Not Re-trigger

**Objective:** Rebooting an instance does NOT re-trigger deployment.

**Steps:**
1. Deploy a GPU instance (event triggers deployment)
2. Reboot the instance
3. Check function logs

**Expected:** No new function invocation — `launchinstance.end` only fires on initial launch.

### T16.5 — Agent Not Ready

**Objective:** Function retries when Cloud Agent is not immediately ready.

**Steps:**
1. Launch a GPU instance
2. Check function logs for agent readiness retries

**Expected:** Function retries up to 10 times (150s), then either succeeds or returns `SKIPPED_AGENT_NOT_READY`.

### T16.6 — OS Readiness Wait

**Objective:** Bootstrap script waits for OS to be fully ready before running Ansible.

**Steps:**
1. Trigger deployment on a fresh instance
2. Check `/var/log/tier0-bootstrap.log`:
   - "Waiting for systemd..."
   - "Waiting for cloud-init to finish..."
   - "Waiting for package manager..."
   - "OS ready"

**Expected:** Bootstrap waits for systemd + cloud-init + package manager before proceeding.

---

## 17. Co-located Tier 0 + DI

### T17.1 — Same Host in Both Groups

**Objective:** Deploy Tier 0 storage and DI on the same instance.

**Inventory:**
```yaml
storage_servers:
  hosts:
    node101:
      ansible_host: 10.200.101.216
di_nodes:
  hosts:
    node101:
      ansible_host: 10.200.101.216
      di_node_ip: 10.200.101.216
      di_node_name: node101-mover
```

**Steps:**
1. Run: `ansible-playbook site.yml -i inventory.yml -e deploy_di=true`
2. Verify:
   - RAID + NFS + Hammerspace volumes configured (Tier 0)
   - pd-di running (DI)
   - Both node types registered in Hammerspace (OTHER + MOVER_EXT)
   - Firewall: NFS ports + DI ports 9095/9096 open
   - `add_node.py` used `?ignoreIpConflicts=true` for shared IP

**Expected:** Both Tier 0 and DI running on the same host without conflicts.

---

## 18. Idempotency

### T18.1 — Full Re-run (Tier 0)

**Steps:**
1. Run full deployment
2. Run again immediately
3. Verify: no errors, no changes (all tasks show "ok" or "skipped")

**Expected:** Second run is a no-op.

### T18.2 — Full Re-run (DI)

**Steps:**
1. Run DI deployment
2. Run again
3. Verify: registration skipped ("already registered"), services already running

**Expected:** Second run is a no-op.

### T18.3 — Bootstrap Script Re-run (OCI Run Command)

**Steps:**
1. Deploy via `oci_deploy.py`
2. Send Run Command again to the same instance
3. Verify: `git clone || git pull` succeeds, ansible re-runs without errors

**Expected:** Idempotent — safe to re-trigger.

---

## 19. Edge Cases and Failure Scenarios

### T19.1 — Anvil Unreachable During Deployment

**Steps:**
1. Deploy with an incorrect `hammerspace_api_host`
2. Verify: Tier 0 storage setup (RAID, NFS) completes successfully
3. Verify: Hammerspace integration fails with clear error message

**Expected:** Storage setup succeeds, API integration fails gracefully.

### T19.2 — DI Package Missing

**Steps:**
1. Empty the `payload/` directory
2. Run DI deployment
3. Verify: clear error message about missing pd-di RPM

**Expected:** Playbook fails with actionable error.

### T19.3 — Wrong Architecture RPMs

**Steps:**
1. Put only aarch64 RPMs in `payload/`
2. Deploy to an x86_64 node
3. Verify: error about no matching architecture RPMs

**Expected:** Fails with clear architecture mismatch message.

### T19.4 — Cloud Agent Not Running

**Steps:**
1. Run `oci_deploy.py` against an instance with Cloud Agent disabled
2. Verify: instance shown as "Agent: N/A" in plan
3. Verify: instance skipped, not targeted

**Expected:** Agent-less instances excluded from deployment.

### T19.5 — OCI Vault Secret Inaccessible

**Steps:**
1. Run with `--vault-secret-id` pointing to a non-existent or no-access secret
2. Verify: bootstrap log shows "OCI Vault fetch failed, falling back"

**Expected:** Graceful fallback with warning.

### T19.6 — Concurrent Deployments (Race Condition)

**Steps:**
1. Launch 10 instances simultaneously
2. Event function triggers 10 parallel Run Commands
3. Verify: all instances deploy without conflicts
4. Verify: no duplicate Hammerspace registrations

**Expected:** All instances provision independently without interference.

### T19.7 — Instance Terminated During Deployment

**Steps:**
1. Start deployment via Run Command
2. Terminate the instance mid-deployment
3. Verify: `oci_deploy.py` reports FAILED/TIMED_OUT for that instance
4. Verify: other instances unaffected

**Expected:** Terminated instance reported as failed, no cascade failure.

---

## Test Environment Requirements

| Component | Minimum |
|-----------|---------|
| OCI compartment with GPU instances | 2+ instances |
| Hammerspace cluster (Anvil) | 1 cluster with API access |
| Ansible controller | 1 (laptop, bastion, or cloud shell) |
| OCI Vault | 1 secret (for Run Command / Functions tests) |
| Both x86_64 and aarch64 RPMs | In `payload/` directory |
| OCI Container Registry | For function image (Functions tests) |

---

## 20. Data Mobility and Integrity

Verify that data written to Hammerspace-managed storage is accessible, movable, and intact across nodes and AZs.

### T20.1 — File Create and MD5 Verify (Single Node)

**Objective:** Write files to a Tier 0 volume and verify data integrity.

**Steps:**
```bash
# On a Tier 0 node — create test files of various sizes
cd /hammerspace/hsvol0

# Small file (1KB)
dd if=/dev/urandom of=testfile_1k.bin bs=1K count=1
md5sum testfile_1k.bin > testfile_1k.md5

# Medium file (100MB)
dd if=/dev/urandom of=testfile_100m.bin bs=1M count=100
md5sum testfile_100m.bin > testfile_100m.md5

# Large file (1GB)
dd if=/dev/urandom of=testfile_1g.bin bs=1M count=1024
md5sum testfile_1g.bin > testfile_1g.md5

# Verify checksums
md5sum -c testfile_1k.md5
md5sum -c testfile_100m.md5
md5sum -c testfile_1g.md5
```

**Expected:** All three `md5sum -c` checks pass with `OK`.

### T20.2 — File Integrity After NFS Re-export

**Objective:** Verify data survives NFS export reload.

**Steps:**
```bash
# 1. Create and checksum files (as T20.1)
md5sum /hammerspace/hsvol0/testfile_*.bin > /tmp/pre_reexport.md5

# 2. Reload NFS exports
ansible-playbook site.yml -i inventory.yml --tags nfs-exports

# 3. Verify checksums from another node (NFS client)
mount -t nfs4 <tier0-ip>:/hammerspace/hsvol0 /mnt/test
md5sum -c /tmp/pre_reexport.md5
umount /mnt/test
```

**Expected:** Checksums match after NFS re-export.

### T20.3 — Data Mobility via Hammerspace Share (Cross-Node)

**Objective:** Write data via a Hammerspace share and verify it's accessible from another node through file mobility.

**Prerequisites:** Hammerspace share created (e.g., `/checkpoints`), at least 2 Tier 0 nodes.

**Steps:**
```bash
# 1. From a client or Anvil — mount the Hammerspace share
mount -t nfs4 <anvil-ip>:/checkpoints /mnt/checkpoints

# 2. Create test dataset
mkdir -p /mnt/checkpoints/mobility_test
for i in $(seq 1 100); do
    dd if=/dev/urandom of=/mnt/checkpoints/mobility_test/file_${i}.bin bs=1M count=10 2>/dev/null
done

# 3. Checksum all files
cd /mnt/checkpoints/mobility_test
md5sum file_*.bin > /tmp/mobility_checksums.md5
echo "Files: $(ls -1 | wc -l), Total: $(du -sh . | awk '{print $1}')"

# 4. Verify checksums (should pass — data just written)
md5sum -c /tmp/mobility_checksums.md5
```

**Expected:** 100 files created, all checksums pass.

### T20.4 — Data Integrity After Objective Change (Place-on-Tier0)

**Objective:** Change a share's placement objective and verify data integrity after file instances are moved by DI.

**Prerequisites:** DI running, at least 2 Tier 0 nodes in different AZs.

**Steps:**
```bash
# 1. Create files and checksum (as T20.3)
md5sum /mnt/checkpoints/mobility_test/file_*.bin > /tmp/pre_mobility.md5

# 2. Change placement objective in Hammerspace
#    (moves file instances to a different Tier 0 node via DI)
anvil> share-update --name checkpoints --objective place-on-az1

# 3. Wait for mobility to complete
#    Monitor via Hammerspace GUI or:
anvil> task-list --recent 10

# 4. Verify checksums after mobility
md5sum -c /tmp/pre_mobility.md5

# 5. Check which node holds the data now
anvil> file-instance-list --path /checkpoints/mobility_test/file_1.bin
```

**Expected:** All checksums pass after DI moves file instances to a different node.

### T20.5 — Large File Mobility with MD5 Verification

**Objective:** Move a large file (10GB+) between AZs and verify integrity.

**Steps:**
```bash
# 1. Create a large file
dd if=/dev/urandom of=/mnt/checkpoints/large_file_10g.bin bs=1M count=10240
md5sum /mnt/checkpoints/large_file_10g.bin > /tmp/large_file.md5
echo "MD5: $(cat /tmp/large_file.md5)"

# 2. Note current placement
anvil> file-instance-list --path /checkpoints/large_file_10g.bin

# 3. Change objective to force movement
anvil> share-update --name checkpoints --objective place-on-az2

# 4. Wait for mobility (large file may take several minutes)
watch 'anvil task-list --recent 5'

# 5. Verify integrity
md5sum -c /tmp/large_file.md5

# 6. Confirm new placement
anvil> file-instance-list --path /checkpoints/large_file_10g.bin
```

**Expected:** 10GB file moves between AZs, MD5 matches after mobility.

### T20.6 — Concurrent Write + Mobility Stress Test

**Objective:** Verify data integrity when files are being written while DI is moving other files.

**Steps:**
```bash
# Terminal 1: Continuous file creation
for i in $(seq 1 50); do
    dd if=/dev/urandom of=/mnt/checkpoints/stress/write_${i}.bin bs=1M count=50 2>/dev/null
    md5sum /mnt/checkpoints/stress/write_${i}.bin >> /tmp/stress_checksums.md5
    echo "Written: file $i"
done

# Terminal 2: While writes are happening, trigger objective change
sleep 10  # Let some files land first
anvil> share-update --name checkpoints --objective place-on-az1
sleep 30
anvil> share-update --name checkpoints --objective place-on-az2

# After both complete:
md5sum -c /tmp/stress_checksums.md5
```

**Expected:** All checksums pass despite concurrent writes and mobility.

### T20.7 — Multi-Volume Data Distribution

**Objective:** Verify data written across multiple Tier 0 volumes on the same node.

**Steps:**
```bash
# Write to each volume on a Tier 0 node
for vol in /hammerspace/hsvol0 /hammerspace/hsvol1 /hammerspace/hsvol2 /hammerspace/hsvol3; do
    dd if=/dev/urandom of=${vol}/integrity_test.bin bs=1M count=512 2>/dev/null
    md5sum ${vol}/integrity_test.bin
done | tee /tmp/multivol_checksums.md5

# Verify all volumes
md5sum -c /tmp/multivol_checksums.md5
```

**Expected:** All volume checksums pass.

### T20.8 — Data Integrity After DI Decommission + Re-deploy

**Objective:** Verify data remains intact when a DI node is decommissioned and a replacement is deployed.

**Steps:**
```bash
# 1. Checksum existing data
md5sum /mnt/checkpoints/mobility_test/file_*.bin > /tmp/pre_decommission.md5

# 2. Decommission the DI node (evacuates data first)
ansible-playbook decommission_di.yml -i inventory.yml --limit mover101 \
  -e hammerspace_api_password="YourPassword"

# 3. Verify data still accessible (from client mount)
md5sum -c /tmp/pre_decommission.md5

# 4. Deploy replacement DI
ansible-playbook site.yml -i inventory.yml --tags di --limit mover102 \
  -e deploy_di=true -e hammerspace_api_password="YourPassword"

# 5. Trigger mobility to verify new DI works
anvil> share-update --name checkpoints --objective place-on-az1

# 6. Verify after mobility with new DI
md5sum -c /tmp/pre_decommission.md5
```

**Expected:** Data intact after decommission, accessible after re-deploy, checksums pass after mobility with new DI.

### T20.9 — Cross-AZ Data Verification

**Objective:** Write data in one AZ, move to another AZ via objective, verify integrity.

**Prerequisites:** At least 2 AZs with Tier 0 nodes.

**Steps:**
```bash
# 1. Create files with AZ1 objective
anvil> share-update --name checkpoints --objective place-on-az1

for i in $(seq 1 20); do
    dd if=/dev/urandom of=/mnt/checkpoints/az_test/file_${i}.bin bs=1M count=100 2>/dev/null
done
md5sum /mnt/checkpoints/az_test/file_*.bin > /tmp/az_checksums.md5

# 2. Verify all files are on AZ1 nodes
anvil> file-instance-list --path /checkpoints/az_test/file_1.bin
# Should show AZ1:nodeXXX

# 3. Move to AZ2
anvil> share-update --name checkpoints --objective place-on-az2

# 4. Wait for mobility
watch 'anvil task-list --recent 10'

# 5. Verify integrity after cross-AZ move
md5sum -c /tmp/az_checksums.md5

# 6. Verify placement changed
anvil> file-instance-list --path /checkpoints/az_test/file_1.bin
# Should show AZ2:nodeXXX
```

**Expected:** Files move from AZ1 to AZ2, all MD5 checksums pass.

### T20.10 — Checkpoint Simulation (AI Workload Pattern)

**Objective:** Simulate an AI training checkpoint workflow — write large files quickly, verify integrity, then move to a different tier/AZ.

**Steps:**
```bash
# 1. Simulate checkpoint write (parallel, large files)
mkdir -p /mnt/checkpoints/ckpt_epoch_001
time (
    for shard in $(seq 0 7); do
        dd if=/dev/urandom of=/mnt/checkpoints/ckpt_epoch_001/shard_${shard}.pt \
           bs=1M count=2048 2>/dev/null &
    done
    wait
)
echo "Checkpoint write time: $SECONDS seconds"

# 2. Checksum all shards
md5sum /mnt/checkpoints/ckpt_epoch_001/shard_*.pt > /tmp/ckpt_001.md5
echo "Total size: $(du -sh /mnt/checkpoints/ckpt_epoch_001/ | awk '{print $1}')"

# 3. Simulate tier migration (move checkpoint to archive AZ)
anvil> share-update --name checkpoints --objective place-on-az2

# 4. Wait for mobility
watch 'anvil task-list --recent 10'

# 5. Verify integrity after migration
md5sum -c /tmp/ckpt_001.md5

# 6. Read back verification (simulate checkpoint restore)
time (
    for shard in $(seq 0 7); do
        md5sum /mnt/checkpoints/ckpt_epoch_001/shard_${shard}.pt > /dev/null &
    done
    wait
)
echo "Checkpoint read-back time: $SECONDS seconds"
```

**Expected:** 8 x 2GB shards written in parallel (16GB total), all MD5 checksums pass after cross-AZ migration, read-back completes successfully.

---

## Quick Test Matrix

| # | Test | Command | Pass Criteria |
|---|------|---------|---------------|
| 1 | Tier 0 full deploy | `ansible-playbook site.yml` | RAID + NFS + Hammerspace OK |
| 2 | DI host deploy | `site.yml --tags di -e deploy_di=true` | pd-di active, registered |
| 3 | DI container deploy | `site.yml --tags di -e deploy_di=true -e di_deployment_type=container` | Container running, NRestarts=0 |
| 4 | DI pre-deploy | `site.yml --tags di -e deploy_di=true -e di_activate=false` | Installed, NOT running |
| 5 | DI activate | `site.yml --tags di-activate -e deploy_di=true -e di_activate=true` | Services started, registered |
| 6 | DI decommission | `decommission_di.yml --limit <node>` | Removed from Hammerspace |
| 7 | Host reset | `reset-tier0-host.yml -e reset_confirm=true` | Clean bare-metal state |
| 8 | OCI Run Command | `oci_deploy.py --compartment-id <OCID> --dry-run` | Instances discovered |
| 9 | Event-driven | Launch GPU instance | Auto-deployed, no human action |
| 10 | Idempotent re-run | Run any playbook twice | No errors, no changes |
| 11 | File create + MD5 | `dd` + `md5sum` on Tier 0 volume | Checksums match |
| 12 | Cross-AZ mobility | Change objective, `md5sum -c` after move | All MD5s pass after DI moves data |
| 13 | Checkpoint simulation | 8 x 2GB parallel write, migrate, verify | 16GB written, moved, checksums pass |
