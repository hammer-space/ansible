# Integration tests

> **Audience: engineers changing the automation.** These tests check the
> playbook's own logic on a laptop; they are not part of a deployment. If you
> are deploying Tier 0, start at [README.md](../../README.md).

Self-contained Ansible playbooks that exercise critical safety paths
without needing remote hosts. They run against `localhost` with
`connection: local`, so they are safe to run on a developer laptop
or in CI.

## Single-entry runner

`tests/run_all.sh` runs every check in this directory plus the static
checks (YAML parsing, `bash -n`, `shellcheck`, `yamllint`,
`ansible-playbook --syntax-check`). Wire it into CI:

```bash
./tests/run_all.sh
```

Exits non-zero if anything fails. Prints a per-step summary at the end.

## `test_repo_root.yml`

Regression test for the `playbook_dir` → `repo_root` migration. The
`plays/*.yml` imports cause `playbook_dir` to resolve to `.../plays/`
instead of the repo root, breaking any task that does
`{{ playbook_dir }}/X` for files like `container/Containerfile`,
`vars/vault.yml`, `payload/`, or `gpu_fabric_data.txt`. Three production
incidents (2026-05-15, 2026-05-18) traced back to this pattern.

Fix: `vars/main.yml` defines `repo_root: "{{ playbook_dir }}/.."` once;
every controller-side file path uses `{{ repo_root }}/...`.

Test cases:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | `vars/main.yml` contains the literal `repo_root: "{{ playbook_dir }}/.."` | Pass — definition present |
| 2 | `repo_root` chains through `plays/..` → resolves to repo root | Pass for vault.yml + Containerfile paths |
| 3 | **Audit:** grep for stray `playbook_dir` references in `roles/` and `plays/` | Empty — every controller path uses `repo_root` |
| 4 | Sanity: `container/`, `vars/`, `payload/` actually exist at the repo root | Pass |

TEST 3 is the regression-prevention case: if anyone re-introduces
`{{ playbook_dir }}/X` in a role or play, this test fails before merge.

## `test_protected_vs_md_split.yml`

Regression test for the 2026-05-18 "0 RAID arrays" incident (Peter's
re-run on hosts that already had working arrays). The boot-drive
safety overhaul had put md array members in `_protected_disks`, which
excluded all data disks from discovery on re-runs.

Test cases:

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | OLD merged behavior (md members in `_protected_disks`) | Reproduces the bug — only fresh disks survive discovery |
| 2 | NEW split (md members in `_md_member_disks` only) | All non-boot disks survive discovery; md members listed informationally |
| 3 | Boot disk somehow leaks into candidate list | Safety gate in `build_raid_arrays.yml` still fires (no regression of boot protection) |
| 4 | End-to-end: discovery + `raid_setup._device_remap` | `mount_points[0].device` correctly remapped from `/dev/md0` to `/dev/md127` |
| 5 | `/dev/md127` mounted at `/hammerspace/hsvol0` (2026-05-20 regression) | Members `sdb`/`sdc` NOT classified as protected — `detect_boot_device.yml` only walks system mountpoints, not data mounts |
| 6 | Azure ephemeral resource disk mounted at `/mnt` | `sdb` excluded from discovery; data disks `sdc`/`sdd` survive. Also audits that `/mnt` + `/mnt/resource` remain in the `SYSTEM_MOUNTS` allow-list |

## `test_azure_az_map.yml`

Covers Azure AZ derivation, added with Azure support on 2026-08-24.

Azure exposes two different placement values that are easy to conflate:
`zone` (availability zone, **1-based**, and an **empty string** rather than
absent on non-zonal VMs) and `platformFaultDomain` (**0-based**, IMDS only).
Getting either wrong is silent: an `is defined` test on `zone` makes every
non-zonal node render a bare `AZ`, and a missing `+1` on the fault domain
renders `AZ0` — a zone label Hammerspace never assigns.

The AZ expression lives once in `vars/main.yml` as `_cloud_az_detected`. This
test loads the **real** expression via `include_vars` rather than copying it,
so the assertions cannot drift from production.

Test cases:

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | `vars/main.yml` defines `_cloud_az_detected` + the Azure IMDS vars | Present; `hammerspace_azure_imds_az` defaults `false` |
| 2 | `azure_zone: "2"` | → `AZ2` (1-based, verbatim) |
| 3 | `azure_zone: "3"` + `azure_fault_domain: "0"` | → `AZ3` — zone outranks fault domain |
| 4 | Non-zonal VM: `azure_zone: ""` + `azure_fault_domain: "0"` / `"2"` | → `AZ1` / `AZ3` — empty zone skipped, fault domain `+1`. Never `AZ0` or bare `AZ` |
| 5 | `oci_fault_domain: FAULT-DOMAIN-3`, incl. alongside `azure_zone` | → `AZ3` — OCI unchanged and still highest priority |
| 6 | No cloud metadata at all | Renders empty; `az_map` falls through to name prefix → group → `hammerspace_default_az` |
| 7 | `hammerspace_node_az` set; volume-name prefix on Azure | Override wins; prefix renders `AZ2:` on Azure and `''` with no metadata |
| 8 | **Audit:** all three call sites, include order, inventory, requirements | `az_map.yml` / `add_volume.yml` / `plays/storage.yml` all delegate to `_cloud_az_detected`; `azure_imds_az.yml` is included *before* `az_map.yml`; `inventory.azure.yml` uses `azure.azcollection.azure_rm`; `requirements.yml` pins `azure.azcollection` |
| 9 | IMDS request failed (no `json` key) / sparse payload / full payload | Parses to `{}` and empty strings without raising; full payload → `AZ2`. **Audits** that `azure_imds_az.yml` uses no `result.attr \| default(...)` — the 2026-07-21 sunrpc failure mode |

TEST 8 is the regression-prevention case: if anyone re-derives the AZ locally
from `oci_fault_domain` in a call site again, Azure hosts silently fall back to
`hammerspace_default_az` there — this test fails before merge.

**Second gotcha:** the IMDS request uses `failed_when: false`, so on a
non-Azure host the registered result has **no `json` key at all**. Writing
`_azure_imds_raw.json | default({})` would hard-fail with `'dict object' has
no attribute 'json'` before `default` ever runs — the exact 2026-07-21 sunrpc
failure mode. TEST 9's audit case blocks that from coming back.

