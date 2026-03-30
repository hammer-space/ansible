# Architecture Review — Hammerspace Tier 0 Ansible Automation

**Review Date:** March 26, 2026
**Codebase:** ~6,080 lines YAML (33 task files, 7 roles) + ~2,550 lines Python (5 scripts) + ~1,260 lines playbooks = **~16,600 lines total**
**Verdict:** Production-ready for Tier 0 / LSS deployments across OCI, AWS, GCP, Azure, and on-prem

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Structure](#2-project-structure)
3. [Execution Flow](#3-execution-flow)
4. [Variable Architecture](#4-variable-architecture)
5. [API Integration](#5-api-integration)
6. [Idempotency](#6-idempotency)
7. [Error Handling](#7-error-handling)
8. [Check Mode Compatibility](#8-check-mode-compatibility)
9. [Code Quality](#9-code-quality)
10. [Security](#10-security)
11. [Scalability](#11-scalability)
12. [Python Utility Scripts](#12-python-utility-scripts)
13. [Testing](#13-testing)
14. [Documentation](#14-documentation)
15. [Remaining Recommendations](#15-remaining-recommendations)

---

## 1. Executive Summary

### Strengths

- 7-role architecture with clear separation of concerns
- Dynamic storage discovery (NVMe, HDD, SSD, mixed) with NUMA-aware RAID grouping
- CPU vendor auto-detection (15+ profiles: AMD EPYC, Intel Xeon, ARM Neoverse/Ampere/Graviton/Grace) with tuned RAID/IO settings
- Multi-cloud inventory (OCI, AWS, GCP, Azure) + static darksite
- Hammerspace REST API integration: node, volume, share, volume group, S3, AZ mapping, cluster config (DNS, AD, site, location, Prometheus)
- Dual API transport: `shell+curl` (Python 3.13+) with `ansible.builtin.uri` fallback (Python < 3.13), auto-detected via `hammerspace_api_method`
- Reusable API wrappers: `_api_request.yml` and `_api_poll_task.yml` abstract transport choice
- Credentials in Ansible Vault (`vars/vault.yml`)
- Per-array RAID failure tracking with partial-failure recovery
- FAILED/CANCELLED detection on all async API polling loops
- Task queue throttling to protect the Hammerspace cluster
- Mount protection via systemd guard services and remount watchdog
- Standardized task naming conventions (537 named tasks, 100% coverage)
- yamllint + ansible-lint configured
- 6 documentation files (~5,500 lines) including variable reference

### Risk Summary

| Category | Risk Level | Notes |
|----------|------------|-------|
| No automated test suite | **HIGH** | No Molecule, pytest, or CI/CD pipeline |
| Credentials in `ps aux` (curl `-u`) | **MEDIUM** | Visible during execution; vault protects at rest |
| Variable precedence complexity | **MEDIUM** | 4 levels; `set_fact` overrides can confuse maintainers |
| `preflight_check.yml` uses `uri` module | **LOW** | Runs on localhost only; not affected by target Python version |
| Scalability at 500+ nodes | **MEDIUM** | Throttling exists but untested at extreme scale |

---

## 2. Project Structure

```
Ansible-Tier0/
├── site.yml                          # Main playbook (OCI / cloud) — 192 lines
├── site-onprem-nvme.yml              # On-prem NVMe (no firewall) — 169 lines
├── verify_nfs.yml                    # NFS verification — 37 lines
├── collect_gpu_fabric.yml            # GPU fabric topology — 119 lines
├── generate_instance_report.yml      # Metadata reporting — 63 lines
├── preflight_check.yml               # Inventory vs Hammerspace diff — 302 lines
│
├── vars/main.yml                     # Master configuration — 758 lines
├── vars/vault.yml                    # Encrypted credentials — 32 lines
├── ansible.cfg                       # SSH pipelining, become, inventory
├── .yamllint / .ansible-lint         # Lint configuration
├── requirements.yml / requirements.txt
│
├── inventory.yml                     # Static (darksite)
├── inventory.oci.yml                 # OCI dynamic inventory
├── inventory.aws.yml                 # AWS dynamic inventory
├── inventory.gcp.yml                 # GCP dynamic inventory
├── inventory.az.yml                  # Azure dynamic inventory
│
├── roles/
│   ├── nvme_discovery/               # 1,239 lines — 5 task files
│   │   ├── tasks/main.yml                   511 lines — orchestration
│   │   ├── tasks/build_raid_arrays.yml      152 lines — array planning
│   │   ├── tasks/build_numa_raids.yml       122 lines — per-NUMA splits
│   │   ├── tasks/detect_boot_device.yml      45 lines — boot exclusion
│   │   ├── tasks/cpu_optimization.yml       372 lines — 15+ vendor profiles
│   │   └── defaults/main.yml                 37 lines
│   │
│   ├── precheck/                     # 1,042 lines — 8 task files
│   │   ├── tasks/main.yml                   158 lines — orchestrator
│   │   ├── tasks/validate_drives.yml        177 lines — count + mount status
│   │   ├── tasks/validate_numa.yml           82 lines — NUMA balance
│   │   ├── tasks/validate_sectors.yml       174 lines — 4K sector + NVMe format
│   │   ├── tasks/validate_network.yml        65 lines — MTU / jumbo frames
│   │   ├── tasks/validate_packages.yml       56 lines — install + verify
│   │   ├── tasks/validate_existing.yml       30 lines — mdadm/NFS detection
│   │   ├── tasks/iperf_test.yml             233 lines — bandwidth testing
│   │   └── defaults/main.yml                 67 lines
│   │
│   ├── raid_setup/                   # 268 lines — 1 task file
│   │   ├── tasks/main.yml                   233 lines — per-array error tracking
│   │   ├── handlers/main.yml                 16 lines
│   │   └── vars/{default,redhat,debian}.yml
│   │
│   ├── filesystem_setup/             # 380 lines — 2 task files
│   │   ├── tasks/main.yml                   149 lines — mkfs + mount
│   │   ├── tasks/mount_protection.yml       194 lines — systemd guards
│   │   └── templates/                         5 Jinja2 templates (guard, timer, check script)
│   │
│   ├── nfs_setup/                    # 473 lines — 1 task file
│   │   ├── tasks/main.yml                   363 lines — exports + validation
│   │   ├── templates/nfs.conf.j2, exports.j2
│   │   ├── files/set-readahead.{sh,service}
│   │   └── handlers/main.yml                 77 lines
│   │
│   ├── firewall_setup/               # 299 lines — 4 task files
│   │   ├── tasks/main.yml                    auto-detects firewalld/ufw/iptables
│   │   ├── tasks/firewalld.yml, ufw.yml, iptables.yml
│   │   └── vars/{redhat,debian,default}.yml
│   │
│   └── hammerspace_integration/      # 2,379 lines — 19 task files
│       ├── tasks/main.yml                   339 lines — node registration + orchestration
│       ├── tasks/_api_request.yml            91 lines — curl/uri transport wrapper
│       ├── tasks/_api_poll_task.yml          82 lines — async task polling wrapper
│       ├── tasks/add_volume.yml             186 lines — volume lifecycle
│       ├── tasks/task_queue_wait.yml         89 lines — rate limiting
│       ├── tasks/create_share.yml           162 lines — share CRUD
│       ├── tasks/share_apply_objective.yml   75 lines — QoS objectives
│       ├── tasks/volume_group_create.yml    114 lines — group management
│       ├── tasks/volume_group_add_volume.yml 86 lines — group membership
│       ├── tasks/az_map.yml                  79 lines — AZ label assignment
│       ├── tasks/cluster/
│       │   ├── dns_update.yml                68 lines
│       │   ├── ad_join.yml                  114 lines
│       │   ├── change_site_name.yml          97 lines
│       │   ├── set_location.yml             105 lines
│       │   └── prometheus_enable.yml        107 lines
│       ├── tasks/s3/
│       │   ├── add_s3_node.yml              108 lines
│       │   ├── add_object_storage_volume.yml 109 lines
│       │   ├── create_s3_server.yml          95 lines
│       │   └── create_s3_user.yml            77 lines
│       └── defaults/main.yml                196 lines
│
├── scripts/
│   ├── assign_az_to_volumes.py       # 754 lines — GPU fabric AZ mapping
│   ├── set_availability_drop.py      # 608 lines — maintenance mode
│   ├── cleanup_instance_nodes.py     # 474 lines — node/volume removal
│   ├── rename_oci_instances_az.py    # 393 lines — OCI instance rename
│   ├── add_volumes_to_group.py       # 324 lines — volume group batch ops
│   └── deploy_new_instances.sh       # Instance provisioning wrapper
│
├── VARIABLE_REFERENCE.md             # 297 lines — all variables + defaults
├── DEPLOYMENT_GUIDE.md               # 1,045 lines — step-by-step
├── DEPLOYMENT_GUIDE_DARKSITE_HDD.md  # 990 lines — air-gapped HDD
├── RACK_OPERATIONS_RUNBOOK.md        # 765 lines — RMA, expansion
├── README.md                         # 1,845 lines — overview
└── ARCHITECTURE_REVIEW.md            # This document
```

---

## 3. Execution Flow

### Playbook Execution Order

```
pre_tasks
│  ├── Flush iptables (site.yml only)
│  ├── Assert required variables
│  └── Check Hammerspace node registration (curl) → node_already_in_hammerspace
│
roles (sequential)
│  ├── nvme_discovery
│  │   ├─► Outputs: raid_arrays, mount_points, nfs_exports
│  │   ├─► CPU detection → auto-tuned RAID chunk/queue/scheduler
│  │   └─► Post-discovery assertions validate non-empty results
│  │
│  ├── precheck
│  │   └─► Read-only validation (drives, NUMA, MTU, packages, iperf)
│  │
│  ├── raid_setup
│  │   ├─► Consumes: raid_arrays
│  │   └─► Per-array error tracking — partial failure continues
│  │
│  ├── filesystem_setup
│  │   ├─► Consumes: mount_points
│  │   ├─► block/rescue with diagnostics on failure
│  │   └─► Mount protection: systemd guard services + remount watchdog
│  │
│  ├── nfs_setup
│  │   ├─► Consumes: nfs_exports, node_already_in_hammerspace
│  │   ├─► Throttled restart (configurable)
│  │   └─► Post-setup showmount validation
│  │
│  ├── firewall_setup (conditional, auto-detects backend)
│  │
│  └── hammerspace_integration (conditional)
│      ├─► Detects API method (curl vs uri) based on Python version
│      ├─► Cluster config: DNS, AD, site name, location, Prometheus
│      ├─► S3: nodes, object volumes, server, users
│      ├─► Node registration with async task polling
│      ├─► Volume groups (optional, before volumes)
│      ├─► Volume add loop (mount_points) with task queue throttling
│      ├─► Volume group membership (after volumes)
│      ├─► Shares + share objectives
│      └─► FAILED/CANCELLED detection on all async waits
│
post_tasks
   ├── systemctl daemon-reload
   └── Generate instance_report.csv
```

### Critical Data Flow

```
nvme_discovery ──► raid_arrays      ──► raid_setup
               ──► mount_points     ──► filesystem_setup, hammerspace_integration
               ──► nfs_exports      ──► nfs_setup
```

Post-discovery assertions validate `raid_arrays | length > 0`, `mount_points | length > 0`, and `nfs_exports | length > 0` before proceeding to downstream roles.

---

## 4. Variable Architecture

### Precedence Map (highest first)

| Level | Source | Example |
|-------|--------|---------|
| 1. `set_fact` | `site-onprem-nvme.yml` pre_tasks | `storage_type: "nvme"` |
| 2. `vars_files` | `vars/main.yml` (758 lines), `vars/vault.yml` | Master config + secrets |
| 3. Play `vars` | `site-onprem-nvme.yml` vars block | On-prem overrides |
| 4. Role `defaults` | `roles/*/defaults/main.yml` (371 lines total) | Fallback defaults |

### Configuration Sections in `vars/main.yml`

| Section | Key Variables |
|---------|---------------|
| RAID Configuration | `use_raid`, `raid_level`, `raid_grouping_strategy`, `raid_max_drives_per_array`, `raid_power_of_2_drives`, `raid_leftover_drives` |
| Hardware RAID | `hw_raid_devices` (list of /dev paths), `xfs_agcount` |
| Storage Type | `storage_type` (nvme/hdd/ssd/scsi/all), `use_dynamic_discovery` |
| NVMe Exclusion | 7 methods: device, path, serial, model, NUMA node, PCIe address, PCIe prefix |
| SCSI Exclusion | 4 methods: device, path, serial, model |
| CPU Optimization | `cpu_optimized_raid`, `cpu_vendor_profile`, `raid_chunk_size`, `nvme_queue_depth` |
| Hammerspace API | `hammerspace_api_host`, credentials (vault ref), port, `hammerspace_api_method` (auto/curl/uri) |
| Volume Config | Thresholds, availability, durability, suspected seconds, skip perf test |
| Volume Naming / AZ | `hammerspace_volume_az_prefix_mode` (auto from OCI fault domain, static, disabled) |
| Task Queue | `hammerspace_max_queued_tasks`, `hammerspace_min_queued_tasks`, retries, delay |
| API Timeouts | Per-operation: node add, volume add, RAID sync |
| AZ Mapping | `hammerspace_enable_az_mapping`, `hammerspace_apply_az_labels`, default AZ |
| Volume Groups | `hammerspace_create_volume_groups`, group definitions, membership |
| Shares | `hammerspace_create_shares`, share definitions, export options |
| Share Objectives | `hammerspace_apply_share_objectives`, objective definitions |
| S3 / Object Storage | S3 nodes, object volumes, S3 server, S3 users |
| Cluster Config | DNS, Active Directory, site name, location, Prometheus |
| NFS | Threads (128), versions (v3+v4.2), RDMA, Hammerspace/mover/client IPs, export opts |
| Firewall | `configure_firewall`, NFS ports, firewalld services |
| Mount Protection | Guard services, remount watchdog, interval, automount timeout |
| Safety | `force_raid_recreate`, `force_fs_recreate`, `skip_confirmation` |
| Pre-Setup Validation | Drive count, NUMA balance, 4K sectors, MTU, iperf bandwidth |

Full variable catalog: see [VARIABLE_REFERENCE.md](VARIABLE_REFERENCE.md)

### Known Precedence Conflicts

| Variable | Defined In | Risk |
|----------|-----------|------|
| `storage_type` | `vars/main.yml`, `nvme_discovery/defaults`, `site-onprem-nvme.yml` (set_fact) | Medium — set_fact wins |
| `use_raid` | `vars/main.yml`, `site-onprem-nvme.yml` (set_fact + vars) | Medium — double-defined |
| `raid_min_drives_per_array` | `vars/main.yml` (=2) vs `nvme_discovery/defaults` (=1) | Medium — different defaults |

---

## 5. API Integration

### Dual Transport Architecture

Python 3.13 removed `cert_file`/`key_file` from `HTTPSConnection.__init__()`, breaking `ansible.builtin.uri`. The codebase supports both transports:

| Transport | When Used | Mechanism |
|-----------|-----------|-----------|
| `shell` + `curl` | Python >= 3.13 (default on modern systems) | `-o /dev/stdout -w "\n%{http_code}"` for body+status, `-D` for headers |
| `ansible.builtin.uri` | Python < 3.13 (legacy systems) | Native Ansible module with JSON parsing |
| Auto-detect | `hammerspace_api_method: auto` (default) | Checks `ansible_python_version` at role start |

**Detection logic** (in `hammerspace_integration/tasks/main.yml`):
```yaml
_hs_use_curl: >-
  {{ true if method == 'curl'
     else false if method == 'uri'
     else (ansible_python_version is version('3.13', '>=')) }}
```

### Reusable Wrappers

| File | Purpose | Inputs | Outputs |
|------|---------|--------|---------|
| `_api_request.yml` | HTTP GET/POST/PUT via either transport | `_api_request_url`, `_api_request_method`, `_api_request_body` | `_api_response.status`, `_api_response.json`, `_api_response.location` |
| `_api_poll_task.yml` | Async task polling via either transport | `_api_poll_url`, `_api_poll_retries`, `_api_poll_delay` | `_api_poll_result.task_status` (COMPLETED/FAILED/CANCELLED) |

### curl Pattern (used in all 17 existing task files)

**Read (GET):**
```yaml
curl -s -o /dev/stdout -w "\n%{http_code}" --max-time 60
  -k -u "user:pass" "{{ hs_api_url }}/endpoint"
# Parse: status = stdout_lines[-1], json = stdout_lines[:-1] | join | from_json
```

**Write (POST/PUT):**
```yaml
curl -s -o /dev/stdout -w "\n%{http_code}" --max-time 180
  -k -u "user:pass" -X POST -H "Content-Type: application/json"
  -D /tmp/hs_headers.txt -d '{{ payload | to_json }}' "{{ url }}"
# Location header: grep -i '^Location:' /tmp/hs_headers.txt
```

**Async Poll:**
```yaml
curl -s --max-time 60 -k -u "user:pass" "{{ location_url }}"
# until: (stdout | from_json).status in ["COMPLETED", "FAILED", "CANCELLED"]
```

### Task Queue Throttling

Before each volume/share/group operation, `task_queue_wait.yml` checks:
1. QUEUED task count — if > `max_queued_tasks` (10), poll until < `min_queued_tasks` (5)
2. EXECUTING task count — optional monitoring via `hammerspace_monitor_executing_tasks`

Max wait: 100 retries x 10s = ~16 minutes per throttle event.

---

## 6. Idempotency

| Role | Idempotent | Mechanism |
|------|-----------|-----------|
| nvme_discovery | Yes | Read-only shell commands, `changed_when: false` |
| precheck | Yes | Read-only validation |
| raid_setup | Yes | Checks existing arrays before `mdadm --create` |
| filesystem_setup | Yes | Checks `blkid` before `mkfs.xfs` |
| nfs_setup | Yes | Template comparison, service state checks |
| firewall_setup | Yes | Module-based (firewalld) or rule-check (iptables/ufw) |
| hammerspace_integration | Yes | GET check (404 = create, 200 = skip) on all resources |

### Edge Cases

| Issue | Impact |
|-------|--------|
| Manually removed RAID not recreated unless `force_raid_recreate: true` | By design — safety check |
| Mount protection templates overwritten every run | Cosmetic — content identical |
| CSV report uses `regexp` in `lineinfile` | Prevents duplicates on re-run |

---

## 7. Error Handling

### Patterns

| Pattern | Usage |
|---------|-------|
| `failed_when: false` | API existence checks (404 = not found, not error) |
| `ignore_errors: true` | iptables flush, optional services |
| `block/rescue` | RAID creation (per-array), filesystem creation |
| Per-item error tracking | RAID arrays — `_raid_failed_arrays` / `_raid_succeeded_arrays` |
| FAILED/CANCELLED detection | All 17 API polling loops |
| Safe JSON parsing | `default('{}', true) \| from_json` with `stdout_lines[-1]` for HTTP status |

### RAID Error Recovery Flow

```
mdadm --create loop (failed_when: false)
  └─► Track results per array
      ├─► ALL fail → ansible.builtin.fail with full diagnostics
      ├─► SOME fail → warning + continue with successful arrays
      └─► NONE fail → proceed normally
```

### API Error Recovery

All polling loops detect terminal states and fail explicitly:
```yaml
until: (result.stdout | from_json).status in ["COMPLETED", "FAILED", "CANCELLED"]
# Followed by:
fail when: status in ["FAILED", "CANCELLED"]
```

---

## 8. Check Mode Compatibility

| Phase | `--check` Support | Notes |
|-------|------------------|-------|
| Discovery | Partial | Shell tasks run (`check_mode: false`), results are read-only |
| Precheck | Full | All validation is read-only |
| RAID / Filesystem | Skipped | Cannot simulate mdadm/mkfs |
| NFS | Partial | Template diff shown, service changes skipped |
| Firewall | Full | Module-based, shows diff |
| Hammerspace API | Partial | GET checks run (`check_mode: false`), POST/PUT gated by `not ansible_check_mode` |

---

## 9. Code Quality

### Task Naming Conventions

537 tasks across 33 files — **100% have explicit names**. Standardized patterns:

| Verb | Usage | Example |
|------|-------|---------|
| Display | Status/output reporting | "Display RAID status", "Display CPU info" |
| Check | Pre-condition inspection | "Check if filesystem already exists" |
| Verify | Post-action validation | "Verify mounts", "Verify NFS exports" |
| Build | Payload/data construction | "Build DNS update payload" |
| Parse | Response extraction | "Parse curl response" |
| Initialize | Variable setup | "Initialize discovery variables" |
| Skip (...) | Conditional skip with parenthetical reason | "Skip firewall configuration (no firewall detected)" |
| Wait | Async polling | "Wait for task queue to clear" |
| Fail | Error termination | "Fail if node add task did not complete successfully" |

### Shell vs Command Usage

| Category | Count | Status |
|----------|-------|--------|
| `ansible.builtin.shell` (pipes, curl quoting, redirects) | ~110 | Correct — shell features required |
| `ansible.builtin.command` (simple executables) | ~32 | Correct |
| `ansible.builtin.uri` in roles | 0 | All API calls use curl (with uri fallback wrapper) |
| `ansible.builtin.uri` in playbooks | 1 | `preflight_check.yml` only (localhost) |

### Remaining Duplication

| Pattern | Location | Impact |
|---------|----------|--------|
| NFS client list builder | 2 copies in `nvme_discovery/tasks/main.yml` (HW RAID + normal path) | Low |
| `HammerspaceClient` class | Duplicated in all 5 Python scripts | Low |
| API path `/mgmt/v1.2/rest` | Hardcoded in `set_fact` + all scripts | Low — `hs_api_url` set once in main.yml |

---

## 10. Security

### Credential Protection

| Layer | Status | Details |
|-------|--------|---------|
| At rest | **Protected** | `vars/vault.yml` → `vault_hammerspace_api_password`; referenced via Jinja2 |
| In transit | **SSL available** | `hammerspace_api_validate_certs` controls TLS; `-k` flag conditional |
| During execution | **Exposed** | `curl -u user:pass` visible in `ps aux` on target nodes |
| Python scripts | **Protected** | `--password-file` and `HAMMERSPACE_PASSWORD` env var supported; `--password` CLI still available for backward compatibility |

### Vault Setup

```bash
# Encrypt vault (required before committing)
ansible-vault encrypt vars/vault.yml

# Run with vault
ansible-playbook site.yml --ask-vault-pass
ansible-playbook site.yml --vault-password-file ~/.vault_pass
```

### Remaining Security Items

| Issue | Risk | Remediation |
|-------|------|-------------|
| Curl `-u` in process list | Medium | Use `.netrc` file or `--config` with credential file |
| SSL verification off by default | Medium | Enable in production, provide CA cert path |

---

## 11. Scalability

### Estimated Performance

| Nodes | API Calls (est.) | Duration (est.) | Bottleneck |
|-------|------------------|-----------------|------------|
| 1 | ~20 | 5–10 min | None |
| 10 | ~200 | 15–20 min | None |
| 50 | ~1,000 | 30–45 min | API task queue |
| 100 | ~2,000 | 60–90 min | API queue + NFS restart |
| 500 | ~10,000 | 5–8 hours | API capacity, network saturation |

### Throttling Mechanisms

| Mechanism | Configuration | Default |
|-----------|--------------|---------|
| Task queue throttle | `hammerspace_max_queued_tasks` / `min` | 10 / 5 |
| Task queue polling | retries x delay | 100 x 10s = ~16 min |
| NFS restart throttle | `throttle:` directive | Limits concurrent restarts |
| Ansible forks | `ansible.cfg` | 5 (increase as needed) |
| Volume add timeout | retries x delay | 40 x 10s = 400s |
| Node add timeout | retries x delay | 30 x 10s = 300s |
| RAID sync timeout | retries x delay | 60 x 30s = 30 min |

---

## 12. Python Utility Scripts

5 scripts share a common `HammerspaceClient` class with configurable `--port` (default 8443), exponential backoff retry, `--dry-run` mode, and confirmation prompts. All support `--password-file`, `HAMMERSPACE_PASSWORD` env var, and interactive prompt for credential safety.

| Script | Lines | Purpose |
|--------|-------|---------|
| `assign_az_to_volumes.py` | 754 | Map GPU memory fabric topology → AZ prefixes on volumes |
| `set_availability_drop.py` | 608 | Set availability-drop on volumes before RMA/maintenance |
| `cleanup_instance_nodes.py` | 474 | Delete volumes and remove nodes (parallel, with task timeout) |
| `rename_oci_instances_az.py` | 393 | Rename OCI instances with AZ prefix from Hammerspace volumes |
| `add_volumes_to_group.py` | 324 | Add instance volumes to Hammerspace volume groups |

---

## 13. Testing

### Current State

| Tool | Status |
|------|--------|
| yamllint | Configured — `.yamllint` (200 char lines, 2-space indent) |
| ansible-lint | Configured — `.ansible-lint` (skip rules for shell/curl pattern) |
| Molecule | Not configured |
| pytest | Not configured |
| pre-commit | Not configured |
| CI/CD | Not configured |

### Recommended Test Strategy

| Layer | Tool | Priority |
|-------|------|----------|
| Role unit tests | Molecule + Docker | High |
| Python unit tests | pytest + responses | Medium |
| Pre-commit hooks | pre-commit (lint + secret scanning) | Medium |
| CI/CD pipeline | GitHub Actions (lint, syntax, molecule on PR) | Medium |

---

## 14. Documentation

| Document | Lines | Content |
|----------|-------|---------|
| `README.md` | 1,845 | Project overview, quick start, architecture |
| `DEPLOYMENT_GUIDE.md` | 1,045 | Full deployment walkthrough with examples |
| `DEPLOYMENT_GUIDE_DARKSITE_HDD.md` | 990 | Air-gapped HDD-specific variant |
| `RACK_OPERATIONS_RUNBOOK.md` | 765 | RMA, expansion, maintenance procedures |
| `VARIABLE_REFERENCE.md` | 297 | All variables with defaults and descriptions |
| `ARCHITECTURE_REVIEW.md` | — | This document |

---

## 15. Remaining Recommendations

### P1 — High

| # | Action | Effort |
|---|--------|--------|
| 1 | **Encrypt `vars/vault.yml`** before committing to VCS | 1 min |
| 2 | **Add Molecule tests** for discovery and precheck roles | Medium |

### P2 — Medium

| # | Action | Effort |
|---|--------|--------|
| 3 | Extract shared `HammerspaceClient` Python module from 5 scripts | Small |
| 4 | Migrate `preflight_check.yml` single `uri` call to curl (consistency) | Small |
| 5 | Create CI/CD pipeline for linting and syntax checking | Medium |
| 6 | Add pre-commit hooks (lint + secret scanning) | Small |

### P3 — Low

| # | Action | Effort |
|---|--------|--------|
| 7 | Create security setup guide (vault, certs, credential rotation) | Medium |
| 8 | Create error recovery guide | Medium |
| 9 | Add architecture decision records (ADRs) | Low |

### Completed

| Action | Status |
|--------|--------|
| Split `precheck/tasks/main.yml` into subtask files | Done |
| Create `VARIABLE_REFERENCE.md` | Done |
| Add `ansible.builtin.uri` fallback for compatible Python | Done |
| Standardize task naming conventions across roles | Done |
| Add `--password-file` / `HAMMERSPACE_PASSWORD` env var to Python scripts | Done |