**Gotcha this test encodes:** `ansible.cfg` enables `jinja2_native`, so
`_cloud_az_detected` renders to `None`, not `""`, when no branch matches. A
plain `| default('')` does not catch `None`, and `None | trim` stringifies to
the literal `"None"` — which would become the AZ label. Every consumption site
must use `| default('', true) | trim`.

## `test_vault_loading.yml`

Regression test for the vault-loading failure mode, hit 2026-08-25 on an Azure
CycleCloud controller running `preflight_check.yml`.

`vars/main.yml` maps the password to the vault variable:

```yaml
hammerspace_api_password: "{{ vault_hammerspace_api_password }}"
```

So loading `vars/main.yml` alone leaves the key present but its **value**
referencing an undefined var — and `hammerspace_api_password is defined`
evaluates **false**. The resulting error tells you to fix `vars/main.yml`,
which is the one file that is not at fault. `preflight_check.yml` loaded
`vars/main.yml` via `vars_files` but never included `vars/vault.yml`; every
other API-touching playbook already did.

Same class as the 2026-05-15 incident, where the `plays/` split broke
`{{ playbook_dir }}/vars/vault.yml` resolution and the password went silently
undefined.

Test cases:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | A var whose value references an undefined var | `is defined` is **false**; true once the vault var exists (root cause) |
| 2 | `vars/main.yml` still maps password → vault var | Present — the premise the audit rests on |
| 3 | **Audit:** every playbook loading `vars/main.yml` + using API creds | All also load `vars/vault.yml` |
| 4 | `preflight_check.yml` specifically | Loads the vault via `repo_root`, with `failed_when: false` |

TEST 3 is the regression-prevention case: a new playbook that talks to the API
without loading the vault fails here rather than in front of a customer.

**Gotcha this test encodes:** the audit requires `vault.yml` to appear near an
`include_vars` / `vars_files` key, not merely somewhere in the file. A bare
`'vault.yml' in text` check is a false negative — `preflight_check.yml` now
mentions `vault.yml` in its own fail_msg, and that alone was enough to make a
naive audit pass while the include was absent. Found by negative-testing this
test; TEST 4 caught the reverted fix and TEST 3 did not.

## `test_run_sh_locale.sh`

Unit tests for `scripts/run.sh`. Pure bash, no Ansible required. Two sections:

### Locale fallback (8 cases)

- en_US.UTF-8 present → picks it first
- en_US.UTF-8 missing → falls back to C.UTF-8
- lowercase variants (utf8 vs UTF-8)
- ko_KR-only host → returns empty (triggers the install-instruction error path)
- empty locale list (broken libc)
- case-insensitive matching
- priority ordering when all 4 candidates are present

### ANSIBLE_LOG_PATH default (4 cases)

- No preset env vars → defaults to `${HOME}/logs/ansible-<timestamp>.log`
- `ANSIBLE_LOG_DIR=/tmp/x` → log path lands under `/tmp/x`
- User-set `ANSIBLE_LOG_PATH` wins over the default
- User-set `ANSIBLE_LOG_PATH` wins even when `ANSIBLE_LOG_DIR` is also set

Run directly:
```bash
bash tests/integration/test_run_sh_locale.sh
```

## `test_di_k8s_render.yml`

Renders the four Kubernetes manifest templates under
`roles/di/templates/` (namespace, image-pull secret, API-credentials
secret, DaemonSet) with mock vars and asserts the structural shape
required by the customer ask. No cluster, no kubeconfig.

Test cases:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | `namespace.yaml.j2` | PSA enforce/audit/warn = `privileged` |
| 2 | `image_pull_secret.yaml.j2` | type `kubernetes.io/dockerconfigjson`, base64 payload present |
| 3 | `api_credentials_secret.yaml.j2` | `HAMMERSPACE_API_*` keys present, `DI_IGNORE_IP_CONFLICTS=true` |
| 4 | `daemonset.yaml.j2` | hostNetwork, ClusterFirstWithHostNet, nodeSelector, tolerations include `nvidia.com/gpu`, priorityClassName, hostAliases, initContainer `register-node`, privileged + SYS_ADMIN/NET_ADMIN/SYS_PTRACE, ports 9095+9096 with `hostPort`, cgroup+modules hostPath, run+tmp emptyDir Memory, and **the API password is NOT in the rendered file** (only referenced via Secret envFrom) |

## `test_di_k8s_validation.yml`

Static-grep validation that the k8s wiring is in place across:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | `plays/di.yml` validator | accepts `'host', 'container', 'kubernetes'` |
| 2 | `container_runtime.yml` | accepts `containerd` as a passthrough (short-circuits via `end_host`) |
| 3 | `roles/di/tasks/main.yml` | k8s mode routes to `kubernetes_deploy.yml` + `kubernetes_status_label.yml`, tagged `di-k8s` + `di-k8s-status` |
| 4 | `decommission.yml` | dispatches `kubernetes_decommission.yml` when k8s mode is selected |

## `test_di_activate_tag.yml`

Tag-propagation regression (Peter's 2026-05-21 report) + extended for the
new `di-k8s` tag (2026-05-26).

Test cases:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Every `di-activate`-tagged `include_tasks` in `roles/di/tasks/main.yml` carries `apply: tags:` propagation | Pass |
| 2 | Every pre_task / post_task in `plays/di.yml` is tagged `[always]` | Pass |
| 3 | `ansible-playbook --list-tasks --tags di-activate site.yml` lists at least one activation task | Pass |
| 4 | Same propagation rule for the `di-k8s` tag | Pass |

## `test_raid_idempotency.yml`

Regression test for the 2026-05-15 EBUSY-on-re-run incident (Peter's bug),
where `mdadm --create /dev/md0 ... /dev/sdb /dev/sdc` failed because the
kernel had already auto-assembled the array as `/dev/md127` after a reboot
(initramfs/homehost mismatch). The role's name-only check
(`'md0' not in existing_arrays`) missed it and tried to recreate over the
already-busy members.

Test cases:

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | Both planned arrays already assembled under different names (md0→md127, md1→md126) | `mdadm --create` skipped for both; `raid_arrays` + `mount_points` device fields remapped to actual `/dev/mdN` |
| 2 | Fresh host, no md arrays exist | Both planned arrays survive the skip filter (no false-positive skips) |
| 3 | Partial overlap — md0 exists as md127, md1 is fresh | Only md1 is created; md0 is skipped |

The tests run the same Jinja expressions the role uses for `_array_md_lookup`,
`_existing_md_for_array`, `_arrays_already_present`, and `_device_remap` —
so a regression in the role logic shows up as a test-2 / test-3 failure.

## `test_boot_device_safety.yml`

Regression test for the 2026-05-14 boot-drive incident, where empty
boot-device detection caused `nvme_discovery` to plan `mkfs.xfs` on
the OS NVMe (kernel `EBUSY` was the only thing that saved it).

Test cases:

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | Run `detect_boot_device.yml` against the actual control host (via `include_role`, which the `script` module needs to resolve `files/`) | `_protected_disks` is non-empty; `_detected_boot_device` is in the list |
| 2 | `_protected_disks: []` and `allow_empty_protected_disks: false` | Playbook hard-fails before any mkfs |
| 3 | `_protected_disks: []` and `allow_empty_protected_disks: true` | Override bypasses the gate (diskless/netboot escape hatch) |
| 4 | Boot drive sneaks into `nvme_devices` | Safety gate in `build_raid_arrays.yml` fires |
| 5 | Clean `nvme_devices` with no overlap | Safety gate does NOT fire (no false positives) |
| 6 | The real `files/scan_disks.sh` run under `LC_ALL=ko_KR.UTF-8` (Keith's host) | Still emits `PROTECTED` lines — the script pins `LC_ALL=C` internally |

## `test_scan_disks.sh`

Unit tests for `roles/nvme_discovery/files/scan_disks.sh`, the protected-disk
resolver.

Regression test for the **2026-08-26** Azure HBv3 failure: a normal host hard-
stopped with `Boot / protected-disk detection returned an EMPTY list`. The old
detection took `findmnt -n -o SOURCE <mp>` and gated on `[ -b "$src" ]`, so any
mount whose `SOURCE` string is not a literal, existing block-device path was
silently dropped — `/dev/root`, a btrfs `/dev/sda2[/@]` subvolume, an
unevaluated `UUID=`. Every `SYSTEM_MOUNTS` entry dropped out, the list came back
empty, and the boot-drive gate refused to run.

The fix resolves through `MAJ:MIN` (which the kernel always reports) and walks
the sysfs topology. These tests drive the production functions directly through
the script's `--resolve-majmin` / `--walk` / `--path-to-name` hooks against a
fake `SCAN_SYSROOT` tree, so **they run on macOS too** — only the final
"real host scan" section self-skips on non-Linux.

| Group | Cases | Expected behavior |
|---|---|---|
| `majmin_to_name` | `8:2`, `8:1`, `8:0`, `253:0`, `0:24`, empty, garbage, unknown | Real MAJ:MIN → kernel name even when the `SOURCE` string is unusable; major `0` (tmpfs/overlay/nfs) and junk → empty |
| `walk_to_disks` | `sda2`, `sda`, `md127`, `dm-0`, missing, empty | partition → parent disk; md → members; LV → PV partition → disk; whole disk → itself |
| `path_to_name` | `sda2[/@]`, plain node, `/dev/mapper/*` symlink, `UUID=`, `overlay`, missing | btrfs subvol suffix stripped, symlink chains followed, non-paths → empty |
| `--disk-for-mount` | `/tmp`, `/nvme`, `/`, `/var`, `/dev/shm`, unmounted path | Resolves each mountpoint to its physical disk via a fake `/proc/self/mountinfo`; tmpfs and unmounted paths → empty |
| `--critical-disks` | the non-overridable refuse-list | Exactly `sda` + `sdd`; `nvme0n1` must **not** appear (or `/tmp` could never be released) and `sda` must (or releasing `/tmp` could expose the OS disk) |
| real host scan (Linux only) | full script run | ≥1 `PROTECTED` line, a `ROOT` line, and **no** pseudo device (`zram*`/`ram*`/`loop*`/`fd*`) in `PROTECTED` — a pseudo device must never be able to satisfy the non-empty gate on its own |

```bash
bash tests/integration/test_scan_disks.sh
```

## `test_check_mode_detection.yml`

Regression test for the **2026-08-26** dry-run failure: `site.yml --check`
aborted at the boot-drive gate with all four classification lists `(none)` and
`ok=28 changed=4 failed=1`.

`command` / `shell` / `script` do not support check mode, so under `--check`
Ansible **skips** them. Every detection task was skipped, each registered
variable came back without usable output, all lists resolved to `[]`, and the
safety gate — which exists to catch a *real* empty result — fired on a
*simulated* one. The playbook could not be dry-run at all.

Fix: `check_mode: false` on the read-only detection tasks (they only read
`/sys`, `/proc`, `findmnt`, `pvs`, `lscpu`), matching the idiom already used in
`roles/di/` and `roles/filesystem_setup/`.

The test also pins down a second trap. **The two kinds of skip register
different shapes:**

| Skip kind | Registered result | `result.stdout is defined` |
|---|---|---|
| `when`-skipped | `{skipped: true, changed: false}` | **false** |
| check-mode-skipped | `{skipped: true, stdout: '', rc: 0, …}` | **true**, but empty |

So the project's standing `result.attr is defined` guard (from the 2026-07-21
sunrpc incident) is necessary but **not sufficient** for check-mode skips — the
key exists, the guard passes, and the fallback never fires. That is why the
failure message's diagnostics block rendered as a blank line and the report
could not be diagnosed from the log. Guard with length, not just definedness.

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | Both skip shapes, `is defined` vs length-aware guard | `is defined` alone misses the check-mode shape; the length-aware guard handles both |
| 2 | **Audit:** `check_mode: false` count per discovery file | `detect_boot_device.yml` = 2, `cpu_optimization.yml` = 5, `main.yml` ≥ 2 |
| 3 | Real `scan_disks.sh` run with `check_mode: false` (Linux only) | Task is **not** skipped and emits `PROTECTED` lines |

```bash
ansible-playbook tests/integration/test_check_mode_detection.yml -i localhost, -c local
```

## `test_discovery_diagnostics.yml`

Regression test for the **2026-08-26** "Discovery produced 0 RAID arrays"
abort. Once check-mode detection was fixed the host classified correctly
(`boot_device detected: sda`) and then failed here, with a message that named
the suspects but reported nothing about what the host actually had.

Four distinct causes produce that abort and they need **different** fixes:

| Cause | Symptom | Fix |
|---|---|---|
| A | `storage_type: ssd\|hdd` and every `/dev/sd*` has the opposite `rotational` flag. Azure synthetic SCSI reports `rotational=1` even for Premium SSD | `storage_type: scsi` — scans `/dev/sd*` with **no** rotational filter |
| B | `storage_type` excludes NVMe on a host whose Tier 0 disks are local NVMe (Azure HBv3/L-series, AWS i-series, bare metal). The NVMe block is gated on `storage_type in [nvme, all]` and never ran | `storage_type: nvme` or `all` |
| C | No candidate disks on either path | check the topology dump |
| D | Disks found but all excluded | check protected list + `*_exclude_*` vars |

The fix snapshots `scsi_all_devices` into `_scsi_prefilter` **before** the
rotational filter overwrites it — without that, "no SCSI disks at all" and "the
filter dropped every disk" are indistinguishable — adds a failure-path topology
dump (`/sys/class/nvme`, `lsblk`, per-disk `rotational`), and names the most
likely cause explicitly.

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | Truth table over 6 scenarios | Each selects the correct cause. Case D2 (`storage_type: scsi`, disks survived the absent filter but were excluded) must report D, **not** B — this case caught a real bug in the branch ordering |
| 2 | **Audit:** source of `main.yml` | `_scsi_prefilter` snapshot precedes the filters; diagnostics task exists, reports NVMe + rotational, and is `check_mode: false`; all four remedies present |

TEST 1 mirrors the branch conditions rather than rendering the real template
(the message is inline in an `assert` and cannot be invoked standalone), so
TEST 2 audits the source to catch drift. Keep the two in step.

```bash
ansible-playbook tests/integration/test_discovery_diagnostics.yml -i localhost, -c local
```

## `test_release_ephemeral_mounts.yml`

Safety tests for the opt-in `release_ephemeral_mountpoints` feature, which
unmounts cloud-provisioned scratch so its disk can be used for Tier 0.

This is the most dangerous feature in the repo — it unmounts filesystems on a
path that ends in `mkfs` — so these tests exist to keep its rails intact. The
disk-resolution half (`--disk-for-mount` / `--critical-disks` against a fake
sysfs) lives in `test_scan_disks.sh`; this file covers the Ansible-side policy.

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | Refuse condition over a mixed plan | `nvme0n1` (scratch) releasable; `sda` (`/boot`) and `sdd` (`/var`) refused |
| 2 | Critical-backed mountpoint | Play **hard-fails** before any unmount — a condition that evaluates right but never aborts is worthless |
| 3 | Scratch-only plan | Gate does **not** fire (a refuse-list that blocks everything is equally broken) |
| 4 | **Audit** | Ships off by default (`release_ephemeral_mountpoints: []`, include gated); **no** `umount -l` / `--lazy` / `force: true` anywhere in the release path; `HARD_SYSTEM_MOUNTS` excludes `/tmp` but contains `/boot` + `/var`; `SYSTEM_MOUNTS` still contains `/tmp`; release runs **before** `detect_boot_device.yml` and the HW RAID block |

TEST 4 audits comment-stripped source: both files legitimately *discuss*
`umount -l` and `detect_boot_device.yml` in prose, so auditing raw text would
match explanation rather than behaviour.

```bash
ansible-playbook tests/integration/test_release_ephemeral_mounts.yml -i localhost, -c local
```

## `test_existing_mounts.yml`

Tests for **existing-mount mode** (`existing_mount_paths`): use filesystems
that are already built and mounted instead of discovering disks and building
RAID. An export directory is created inside each mount and wired into NFS +
Hammerspace; nothing else is touched.

Two failure modes in this mode are silent, which is why they are pinned here:

- **The `mp` export option.** It means "only export this path if it is itself a
  mountpoint". An export *subdirectory* is not one, so leaving `mp` in place
  produces **no export at all** — `/etc/exports` looks correct and `showmount`
  returns nothing. It must become `mountpoint=<parent>`, which keeps the "do
  not export if the array is unmounted" guarantee.
- **`filesystem_setup` runs `mkfs`** against every `mount_points` entry. If it
  is not gated off, it reformats the very filesystem the operator asked to
  reuse.

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| 1 | Subdirectory export vs bare-mountpoint export | Subdir drops `mp` and gains `mountpoint=<parent>`, other options untouched; a `subdir: ''` export keeps plain `mp` |
| 2 | Path normalisation | Trailing slash collapsed, empty subdir → the mount itself, and `/` stays `/` rather than normalising to `''`. Caught two real bugs: `'/' \| regex_replace('/+$','')` is the empty string, and `/` + subdir produced `//tier0` |
| 3 | `existing_mount_paths: ['/']` (runs the real task file) | Refused before anything is created — exporting `/` with `no_root_squash` would hand out the OS |
| 4 | **Audit** | Ships off by default; `nvme_discovery`, `raid_setup` and `filesystem_setup` all gated on `existing_mount_paths ... length == 0`; the task file contains no `mkfs` / `wipefs` / `mdadm` / `ansible.posix.mount` |

TEST 4 audits comment-stripped source — the task file's header *describes* what
it must never do, so auditing raw text matches prose instead of behaviour.

The `findmnt`-backed checks (is it really a mountpoint, which disk backs it)
need Linux; on macOS TEST 3 still fires because the system-mountpoint refusal
runs first and is a pure string check.

```bash
ansible-playbook tests/integration/test_existing_mounts.yml -i localhost, -c local
```

## Running

**Linux only** (except `test_scan_disks.sh`, which runs anywhere) — uses
`/proc/mounts`, `/proc/swaps`, `lsblk`, `findmnt`.
On macOS the playbook self-skips at the first task. Run on the Korean-locale
host that hit the original bug (`dskbd079`), inside CI, or any Linux dev box.

```bash
# Boot-drive safety
ansible-playbook tests/integration/test_boot_device_safety.yml -i localhost, -c local

# RAID idempotency (re-run after reboot)
ansible-playbook tests/integration/test_raid_idempotency.yml -i localhost, -c local

# Through the locale wrapper (recommended on non-English systems)
./scripts/run.sh playbook tests/integration/test_boot_device_safety.yml -i localhost, -c local
./scripts/run.sh playbook tests/integration/test_raid_idempotency.yml -i localhost, -c local
```

Exit code is non-zero if any test fails. The final task prints a report:

```
============================================================
BOOT-DRIVE SAFETY REGRESSION TEST REPORT
============================================================
PASSED (6):
  ✓ TEST 1: real detection produces non-empty list
  ✓ TEST 2: empty _protected_disks → hard-fail (expected behavior, caught by rescue)
  ...
FAILED: 0
============================================================
```

## `test_nfs_sunrpc_status.yml`

Regression test for the 2026-07-21 `'dict object' has no attribute 'rc'`
crash in `roles/nfs_setup/tasks/main.yml` (Display sunrpc pool_mode status).
`sunrpc_unload` is registered by a task *inside* a `when`-guarded block; when
the block is skipped (check mode / `pool_mode` already set /
`node_already_in_hammerspace`), the skipped task still defines the variable as
a `{skipped: true, ...}` dict **without an `rc` key**. The old status template
did `sunrpc_unload.rc | default(0)`, which hard-fails on strict ansible-core
versions before `default` can run. Fix guards the attribute:
`sunrpc_unload.rc is defined and sunrpc_unload.rc != 0`.

Test cases:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Skipped register result shape | Defined dict, `skipped: true`, **no `rc` key** (root-cause invariant, version-independent) |
| 2 | Fixed template + skipped dict | Renders `SAVED`, no crash |
| 3 | Fixed template + `rc=0` (unload succeeded) | Renders `SAVED` |
| 4 | Fixed template + `rc=1` (unload failed, module in use) | Renders `REBOOT` — the branch still fires |
| 5 | `sunrpc_unload` undefined + `pool_mode=pernode` | Renders `OK` — `is defined` short-circuits cleanly |

## `test_share_objective_status.yml`

Pins the 2026-09-18 field report: a write from a Tier 0 node went to the DSX
first. Two defects — `share_apply_objective.yml` never checked the HTTP status
of `objective-set` (a 4xx has no `Location`, so the poll was skipped and
"applied" was printed), and joining a volume group placed nothing because no
objective pointed at it. `tier0_place_on_group.yml` applies the group's
auto-created `place-on-<group>`.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Registered POST result: 404 body, code on last line, no `Location` | Status gate fires; the old task-status gate alone would not have (poll skipped) |
| 2 | 202 + `Location` | Passes the gate, proceeds to the poll — no false positive |
| 3 | **Audit** `share_apply_objective.yml` | POST is `no_log` + `failed_when: false`; a fail task gated on `status not in [200, 201, 202]` prints the body; no `-o /dev/stdout`; `run_once` on every task |
| 4 | Derivation | `place-on-<hammerspace_volume_group>`; shares from `hammerspace_shares`; overrides honoured; an EMPTY override string falls back |
| 5 | **Audit** `tier0_place_on_group.yml` + wiring | Included after `create_share.yml`, gated on the flag, `run_once`, GET-only on `/objectives/`, hard failure on `!= 200` / empty group / empty share list, lookup `no_log` + `failed_when: false`, defaults + docs present |
| 6 | One group / two groups / two groups + explicit objective | Guard fires only on ambiguity — a share objective is cluster-wide and names one group — and `hammerspace_tier0_place_on_objective` escapes it |

Mutation-tested: dropping the status gate, one `run_once`, `failed_when`, the
`!= 200` failure, the empty-share failure, or the multi-group guard each fails
the relevant test.

## `test_volume_group_join.yml`

The volume group join is a read-modify-write over the whole membership list,
so the dangerous property is DATA LOSS IN THE GROUP, not deployment failure:
a bug silently evicts other people's volumes and nothing fails. It also has
to honour a per-host / per-AZ `hammerspace_volume_group` — `run_once`
evaluates variables in ONE host's context, so a single scalar read sent every
volume in the play to whichever group that host named, and skipped the step
entirely when that host named none.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Group holds 2 volumes; run creates 2, one already a member | Append-only — both pre-existing kept, 1 added, no duplicates |
| 2 | Re-run with everything already a member | Zero additions, no PUT issued |
| 3 | **Audit** orchestrator + per-group body | `run_once` union write over `ansible_play_hosts`; the per-group body never re-reads the play var; the join files never POST; 404 fatal unless `hammerspace_create_volume_groups` (gate pinned in `test_volume_group_create.yml`); membership re-read; opt-in gate on the include considers every host |
| 4 | **Audit** `add_volume.yml` | Records the exact volume name it used — never re-derived for grouping |
| 5 | 4 hosts, 2 AZs, a group each | One plan entry per group, volumes unioned per group, nothing crosses over; the old single-scalar read is shown sending all 4 to one group |
| 6 | One group / empty per-host value / no group named | Unchanged single union write; empty value falls back to the play value; nothing produces a group named `''` |
| 7 | **Audit** the split | Per-group include with `loop_var: _vgj`; plan and per-group facts reset (`set_fact` persists across loop iterations and across `hammerspace_serial` batches); share-objective ambiguity guard with an escape |

Mutation-tested: dropping the plan reset, re-reading the play var in the
per-group body, dropping the per-group fact reset, accepting an empty group
name, softening the ambiguity guard, or narrowing the include gate back to the
current host each fails the relevant test.

## `test_volume_group_create.yml`

`hammerspace_create_volume_groups` used to act only on the
`hammerspace_volume_groups` list, so `-e hammerspace_volume_group=<new>
-e hammerspace_create_volume_groups=true` created nothing and the join failed
with HTTP 404. A missing `hammerspace_volume_group` is now created, with
this run's volumes, when the flag is on. The old create file also never read
its POST status and printed "created successfully" regardless.

TESTs 1–5 run the real task files against `mock_anvil_volume_groups.py`, a
throwaway local HTTP server. The mock proves the gating; it does not prove a
real Anvil accepts the POST body.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | POST 202 + Location, task COMPLETED, group listed | `_vgc_created`, status 200, uuid, members = this run's volumes in the shape the join reads |
| 2 | POST 400 whose body echoes `authorization` as a JSON object | Hard failure naming HTTP 400; `Basic <redacted>`; neither the password nor its base64 in the message |
| 3 | POST 202, group never listed | Hard failure "still not listed" — not "created" |
| 4 | Join, flag `"true"` (string, as `-e` passes it), group missing | Group created, recorded for the place-on wait, join adds nothing |
| 5 | Join, flag `"false"` (string), group missing | Still the hard "does not exist" failure — `"false"` is truthy without `\| bool` |
| 6 | **Audit** `volume_group_create.yml` | 4 curl tasks, all `no_log`; no fixed `/tmp` header path; `-d @file`; POST and lookup status gates; listing check; block gate decided once; temp files removed before the rejection failure |
| 7 | **Audit** the join gate | Create only on 404 AND flag `\| bool`; no POST in the join file; check mode suppresses both failures; `main.yml` reads the flag with `\| bool` |
| 8 | Place-on wait decision, verbatim | Waits only for a group created in this run with no explicit objective; source holds exactly that expression |
| 9 | Exhausted `until` + `failed_when: false` + `no_log` | Play continues — the waits rely on this so their failures are reported uncensored (checked on ansible-core 2.14 and 2.20) |

Mutation-tested (9): gating the block on the fact it changes, dropping the
POST status gate, the POST `no_log`, the `\| bool` in the join or `main.yml`,
a check-mode guard, the listing verification, the body redaction, or forcing
the place-on wait each fails the relevant test.

## `test_api_body_redaction.yml`

The Hammerspace API echoes the request headers inside some error bodies
(`ENTITY_NOT_FOUND`, `errorCode 4002`). The curl tasks are `no_log`, so the
failure messages print the response body instead — and that body held the
credential, which `scripts/run.sh` then wrote to `~/logs`. The old comment
claimed the response "cannot contain the credential".

**The first fix did not work.** It anchored on `authorization:` —

```
regex_replace('(?i)authorization:\s*Basic\s+[A-Za-z0-9+/=]+', ...)
```

— and this test fed it a fixture where `headers` was a *string* of header
lines. It passed. A real Anvil emits `headers` as a JSON **object**:

```
"headers":{"host":"...","authorization":"Basic <b64>","content-type":...}
```

where `authorization` is followed by `":"`, not `:`. The pattern never
matched, and the credential printed in full — it reached a screen-share
before anyone noticed. The redaction is now **unanchored**: it matches
`Basic <base64>` wherever it appears, whatever precedes it.

> A redaction test must use a body captured from the real API. This one was
> written from the same assumption as the redaction it was checking, so the
> two agreed with each other and both were wrong.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Dummy credential in **both** body shapes — JSON-object headers (from the field report) and header-line string — through the shared pattern | Token gone, `Basic <redacted>` present, error code and the volume name intact |
| 2 | The **old anchored** pattern against the JSON-object shape | Still leaks — pinned, so re-anchoring cannot pass silently |
| 3 | **Audit** every line printing `(empty response body)` in `roles/hammerspace_integration` (recursing into `cluster/` and `s3/`) and `roles/di` | Each carries the redaction; count guard (>= 4) so it cannot pass vacuously |
| 4 | No comment says the response cannot contain the credential | Zero matches |
| 5 | The anchored pattern appears nowhere in the role | Zero matches |

Mutation-tested: re-anchoring one site, un-redacting one site, and restoring
the false claim each fail it.

## `test_volume_add_status.yml`

A field run failed at the volume group PUT with

```
[{"errorCode":4002,"args":["storage volume","10.1.16.4::/nvme/tier0"],
  "message":"ENTITY_NOT_FOUND", ...}]
```

The PUT was correct. It was the first task in the whole run that actually
*checked* whether the volume existed — and it runs `run_once`, so it failed on
whichever host Ansible picked rather than the host that built the name.

`add_volume.yml` registered the POST result but read `.status` at exactly one
place: to recognise the `400`/"already exists" case. Every other rejection
(401, 403, 404, 409, 5xx, or curl never running at all — HTTP 0) fell through.
The completion poll that should have caught it is gated on a `Location` header
a rejected POST never returns, so the poll was **skipped rather than failed**.
Both gates passed by being absent, and the task printed "added successfully".

> The same shape as the `objective-set` silent success: an async API call has
> **two** failure points — the request status and the task status — and "no
> Location, so skip the poll" is how a rejected request becomes a silent
> success.

A second path reached the same place: the existence pre-check set
`volume_already_exists` from HTTP status alone, so a `200` with a null body
skipped the add entirely.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | POST status gate over 403 / 404 / 409 / 500 / HTTP 0 / 201 / 202 / already-exists | Fires on every rejection, silent on 2xx and on the reclassified already-exists |
| 2 | 2xx with and without a `Location` | A 2xx with no Location **fails**; it must never skip the poll |
| 3 | `volume_already_exists` from (200 + uuid), (200 + empty), (404) | Only the first is "exists" — a 200 alone is not proof |
| 4 | **Audit** `add_volume.yml` for all four gates | Present; `>= 3` `fail` tasks as a count guard |
| 5 | The verification GET | `check_mode: false`, `no_log: true`, skipped under `--check` |
| 6 | Body prints in the file | Redacted, and the anchored pattern absent |
| 7 | Gate ordering | The status gate precedes the poll, so a rejected add does not wait out the full retry budget first |
| 8 | `urlencode` on the field-report volume name, plus an audit of the file | Produces `10.1.16.4%3A%3A/nvme/tier0` (slashes NOT encoded); no `storage-volumes/{{...}}` URL remains; both lookups list `/storage-volumes` |
| 9 | List-and-select over: volume present, absent, an unnamed entry, an error object, an empty list | Finds the uuid only on an exact name; the per-node listing keeps `10.1.16.40::...` out of `10.1.16.4`'s |

**Follow-up (rule 5).** Once the Anvil could reach the node, the add
succeeded and the post-add verification 404'd on a volume the Anvil UI
showed as present. A volume name always contains `/`, and Jinja's
`urlencode` encodes `:` but **not** `/`, so `GET /storage-volumes/<name>`
became `.../storage-volumes/10.1.16.4%3A%3A/nvme/tier0` and the API read
`/nvme/tier0` as extra path segments. That lookup had **always** 404'd —
the existence pre-check never once found an existing volume, and re-runs
survived only on the 400 "already exists" reclassification. Both lookups now
list `/storage-volumes` and select by name, which also avoids depending on
whether a given Anvil accepts `%2F` in a path.

Mutation-tested (9): dropping the status gate, dropping `no_log`, dropping
`check_mode: false`, re-anchoring the redaction, removing the post-add
verification, reverting either lookup to a per-name URL, dropping the `::`
node anchor, and loosening the exact-name match each fail it.

Comment lines are stripped before the source audits run — this file explains
the bug in prose, and an audit matching the explanation would pass on broken
code.

## `test_di_decommission_volume.yml`

Three defects in `roles/di/tasks/decommission_volume.yml` and the node
removal in `decommission.yml`, found by applying the `add_volume.yml`
per-name URL finding to the rest of the repo.

1. **Addressing.** Both requests used
   `/storage-volumes/{{ _volume.name | urlencode }}`. A volume name contains
   `/`, which `urlencode` does not encode, so every call 404'd. The volume is
   now addressed by `uoid.uuid`, falling back to the name with `/` encoded as
   `%2F` (what the Python scripts do with `quote(name, safe='')`).
2. **Silent failure.** Neither request's status was read. A 404 has no
   `Location`, so both polls were skipped and the file printed "removed
   successfully" for a volume nothing touched — then `decommission.yml`
   deleted the node, without the evacuation ever having run.
3. **Dry run was not dry.** `roles/di/tasks/_api_request.yml` runs curl with
   `check_mode: false` (its GETs must run under `--check`), and nothing gated
   the writes. `--check` — which both the deployment guide and the DI guide
   recommend as a preview — **deleted the DI node for real**. It did not also
   decommission the volumes only because defect 1 made those calls 404, so
   fixing defect 1 alone would have made `--check` more destructive.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Addressing over: uuid present, no `uoid`, empty uuid (AZ-prefixed name) | uuid when present; otherwise the name with `:` and `/` both encoded; never a raw `/` |
| 2 | **Audit** the request URLs | No `storage-volumes/{{ _volume...` URL; exactly two request URLs via `_dv_id` |
| 3 | **Audit** the gates | Status gates on both requests, a fail on a decommission with no task `Location` (evacuation cannot be waited on), `!= 'COMPLETED'` on both polls, post-removal verification that fails closed, and no unconditional "removed successfully" |
| 4 | Verification predicate over: gone, still listed, list call failed, 200 with an error object, empty list | Blocks the node delete whenever it cannot positively confirm the volume is gone |
| 5 | **Audit** every POST/PUT/PATCH/DELETE through the DI request helper | Exactly 3, each after a `when: not ansible_check_mode` gate, each with a check-mode "would …" message |

TEST 5's count guard is deliberate: a new write anywhere in `roles/di` fails
it until it is gated and the expected count is updated.

Mutation-tested (6): reverting the URL, dropping the decommission status gate,
making the verification fail open, dropping the no-`Location` gate, and
removing the check-mode gate on the volume writes or on the node DELETE each
fail it. The first run caught only 4 of 6 — TEST 4 reproduces the predicate,
so it cannot see the file drift, and TEST 3 matched task names that survive a
weakened `when:`. The conditions themselves are now pinned.

## `test_api_host_required.yml`

`hammerspace_api_host` used to ship as one site's Anvil IP, so a site that
forgot to override it silently talked to that cluster. It now ships empty and
every play that talks to the Anvil includes
`roles/hammerspace_integration/tasks/require_api_host.yml` first (before the
Key Vault lookup), tagged `always` so `--tags` filtering cannot skip it.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Raw `vars/main.yml` | `hammerspace_api_host: ""`; no IP literal default |
| 2 | Gate run with `""`, `"   "`, `"10.0.0.10"` | Blank values fail with a message naming the variable; a real IP passes |
| 3 | **Audit** eight plays | Each includes the gate, tagged `always`, ahead of the (last) `keyvault_password` include |

Mutation-tested: restoring an IP default, removing the gate from `di.yml`,
dropping the `always` tag, and weakening the length check each fail it.
`_api_host_probe.yml` is its helper (no `test_` prefix, so the runner skips it).

## `test_nfs_exports_hardening.yml`

2026-09-18 NFS field report follow-up. Pins the DI-export persistence bug
(`di_exports.yml` ended the play unless `deploy_di` was set, while the storage
play regenerates `/etc/exports` every run), the unanchored `lineinfile` regexp
(`10\.0\.12\.24` matched inside `10.0.12.242` and replaced the whole
template line), the shipped example IP in `mover_nodes`, the unread
`exportfs -ra` stderr, dead subnets after `0.0.0.0/0`, and the silent
multi-NIC registration.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Template exports line with `10.0.12.242`; probe `10.0.12.24` / `10.0.12.242` / `10.0.12.99` | Old `^path\s.*IP` matches the prefix (the defect); new `^path\s+IP\(` matches neither the template line nor a prefix, only a dedicated line; skip guard is exact |
| 2 | `mount_points` undefined | Export paths recovered from `/etc/exports`, comments skipped, deduplicated |
| 3 | `client_subnets` orderings | Subnet after `0.0.0.0/0` flagged; single host after it, or subnet before it, not flagged |
| 4 | **Audit** | `di_exports.yml` gated on `groups['di_nodes']` never `deploy_di`, tagged `nfs-exports`, anchored regexp, skip guard, `hammerspace_nodes` excluded, `regex_findall` fallback; `mover_nodes: []`; exportfs registered + `failed_when: false` + strict gate on stderr; shadow warning; multi-NIC warning; `nfs_exportfs_strict` documented |

Mutation-tested: old regexp, `deploy_di` gate, example IP, dropped exportfs
gate — each fails TEST 4.

## `test_vm_writeback.yml`

Covers the opt-in NFS-client writeback caps (`perf_vm_writeback`). The tuning
recipe that circulates for `azcopy` ingestion —

```
vm.dirty_background_bytes = 268435456
vm.dirty_bytes            = 1073741824
vm.dirty_background_ratio = 0
vm.dirty_ratio            = 0
```

— silently discards its own first line. `vm.dirty_*_bytes` and
`vm.dirty_*_ratio` are one limit in two units, and each kernel handler zeroes
the other on write, so line 3 resets `dirty_background_bytes` to 0 and the
background flush threshold ends up at 0 pages rather than 256 MB. Line 4 is a
no-op only because line 2 already set the ratio to 0 and that handler acts on a
change. The role therefore accepts one form per limit and refuses a mix.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Simulate `dirty_background_bytes_handler` / `dirty_bytes_handler` / `dirty_background_ratio_handler` / `dirty_ratio_handler` over the four-line recipe | Final `dirty_bytes` = 1 GB survives, `dirty_background_bytes` = 0 — the 256 MB target is lost; derived thresholds 262144 / 0 pages |
| 2 | Guard evaluated against 8 configs | Refuses the four-line recipe, both mixed forms, `*_ratio: 0`, a sub-two-page `*_bytes`, and `background >= dirty`; accepts `*_bytes`-only and `*_ratio`-only |
| 3 | Shipped defaults + `vars/main.yml` | `*_bytes`-only, `background < dirty`, both above two pages, `perf_vm_writeback_enabled: false` in both, the two dicts identical, filename a bare `*.conf` |
| 4 | **Audit** `vm_writeback.yml` (comment lines stripped first) | Exactly 2 read-only shell probes, each `check_mode: false`; verification gated `when: not ansible_check_mode`; `sysctl_file` pinned to `/etc/sysctl.d/{{ perf_vm_writeback_sysctl_filename }}` and never `/etc/sysctl.conf`; mutual-exclusion, two-page-minimum, ratio-zero and `background < dirty` conditions present; `LC_ALL=C LANG=C`; `executable: /bin/bash` |
| 5 | **Audit** wiring + docs | `include_tasks: vm_writeback.yml` gated on `perf_vm_writeback_enabled | default(false)`, tagged `[perf, writeback]`, listed in the role summary; `VARIABLE_REFERENCE.md` documents the variables; `docs/TROUBLESHOOTING.md` names `dirty_background_bytes` and `NFS_Unstable` |

| 6 | Post-apply verification against 5 synthetic read-back maps | Fails on an empty map, on empty values, on a partial read-back, and on a `*_bytes` that came back `0`; passes only when every requested key came back non-empty and non-zero. A `*_ratio` of `0` beside a set `*_bytes` is expected, not a failure |

Note on TEST 4: the probes are read-only `shell` tasks whose output feeds the
safety assert, so both must carry `check_mode: false` — `command`/`shell` are
skipped under `--check` and a check-mode skip registers `stdout: ''`, which
would let the guard pass on an empty page size. The verification assert is
gated the other way (`when: not ansible_check_mode`): nothing was applied, so
the read-back would be the host's pre-existing state.

TEST 6 exists because of a bug found by smoke-running the role on a host with
no `/proc/sys/vm`: the read-back exited 0 with **empty** values and the
verification reported success for limits it had never read. `printf '%s' "$(cat
missing)"` does not trip `set -e` — `printf` itself succeeds. The read-back now
skips an unreadable knob, and the verification is guarded on the value's
**length**, not on the key being defined.

Comment lines are stripped before the audit because the task file's own header
explains the `/etc/sysctl.conf` trap in prose, and a negative search over the
raw text would always report a regression.

Mutation-tested: dropping one `check_mode: false`, pointing `sysctl_file` at
`/etc/sysctl.conf`, deleting the ratio-zero or two-page rule, dropping one
probe's `LC_ALL=C` or `executable: /bin/bash`, removing the `when: not
ansible_check_mode` gate, removing the `[ -r "$f" ]` read-back test, the
`_vmwb_unread` gate or its empty-value filter, letting the read-back fail the
play, removing the `writeback` tag, defaulting the cap on, and shipping
`vm.dirty_ratio: 0` — each fails the relevant test.

Two YAML traps hit while writing this file, both already known here: an
`assert.that` entry containing `: ` inside a quoted regex parses as a
**mapping** (quote the whole expression), and a **block scalar does not process
string escapes**, so `join('\n')` under `>-` joins with a literal backslash-n
and every multi-line needle silently stops matching — use a double-quoted
scalar for that, or `regex_findall(..., multiline=True)`.

## When to run

- Before merging any change to `roles/nvme_discovery/tasks/detect_boot_device.yml`
  (protected-disk / md-member / LVM-PV split, hard-fail behavior)
- Before merging any change to the `rejectattr` filters in
  `roles/nvme_discovery/tasks/main.yml`
- Before merging any change to the safety gate in
  `roles/nvme_discovery/tasks/build_raid_arrays.yml`
- After any change to `roles/precheck/tasks/validate_drives.yml`
- After any change to `roles/raid_setup/tasks/main.yml` (idempotency
  detection, `_device_remap`, or the per-element list rebuild pattern)
- After any change to `scripts/run.sh` (locale-fallback chain)
- After any change to `roles/perf_tuning/tasks/vm_writeback.yml` or to
  `perf_vm_writeback` in `vars/main.yml` (the `*_bytes` / `*_ratio`
  exclusivity guard — a mixed config's meaning depends on line order)
- After any change to `roles/nfs_setup/tasks/main.yml` that references a
  variable registered inside a `when`-guarded block (skipped-result dicts
  lack `rc`/`stdout`/etc. — always guard with `is defined`)
- After any change to `_cloud_az_detected` in `vars/main.yml`, to
  `roles/hammerspace_integration/tasks/az_map.yml` /
  `azure_imds_az.yml` / `add_volume.yml`, or to the AZ columns in
  `plays/storage.yml`'s instance report
- After adding a cloud provider, an inventory plugin, or a new entry to the
  `SYSTEM_MOUNTS` allow-list in
  `roles/nvme_discovery/tasks/detect_boot_device.yml`
- After adding any playbook that talks to the Hammerspace API — it must load
  `vars/vault.yml` via `{{ repo_root }}` or the password is silently undefined
- After any change to `roles/hammerspace_integration/tasks/add_volume.yml`,
  especially to the existence pre-check, the POST status gate, the completion
  poll, or the post-add verification — the name this file records in
  `_hs_run_volume_names` is what the volume group join PUTs, and a name the
  API cannot resolve fails far away on another host
- After any change to `roles/hammerspace_integration/tasks/volume_group_create.yml`,
  the create gate in `volume_group_join_one.yml`, or the objective wait in
  `tier0_place_on_group.yml` (`test_volume_group_create.yml` — needs
  `python3` for the mock Anvil)
- After any change to `roles/di/tasks/decommission_volume.yml`,
  `roles/di/tasks/decommission.yml`, or any new POST/PUT/DELETE through
  `roles/di/tasks/_api_request.yml` — that helper ignores `--check`, so every
  write must be gated by its caller
- After adding any task that prints an API response body in a failure message
  — the Anvil echoes the `Authorization` header into some error bodies, and
  the redaction must stay **unanchored** (`Basic <base64>`, not
  `authorization: Basic ...`)

Wire into pre-merge CI as `./tests/run_all.sh` — it runs every check in one
command and exits non-zero on any failure.

## Notes

- TEST 6 **of `test_run_sh_locale.sh`** needs the `ko_KR.UTF-8` locale
  generated on the host. If
  it's missing, the test will skip rather than fail spuriously.
  Generate with `localectl set-locale ko_KR.UTF-8` (RHEL/Rocky) or
  `locale-gen ko_KR.UTF-8` (Debian/Ubuntu). The test still validates
  locale-resilience because the detection forces `LC_ALL=C`
  internally regardless of the calling environment.
- The tests use `block` / `rescue` to convert expected failures into
  test passes. Real failures bubble up via the final `_test_failures`
  list.
