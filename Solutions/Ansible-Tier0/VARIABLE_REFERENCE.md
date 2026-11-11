# Variable Reference

Every setting in the Hammerspace Tier 0 Ansible automation, with its default
and what it does.

**Where to set things:** site settings go in `vars/main.yml`; passwords go in
`vars/vault.yml` (encrypt it with `ansible-vault encrypt vars/vault.yml`).
Anything you set in `vars/main.yml` overrides the role defaults. Anything can
also be passed on the command line with `-e name=value`.

---

## Start here — the settings most sites change

| Variable | Set it to | Why |
|----------|-----------|-----|
| `hammerspace_api_host` | Your Anvil management IP, via `-e hammerspace_api_host=<ip>` | The one value every deployment needs. Empty in `vars/main.yml`; the run stops immediately if it is not supplied. The NFS export list, network test and DI settings follow it automatically. |
| `vault_hammerspace_api_password` | The admin password, in `vars/vault.yml` | Used for every API call. |
| `client_subnets` | The subnets your NFS clients are on | Ships as `0.0.0.0/0`; narrow it before production. |
| `hammerspace_nodes` | Extra Anvil / DSX IPs, if any | The Anvil is included already. Listed IPs get `no_root_squash`. |
| `storage_type` | `nvme` (default), or `scsi` for `/dev/sd*` disks | Which drives are discovered. |
| `hammerspace_volume_group` | The name of your Tier 0 volume group | Volumes are added to it on every run. Must exist already, unless `hammerspace_create_volume_groups: true` (then a missing group is created with this run's volumes). May be set per host or per inventory group (e.g. `group_vars/AZ1`), or per run with `-e`; each host's volumes go to the group that host names, one write per distinct group. |
| `hammerspace_tier0_place_on_group` | `true` | Applies the group's placement objective to your shares so new files land on Tier 0 first. |
| `hammerspace_volume_az_prefix_enabled` | `true` on multi-zone deployments | Puts `AZ1:` etc. on volume names. |
| `hammerspace_api_validate_certs` | `true` once the Anvil has a trusted certificate | Off by default for self-signed certificates. |
| `deploy_di` | `true` if you are deploying mover nodes | Off by default. |

Everything else has a working default. The sections below are grouped by
topic in roughly the order the playbook uses them.

---

## RAID Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `use_raid` | `true` | Enable software RAID (mdadm). Set `false` for hardware RAID or per-drive mode. |
| `hw_raid_devices` | *(undefined)* | List of HW RAID logical volumes (e.g., `/dev/sda`, `/dev/md126`). Only used when `use_raid: false`. |
| `xfs_agcount` | `512` | XFS allocation group count for `mkfs.xfs`. Hammerspace recommended. Remove to use auto. |
| `raid_level` | `0` | RAID level for dynamic arrays (0, 1, 5, 10). |
| `raid_chunk_size` | `512` | RAID chunk size in KB. Auto-tuned when `cpu_optimized_raid: true`. |
| `raid_max_drives_per_array` | `0` | Max drives per array. `0` = unlimited (all drives per NUMA node). |
| `raid_min_drives_per_array` | `2` | Minimum drives to form an array. Arrays with fewer are skipped. |
| `raid_grouping_strategy` | `numa` | Grouping mode: `numa`, `single` (one array), `per_drive` (no RAID). |
| `raid_power_of_2_drives` | `false` | Round drive count down to nearest power of 2. Recommended for RAID 0. |
| `raid_leftover_drives` | `skip` | Handle leftover drives: `skip`, `separate`, `add_last`, `individual`. |
| `force_raid_recreate` | `false` | Force recreation of existing arrays. **DESTRUCTIVE**. |
| `wait_for_sync` | `true` | Wait for RAID sync on mirrored/parity arrays. |
| `sync_timeout` | `180` | Sync wait timeout in minutes. |

## Storage Device Type

| Variable | Default | Description |
|----------|---------|-------------|
| `storage_type` | `"nvme"` | Device types to discover: `nvme`, `hdd`, `ssd`, `scsi`, `all`. Note `ssd`/`hdd` scan **only** `/dev/sd*` and filter on the kernel `rotational` flag — they never look at NVMe. Azure synthetic SCSI reports `rotational=1` even for Premium SSD, so `ssd` can silently drop every data disk; use `scsi` there, which applies no rotational filter. |
| `release_ephemeral_mountpoints` | `[]` | **OPT-IN, and the most destructive switch in this repo.** Exact mountpoints to unmount and remove from `/etc/fstab` *before* discovery, so their disks become Tier 0 candidates. For Azure/CycleCloud HB-series, where the local NVMe arrives mounted as scratch: `['/tmp', '/nvme']`. Empty (default) disables the feature entirely. **Hard refuse-list that cannot be overridden:** any mountpoint whose physical disk also backs `/`, `/boot`, `/boot/efi`, `/usr`, `/var`, `/etc`, `/home` or **active swap** is rejected outright. Nothing is reformatted by this step — the normal protected-disk gate still runs afterwards against the new state. No lazy unmount is used, so a busy mountpoint fails loudly; `/tmp` on a running system almost always is, and since the fstab entry is removed first the fix is to reboot and re-run. |
| `existing_mount_paths` | `[]` | **Existing-mount mode.** Use filesystems that are already built and mounted instead of discovering disks and building RAID. Entries are either a bare path (`/mnt/raid0`) or `{path, subdir}`. For each, an export directory is created inside the mount and wired into NFS + Hammerspace. In this mode the playbook does **not** discover disks, run `mdadm`, run `mkfs`, edit `/etc/fstab`, or mount anything — `nvme_discovery`, `raid_setup` and `filesystem_setup` are all skipped, and the only write is `mkdir`. **Refused:** a path that is not actually a mountpoint, and any system mountpoint (`/`, `/boot`, `/usr`, `/var`, `/etc`, `/home`, ...). Empty (default) uses normal discovery. |
| `existing_mount_subdir` | `tier0` | Directory created inside each `existing_mount_paths` entry and exported. Set an entry's `subdir` to `''` to export the mountpoint itself. |
| `existing_mount_dir_mode` / `_owner` / `_group` | `0755` / root / root | Permissions and ownership of the created export directory. |
| `use_dynamic_discovery` | `true` | Auto-discover drives and group by NUMA. Set `false` for manual `raid_arrays`. |
| `mount_base_path` | `/hammerspace` | Base path for mount points (`/hammerspace/hsvol0`, etc.). |

## NVMe Device Exclusion

| Variable | Default | Description |
|----------|---------|-------------|
| `nvme_exclude_devices` | `[]` | Exclude by device name (e.g., `nvme0n1`). |
| `nvme_exclude_paths` | `[]` | Exclude by path (e.g., `/dev/nvme0n1`). |
| `nvme_exclude_serials` | `[]` | Exclude by serial number. Consistent across reboots. |
| `nvme_exclude_models` | `[]` | Exclude by model name (e.g., `"Samsung SSD 980 PRO"`). |
| `nvme_exclude_numa_nodes` | `[]` | Exclude all drives on specific NUMA nodes. |
| `nvme_exclude_pcie_addresses` | `[]` | Exclude by PCIe address (e.g., `0000:03:00.0`). |
| `nvme_exclude_pcie_prefixes` | `[]` | Exclude by PCIe bus prefix (e.g., `0000:03`). |
| `additional_exclude_devices` | `[]` | Has no effect — kept for compatibility. Use `nvme_exclude_devices` / `scsi_exclude_devices`. |

## SCSI/SATA Device Exclusion

| Variable | Default | Description |
|----------|---------|-------------|
| `scsi_exclude_devices` | `[]` | Exclude by device name (e.g., `sda`). |
| `scsi_exclude_paths` | `[]` | Exclude by path (e.g., `/dev/sda`). |
| `scsi_exclude_serials` | `[]` | Exclude by serial number. |
| `scsi_exclude_models` | `[]` | Exclude by model name (e.g., `"VIRTUAL-DISK"`). |
| `expected_scsi_count` | *(undefined)* | Expected SCSI drive count. Set `0` or omit to skip check. |

## CPU-Optimized RAID

| Variable | Default | Description |
|----------|---------|-------------|
| `cpu_optimized_raid` | `true` | Auto-detect CPU and apply vendor-optimized RAID settings. |
| `cpu_vendor_profile` | `auto` | Manual override: `auto`, `amd_epyc`, `amd_epyc_genoa`, `intel_xeon`, `intel_xeon_sapphire`. |
| `nvme_queue_depth` | *(auto)* | NVMe queue depth. Auto-tuned based on CPU cores. |
| `nvme_io_scheduler` | *(auto)* | I/O scheduler: `none`, `mq-deadline`, `kyber`. |

## Manual RAID Configuration

Used when `use_dynamic_discovery: false`.

| Variable | Default | Description |
|----------|---------|-------------|
| `raid_arrays` | *(undefined)* | List of manual RAID arrays with `name`, `device`, `level`, `drives`. |
| `mount_points` | *(undefined)* | List of manual mount points with `path`, `device`, `fstype`, `label`, `mount_opts`. |

## Hammerspace API Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_api_host` | `""` *(required — pass with `-e`)* | Anvil management IP for API access. Empty by default so no site inherits another's cluster; the run fails at start, naming this variable, if it is empty. Set once; other settings derive from it. |
| `hammerspace_api_user` | `"admin"` | API username (admin role required). |
| `hammerspace_api_password` | `"{{ vault_hammerspace_api_password }}"` | API password. Sourced from Ansible Vault. |
| `hammerspace_api_port` | `8443` | API port. |
| `hammerspace_api_validate_certs` | `false` | Validate SSL certs. Set `true` for production CAs. |
| `hammerspace_api_curl_timeout` | `60` | Per-request curl timeout in seconds. |
| `hammerspace_api_method` | `"auto"` | API HTTP method: `auto` (detect Python version), `curl` (always), `uri` (always). |
| `hammerspace_debug` | `false` | Enable debug output for API calls. |
| `hammerspace_keyvault_uri` | *(unset)* | **Azure only, opt-in.** Pull the admin password from this Key Vault (`https://<vault>.vault.azure.net`) instead of `vars/vault.yml`. |
| `hammerspace_keyvault_secret_name` | *(unset)* | Name of the secret in that vault. |
| `hammerspace_keyvault_auth_source` | `"auto"` | How the control machine authenticates to Azure: `auto`, `cli`, `credential_file`, `env`, `msi`. |

## Volume Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_add_volumes` | `true` | Auto-add discovered volumes to Hammerspace. |
| `hammerspace_volume_high_threshold` | `0.98` | Utilization threshold triggering data evacuation (98%). |
| `hammerspace_volume_low_threshold` | `0.90` | Target utilization after evacuation (90%). |
| `hammerspace_volume_max_suspected_seconds` | `0` | Seconds before suspected → unavailable. `0` = immediate. |
| `hammerspace_volume_unavailable_multiplier` | `0` (vars) / `1` (defaults) | `0` = availability drops to 0 when unavailable; `1` = unchanged. |
| `hammerspace_volume_availability` | `2` | Target availability level. |
| `hammerspace_volume_durability` | `3` | Target durability level. |
| `hammerspace_skip_performance_test` | `true` | Skip perf test on volume add (faster deployment). |
| `hammerspace_additional_ips` | *(undefined)* | Additional IPs for volume access. |
| `hammerspace_excluded_ips` | *(undefined)* | IPs to exclude from volume access. |

## Volume Naming and AZ Prefix

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_volume_az_prefix_enabled` | `false` | Enable AZ prefix in volume names. |
| `hammerspace_volume_az_prefix_mode` | `""` | Prefix mode: `""` (none), `"auto"` (from OCI fault domain), `"AZ1:"` (static). |
| `hammerspace_volume_az_prefix` | *(undefined)* | Direct override. Ignores mode settings if defined. |
| `hammerspace_node_name` | `"{{ inventory_hostname }}"` | Node name in Hammerspace. Use `"AZ1:hostname"` for AZ prefix. |
| `hammerspace_node_ip` | `"{{ ansible_default_ipv4.address }}"` | Node IP for Hammerspace registration. |
| `hammerspace_create_placement_objectives` | `true` | Create placement objectives for volumes. |

## Anvil Overload Protection

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_serial` | `0` | Process N nodes at a time through hammerspace_integration (0 = all in parallel). Use for large deployments. Can also be set via `-e hammerspace_serial=2`. |

## Task Queue Throttling

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_task_queue_throttle` | `true` | Enable task queue throttling to protect cluster. |
| `hammerspace_min_queued_tasks` | `5` | Resume operations when queue drops below this. |
| `hammerspace_max_queued_tasks` | `10` | Pause operations when queue exceeds this. |
| `hammerspace_task_queue_retries` | `100` | Max retries for queue wait (100 x 10s = ~16 min). |
| `hammerspace_task_queue_delay` | `10` | Delay between retries in seconds. |
| `hammerspace_monitor_executing_tasks` | `false` | Monitor executing (not just queued) tasks. |
| `hammerspace_wait_for_executing` | `false` | Wait for executing tasks to complete. |

## API Timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_node_add_retries` | `30` | Node add poll retries (30 x 10s = 5 min). |
| `hammerspace_node_add_delay` | `10` | Node add poll delay in seconds. |
| `hammerspace_volume_add_retries` | `40` | Volume add poll retries (40 x 10s = ~7 min). |
| `hammerspace_volume_add_delay` | `10` | Volume add poll delay in seconds. |
| `raid_sync_retries` | `60` | RAID sync poll retries (60 x 30s = 30 min). |
| `raid_sync_delay` | `30` | RAID sync poll delay in seconds. |

## Availability Zone Mapping

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_enable_az_mapping` | `false` | Parse AZ from node name (format: `"AZ1:node-name"`). |
| `hammerspace_apply_az_labels` | `false` | Apply AZ labels to nodes via API. |
| `hammerspace_default_az` | `"AZ1"` (vars) / `"default"` (defaults) | Default AZ when not detected. |
| `hammerspace_azure_imds_az` | `false` | Query Azure IMDS for `zone` / `platformFaultDomain`. Enable on Azure. |
| `azure_imds_api_version` | `"2021-02-01"` | Azure IMDS API version. |
| `azure_imds_timeout` | `5` | IMDS request timeout in seconds. |
| `hammerspace_gpu_fabric_az` | `false` | **OCI GPU only.** Derive the AZ from the instance's GPU memory fabric; instances on the same fabric share an AZ. |
| `hammerspace_gpu_fabric_az_update_file` | `true` | Record each node's fabric → AZ mapping in `gpu_fabric_data.txt` for later runs. |

### Cloud placement host variables

Supplied by the dynamic inventories, or by `azure_imds_az.yml` when
`hammerspace_azure_imds_az: true`.

| Variable | Source | Base | Maps to |
|----------|--------|------|---------|
| `oci_fault_domain` | `inventory.oci.yml` | `FAULT-DOMAIN-N` | `AZN` |
| `azure_zone` | `inventory.azure.yml`, Azure IMDS | **1-based** (`"1"`, `"2"`) | `AZ1`, `AZ2` |
| `azure_fault_domain` | Azure IMDS only | **0-based** (`"0"`, `"1"`) | `AZ1`, `AZ2` (i.e. `+1`) |

Precedence: `hammerspace_node_az` > `oci_fault_domain` > `azure_zone` >
`azure_fault_domain` > `"AZ1:"` prefix in the node name > inventory group
matching `^AZ[0-9]+$` > `hammerspace_default_az`.

`azure_zone` is an **empty string** — not absent — on non-zonal Azure VMs
(availability sets), so it is tested for length rather than `is defined`.

The same detected value is used for the node's AZ label, the volume-name
prefix and `instance_report.csv`, so the three always agree.

## Volume Groups

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_create_volume_groups` | `false` | Allow the playbook to create volume groups. When `true`: a `hammerspace_volume_group` that does not exist is created with this run's volumes as members (e.g. one group per Slurm job), and every group in `hammerspace_volume_groups` is created if missing. An existing group is never modified beyond the normal append. A created group is only reported once the cluster lists it. A group created this way carries only the objectives Hammerspace creates automatically (`place-on-<group>` etc.). |
| `hammerspace_volume_groups` | *(undefined)* | List of groups to create, each with `name` and either `volumes` (list of storage volume names) or `location_pattern`. Requires `hammerspace_create_volume_groups: true`. |
| `hammerspace_volume_group_create_wait_retries` / `_delay` | `12` / `5` | How long to wait for a newly created group to be listed before failing. |
| `hammerspace_volume_group_members` | *(undefined)* | List of `volume_name` + `group_name` pairs. |

## Shares

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_create_shares` | `false` | Create Hammerspace shares. |
| `hammerspace_update_existing_shares` | `false` | Update shares that already exist. |
| `hammerspace_shares` | *(undefined)* | List of shares with `name`, `path`, `export_options`, optional `objective`. |
| `default_share_export_options` | `[{subnet: "*", accessPermissions: "RW", rootSquash: false}]` | Default export options for shares. |

## Share Objectives

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_apply_share_objectives` | `false` | Apply objectives to shares. |
| `hammerspace_share_objectives` | *(undefined)* | List with `share_name`, `objective`, `applicability`. |
| `hammerspace_tier0_place_on_group` | `false` | Apply the volume group's auto-created `place-on-<hammerspace_volume_group>` objective to each share in `hammerspace_shares`, so first instances land on Tier 0 instead of being spread across every volume (DSX-backed included). Requires `hammerspace_volume_group`. Missing objective/share/group is a hard failure, and so is a run naming more than one group (a share objective names one — set `hammerspace_tier0_place_on_objective`, use a group containing them all, or run one AZ at a time). Selects the tier, not the node. |
| `hammerspace_tier0_place_on_objective` | `place-on-<hammerspace_volume_group>` | Override the objective name if the group's objective was renamed. |
| `hammerspace_tier0_objective_wait_retries` / `_delay` | `12` / `5` | For a group created in the same run, how long to wait for its `place-on-<group>` objective to appear. A group that already existed is not waited on. |
| `hammerspace_tier0_place_on_shares` | names in `hammerspace_shares` | Shares to apply it to; set to target shares created outside this playbook. |
| `hammerspace_tier0_place_on_applicability` | `TRUE` | Objective applicability expression, e.g. `LAST_USE_AGE<1*HOURS?TRUE`. |

## S3/Object Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_add_s3_nodes` | `false` | Add S3 storage nodes. |
| `hammerspace_add_object_volumes` | `false` | Add object storage volumes. |
| `hammerspace_create_s3_server` | `false` | Create S3 protocol server. |
| `hammerspace_create_s3_users` | `false` | Create S3 user accounts. |
| `hammerspace_s3_nodes` | *(undefined)* | S3 node configs: `name`, `region`, `access_key_id`, `secret_access_key`. |
| `hammerspace_object_volumes` | *(undefined)* | Object volumes: `name`, `node_name`, `bucket_name`, `prefix`. |
| `hammerspace_s3_server` | *(undefined)* | S3 server config: `name`, `port`, `tls_enabled`, `enabled`. |
| `hammerspace_s3_users` | *(undefined)* | S3 users: `name`, `access_key_id`, `secret_access_key`, `enabled`. |

## Cluster Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_update_dns` | `false` | Update DNS settings on cluster. |
| `hammerspace_join_ad` | `false` | Join Active Directory domain. |
| `hammerspace_update_site_name` | `false` | Update cluster site name. |
| `hammerspace_set_location` | `false` | Set physical location metadata. |
| `hammerspace_enable_prometheus` | `false` | Enable Prometheus monitoring endpoint. |
| `hammerspace_dns_config` | *(undefined)* | DNS: `primary_server`, `secondary_server`, `search_domains`. |
| `hammerspace_ad_config` | *(undefined)* | AD: `domain`, `username`, `password`, `ou`. |
| `hammerspace_site_config` | *(undefined)* | Site: `name`. |
| `hammerspace_location_config` | *(undefined)* | Location: `datacenter`, `room`, `row`, `rack`, `position`. |
| `hammerspace_prometheus_config` | *(undefined)* | Prometheus: `port`. |

## NFS Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `nfs_threads` | `128` | NFS server thread count. Hammerspace recommends 128. |
| `nfs_exportfs_strict` | `true` | Treat any warning from `exportfs -ra` as a failure. `exportfs` exits 0 even when it skips an entry, so the warning is the only sign that a path is not exported. |
| `nfs_vers3` | `"y"` | Enable NFSv3. |
| `nfs_vers4_0` | `"n"` | Enable NFSv4.0. |
| `nfs_vers4_1` | `"n"` | Enable NFSv4.1. |
| `nfs_vers4_2` | `"y"` | Enable NFSv4.2. |
| `nfs_rdma_enabled` | `"y"` | Enable NFS over RDMA. |
| `nfs_rdma_port` | `20049` | RDMA port. |
| `hammerspace_nodes` | `["{{ hammerspace_api_host }}"]` | Anvil/DSX IPs requiring `no_root_squash`. Defaults to the primary Anvil (no duplication); add secondary Anvils/DSX IPs here. |
| `mover_nodes` | `[]` | Mover/DI node IPs requiring `no_root_squash` — every data-path IP a DSX uses to reach the node. A list: pass it as `-e '{"mover_nodes": ["10.0.0.20"]}'` or in a `-e @file`. DI nodes in `di_nodes` are added automatically (`di_auto_export`). |
| `client_subnets` | `["0.0.0.0/0"]` | Client subnets with `root_squash`. exports(5) matches by entry TYPE (single host > IP network > wildcard > netgroup > `*`), not line order; a second subnet after `0.0.0.0/0` is never matched. |
| `hammerspace_export_opts` | `"rw,no_root_squash,sync,secure,mp,no_subtree_check"` | Export options for Hammerspace nodes. |
| `client_export_opts` | `"rw,root_squash,sync,secure,mp,no_subtree_check"` | Export options for clients. |
| `nfs_exports` | *(undefined)* | Manual NFS exports (when `use_dynamic_discovery: false`). |
| `nfs_version` | `4` | NFS version (role default). |
| `default_export_options` | `"rw,sync,no_subtree_check"` | Role-level default export options. |

## Filesystem Setup

| Variable | Default | Description |
|----------|---------|-------------|
| `force_fs_recreate` | `false` | Force filesystem recreation. **DESTRUCTIVE**. |
| `default_fstype` | `xfs` | Default filesystem type. |
| `default_mount_opts` | `defaults,nofail,discard` | Default mount options. |

## Firewall Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `configure_firewall` | `true` | Enable firewall configuration. Set `false` if managed externally. |
| `flush_iptables` | `true` | Flush iptables before configuring. Clears REJECT rules blocking NFS. |
| `enable_ufw` | `false` | Enable UFW (Debian/Ubuntu only). Ensure SSH is allowed first. |
| `nfs_ports` | *(see below)* | NFS ports for UFW: 111/tcp+udp, 2049/tcp+udp, 20048/tcp+udp, 20049/tcp. |
| `firewalld_services` | `["nfs", "rpc-bind", "mountd"]` | Firewalld services (RHEL/Rocky/CentOS). |

## Mount Point Protection

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_mount_protection` | `true` | Enable systemd mount protection (guard + watchdog). |
| `hammerspace_mount_guard_enabled` | `true` | Guard service keeps process on mount to prevent unmount. |
| `hammerspace_remount_watchdog_enabled` | `true` | Watchdog auto-remounts if accidentally unmounted. |
| `hammerspace_remount_watchdog_interval` | `"1min"` | Watchdog check interval. |
| `hammerspace_automount_timeout` | `10` | systemd automount timeout for device availability. |

## Performance Tuning (`perf_tuning` role)

Hammerspace Tier 0 performance BKMs applied on `storage_servers` after `nfs_setup`.
Each section can be toggled independently. Role defaults live in `roles/perf_tuning/defaults/main.yml`.

| Variable | Default | Description |
|----------|---------|-------------|
| `perf_tuning_enabled` | `true` | Master toggle. Set `false` to skip the entire role. |
| `perf_sunrpc_tunables_enabled` | `true` | Tune sunrpc TCP slot tables (sysctl + modprobe.d). |
| `perf_sunrpc_tcp_slot_table_entries` | `128` | RPC slots per TCP connection (kernel default is 2). |
| `perf_sunrpc_tcp_max_slot_table_entries` | `65536` | Upper bound on slot growth. |
| `perf_nfsd_tunables_enabled` | `true` | NFSD server-side tuning. |
| `perf_nfsd_max_block_size` | `1048576` | NFSD max negotiated block size (bytes). 1MB is the kernel max. |
| `perf_nfsd_direct_io` | `true` | Enable nfsd direct I/O (bypasses page cache). Kernel-version dependent; warning emitted if unsupported. |
| `perf_readahead_enabled` | `true` | Apply read-ahead to NVMe/MD/SCSI block devices behind `/hammerspace/hsvol*`. |
| `perf_readahead_kb` | `4096` | Read-ahead in KB. Hammerspace recommends 1024-16384 for sequential workloads. |
| `perf_irq_pinning_enabled` | `true` | Pin NIC RX/TX queue IRQs to NUMA-local CPUs (oneshot systemd unit). |
| `perf_irq_pin_interfaces` | `[]` | Interfaces to pin. Empty = auto-detect 25Gbps+ NICs (`/sys/class/net/*/speed >= 25000`). |
| `perf_irq_pinning_stop_irqbalance` | `true` | Stop and disable `irqbalance` (it fights manual pinning). |
| `perf_tcp_sysctl_enabled` | `true` | Apply high-throughput TCP sysctl tunables (`rmem/wmem_max=256MB`, `tcp_mtu_probing=1`, etc.). |
| `perf_tcp_sysctl` | *(dict)* | Override individual sysctl values. See role defaults for the full map. |
| `perf_vm_writeback_enabled` | `false` | **Opt-in.** Cap dirty page cache for bulk NFS-client ingest (azcopy, rsync, cp). See note below. |
| `perf_vm_writeback` | *(dict)* | `vm.dirty_background_bytes` = 256 MB, `vm.dirty_bytes` = 1 GB. |
| `perf_vm_writeback_sysctl_filename` | `99-hammerspace-vm-writeback.conf` | Filename written under `/etc/sysctl.d/`. |

**Run just the perf section:** `ansible-playbook site.yml --tags perf`

**Run just the writeback limits:** `ansible-playbook site.yml --tags writeback -e perf_vm_writeback_enabled=true`

### `perf_vm_writeback` — dirty page cache limits for NFS ingest

Everything else in this role tunes the NFS **server** (nfsd threads, sunrpc pools,
read-ahead, IRQ affinity) or the wire (TCP buffers). `perf_vm_writeback` is the
only client-side writeback knob: it caps how much dirty page cache a bulk writer
on an NFS *mount* may accumulate before the kernel throttles it.

With no explicit cap the limit is `vm.dirty_ratio` = 20 % of available memory. On a
large-memory ingest host that is hundreds of GB of dirty NFS pages, which looks from
the outside like the ingest tool consuming enormous amounts of memory — the pages are
charged to the page cache and to `NFS_Unstable`, not to the process RSS.

**Off by default**, because the knob is global rather than per-mount: on a Tier 0
`storage_server` it also throttles writeback to the local NVMe / MD array behind
`/hammerspace/hsvol*`. Enable it on hosts whose job is NFS ingest.

**Set one form per limit, never both.** `vm.dirty_bytes` and `vm.dirty_ratio` are one
limit in two units, and the kernel keeps them exclusive by zeroing the other on write.
A file containing both therefore means "whichever line came last". The role refuses a
mixed configuration rather than persisting one whose meaning depends on line order,
and it also refuses:

| Configuration | Why it is refused |
|---|---|
| `vm.dirty_bytes` **and** `vm.dirty_ratio` (same for the `_background_` pair) | Writing either zeroes the other; the result depends on line order. |
| `vm.dirty_ratio: 0` or `vm.dirty_background_ratio: 0` | A zero-page threshold, not "unlimited". The kernel sets the ratio to 0 itself when you set the `_bytes` form — you never write it. |
| a `*_bytes` value below two pages | Silently ignored by the kernel; the previous value is retained. |
| `vm.dirty_background_bytes` ≥ `vm.dirty_bytes` | The kernel substitutes `vm.dirty_bytes / 2` for the background threshold. |

The role reads the values back from `/proc/sys/vm/` after applying them and fails if
the kernel did not keep them, and warns when another file under `/etc/sysctl.d/` (or
`/etc/sysctl.conf`, which `sysctl --system` applies last) sets a `vm.dirty_*` key that
would revert the tuning on the next boot.

## Safety Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `skip_confirmation` | `false` | Skip confirmation prompts. Use with caution. |
| `force_raid_recreate` | `false` | Force RAID recreation. **DESTRUCTIVE**. Overrides both the same-name skip and the new device-membership skip (the latter detects an array assembled under a different kernel name e.g. `md127`, remaps mounts to it, and skips `mdadm --create`). Set `true` only when you actually want to nuke the existing array. |
| `force_fs_recreate` | `false` | Force filesystem recreation. **DESTRUCTIVE**. |
| `allow_empty_protected_disks` | `false` | **DANGEROUS.** Bypasses the boot-drive safety gate. When `false` (default) the playbook stops if it cannot identify the disk(s) holding `/`, other system mounts or active swap — the last line of defence against formatting the OS disk. Set `true` only on diskless / netboot systems that have no OS disk. **Do not use it to silence the gate on a normal server** — the failure message prints the server's mount, swap and block-device layout; read that first, and if the server does have an OS disk, report it to Hammerspace support. Existing `md` arrays and LVM volumes are not part of this gate; they are adopted, not blocked. |

## Pre-Setup Validation (Precheck)

| Variable | Default | Description |
|----------|---------|-------------|
| `expected_nvme_count` | *(undefined)* | Expected NVMe count (including boot). Omit to skip check. |
| `enforce_drive_count` | `false` | Fail if drive count doesn't match expected. |
| `fail_on_drives_in_use` | `false` (vars) / `true` (defaults) | Fail if non-boot drives are mounted/in RAID/LVM. |
| `warn_on_numa_imbalance` | `true` | Warn if NUMA nodes have unequal drive counts. |
| `expected_sector_size` | `4096` | Expected sector size in bytes. |
| `require_4k_sectors` | `false` | Fail if drives don't have 4K sectors. |
| `format_nvme_to_4k` | `false` | Format NVMe to 4K sectors. **DESTRUCTIVE**. |
| `nvme_format_confirm` | *(undefined)* | Must be `"YES_I_UNDERSTAND_THIS_IS_DESTRUCTIVE"` to allow formatting. |
| `expected_mtu` | `9000` | Expected MTU for jumbo frames. |
| `mtu_ping_size` | `8972` | Ping payload size (MTU - 28 bytes ICMP overhead). |
| `enforce_mtu_test` | `false` | Fail if jumbo frame tests fail. |
| `network_test_targets` | `["{{ hammerspace_api_host }}"]` | IPs for jumbo frame connectivity tests. Defaults to the primary Anvil. |
| `fail_on_missing_packages` | `true` | Fail if required packages are missing. |

## iperf Bandwidth Testing

| Variable | Default | Description |
|----------|---------|-------------|
| `iperf_test_enabled` | `false` | Enable iperf bandwidth tests. |
| `iperf_version` | `"iperf3"` | iperf version: `"iperf3"` (recommended) or `"iperf"` (legacy). |
| `iperf_test_targets` | `[]` | Server IPs with iperf running. |
| `iperf_test_duration` | `10` | Test duration in seconds. |
| `iperf_test_parallel` | `64` | Parallel streams (64 recommended for 200Gbps). |
| `iperf_min_bandwidth_mbps` | `40000` | Minimum expected bandwidth in Mbits/sec. |
| `iperf_enforce_bandwidth` | `false` | Fail if bandwidth is below minimum. |

## Data Instantiator (DI) Deployment

| Variable | Default | Description |
|----------|---------|-------------|
| `deploy_di` | `false` | Master switch: enable DI deployment on `di_nodes` group. |
| `di_activate` | `true` | Activation control: `true` = full deploy; `false` = install only, skip services and registration. Activate later with `--tags di-activate -e di_activate=true`. |
| `di_auto_export` | `true` | Auto-wire DI node IPs into Tier 0 NFS exports (`no_root_squash`). Runs on every `site.yml` run whenever `di_nodes` is populated — not only with `deploy_di` — because the storage play regenerates `/etc/exports` each run. |
| `di_deployment_type` | *(set explicitly)* | Deployment mode: `"host"` (RPM install), `"container"` (podman/docker), or `"kubernetes"` (DaemonSet on containerd). Set it in `vars/main.yml` rather than relying on the shipped value. |

## DI Container Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `di_container_runtime` | `"podman"` | Container runtime: `"podman"` or `"docker"`. |
| `di_container_base_image` | `"rockylinux:9"` | Base image (must be el9 for pd-di RPMs). |
| `di_container_image` | `"hammerspace-di:local"` | Built container image name/tag. |
| `di_container_name` | `"pd-di"` | Container instance name. |
| `di_container_build_dir` | `"/opt/hammerspace/di-container"` | Build context directory on target. |
| `di_container_privileged` | `true` | Run container privileged (required for NFS + LTTng). |
| `di_container_host_network` | `true` | Use host networking (required for ports 9095/9096). |
| `di_container_restart_policy` | `"unless-stopped"` | Container restart policy. |
| `di_image_source` | `"build"` | Image source: `"build"` (Containerfile on target), `"local"` (tar from controller), `"url"` (download tar), `"registry"` (pull from registry). |
| `di_image_local_path` | *(undefined)* | Path to image tar on controller (when `di_image_source == "local"`). |
| `di_image_url` | *(undefined)* | URL to download image tar (when `di_image_source == "url"`). |
| `di_image_registry_ref` | *(undefined)* | Registry reference to pull (when `di_image_source == "registry"`). |

## DI Cluster Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_cluster_mgmt_ip` | `"{{ hammerspace_api_host }}"` | Anvil management IP for DI (defaults to Tier0 API host). |
| `hammerspace_cluster_hostname` | `"data-cluster"` | Hostname added to `/etc/hosts` on DI nodes. |

## DI Package Source

| Variable | Default | Description |
|----------|---------|-------------|
| `di_rpm_source` | `"directory"` | Source mode: `"url"`, `"local"`, `"directory"`. |
| `di_tarball_url` | *(Hammerspace URL)* | Tarball download URL (when `di_rpm_source == "url"`). |
| `di_tarball_local_path` | *(undefined)* | Controller-local tarball path (when `di_rpm_source == "local"`). |
| `di_payload_local_dir` | `"{{ repo_root }}/payload"` | Directory with loose RPMs (when `di_rpm_source == "directory"`). |
| `babeltrace_rpm_url` | *(Rocky Linux URL)* | Babeltrace RPM URL (RedHat only). |
| `di_add_node_script_url` | *(Hammerspace URL)* | Fallback URL for `add_node.py`. |
| `di_tmp_dir` | `"/tmp/di_install"` | Temp directory for tarball extraction. |

## DI Node Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `di_node_netmask_prefix` | `24` | Netmask prefix for `add_node.py -l` flag. |
| `di_registration_method` | `"script"` | Registration method (`"script"` uses `add_node.py`). |
| `di_node_name` | `inventory_hostname` | Override per host in inventory. |
| `di_node_ip` | `ansible_default_ipv4.address` | Override per host in inventory. |
| `di_node_az` | *(auto-detected)* | Explicit AZ for a DI node. Auto-detected from inventory group names (`AZ1`, `AZ2`, ...) if not set. |

## DI AZ Distribution

| Variable | Default | Description |
|----------|---------|-------------|
| `di_min_az_count` | `2` | Minimum AZs DI nodes should span. Set to `1` to disable. |
| `di_enforce_az_distribution` | `false` | `true` = fail if below `di_min_az_count`; `false` = warn only. |

## DI Firewall

| Variable | Default | Description |
|----------|---------|-------------|
| `di_firewall_ports` | `[{port: 9095, proto: tcp}, {port: 9096, proto: tcp}]` | Ports for DI communication with Anvil. |

## DI SELinux

| Variable | Default | Description |
|----------|---------|-------------|
| `di_disable_selinux` | `true` | Disable SELinux on RedHat systems. |
| `di_selinux_reboot` | `true` | Reboot after SELinux changes. |

## DI Decommission

| Variable | Default | Description |
|----------|---------|-------------|
| `di_decommission_evacuate_data` | `true` | Evacuate data before removing volumes. |
| `di_decommission_stop_services` | `true` | Stop DI services after decommission (skipped in k8s mode). |
| `di_decommission_poll_retries` | `180` | Max poll retries (180 x 10s = 30 min). |
| `di_decommission_poll_delay` | `10` | Poll delay in seconds. |
| `di_k8s_decommission_delete_namespace` | `false` | When `true`, k8s decommission also deletes the `di_k8s_namespace`. |

## DI Kubernetes Mode (`di_deployment_type: "kubernetes"`)

Only consulted when `di_deployment_type == "kubernetes"`. Operator MUST set
`di_k8s_registry_namespace` (and registry credentials, unless using a public
image). Everything else has a sensible default.

| Variable | Default | Description |
|----------|---------|-------------|
| `di_k8s_kubeconfig` | `$KUBECONFIG` or `~/.kube/config` | Kubeconfig the controller uses for all k8s API calls. |
| `di_k8s_namespace` | `"hammerspace-di"` | Namespace for the DaemonSet, secrets, pull-secret. Created with PodSecurityAdmission = `privileged`. |
| `di_k8s_psa_level` | `"privileged"` | PSA profile applied to the namespace (`privileged` required for hostNetwork + privileged SC). |
| `di_k8s_node_selector_label` | `"hammerspace.io/di"` | Node label that scheduling gates on. Same label is the customer-visible "DI is here" tag. |
| `di_k8s_node_selector_value` | `"true"` | Value paired with the selector label. |
| `di_k8s_target_nodes` | `[]` | Explicit list of k8s node names to label. When empty, hostnames from the `di_nodes` inventory group are used. |
| `di_k8s_tolerations` | `[]` | List of toleration dicts so the DS schedules on tainted GPU nodes. Example: `[{key: "nvidia.com/gpu", operator: "Exists", effect: "NoSchedule"}]`. |
| `di_k8s_priority_class` | `""` | Optional `priorityClassName` (recommend `system-node-critical` for production). |
| `di_k8s_status_label` | `"hammerspace.io/di-status"` | Node label set by `kubernetes_status_label.yml` after rollout — values `ready` / `degraded` / `failed`. |
| `di_k8s_registry` | `"{{ oci_region }}.ocir.io"` | Image registry hostname. |
| `di_k8s_registry_namespace` | *(required)* | OCIR tenancy namespace (or equivalent registry path segment). |
| `di_k8s_image_repository` | `"hammerspace-di"` | Image repository name within the registry. |
| `di_k8s_image_tag` | `"5.1.41-452"` | Image tag (defaults to `pd_di_version`). |
| `di_k8s_image_arch_suffix` | `true` | Append `-amd64`/`-arm64` to pushed tags (one per build host). |
| `di_k8s_image_pull_secret` | `"hammerspace-di-pull"` | Name of the `kubernetes.io/dockerconfigjson` Secret created in the namespace. |
| `di_k8s_registry_username` | `""` | Registry username (set in `vars/vault.yml`). |
| `di_k8s_registry_password` | `""` | Registry password/token (set in `vars/vault.yml`). |
| `di_k8s_registry_email` | `"noreply@hammerspace.com"` | Registry email (cosmetic; required by some registries). |
| `di_k8s_api_credentials_secret` | `"hammerspace-api-credentials"` | Opaque Secret holding `HAMMERSPACE_API_*` (consumed by the initContainer via `envFrom`). |
| `di_k8s_build_push` | `true` | Build + push the image as part of the playbook. Set `false` to deploy a pre-existing image. |
| `di_k8s_build_tool` | `"auto"` | Build tool: `auto` (pick first available), `podman`, `docker`, `buildah`, `nerdctl`. |
| `di_k8s_build_multiarch_manifest` | `false` | When `true` and `buildah` is present on the build host, assemble a multi-arch manifest list. |
| `di_k8s_rollout_timeout` | `300` | Seconds to wait for the DaemonSet rollout. |
| `di_k8s_rollout_poll_delay` | `5` | Seconds between rollout polls. |
| `di_k8s_status_retries` | `30` | Retries when fetching DI pods for the status-label snapshot. |
| `di_k8s_status_delay` | `5` | Delay between status-label retries. |
| `di_k8s_auto_install_collection` | `true` | Auto-install `kubernetes.core` via `ansible-galaxy` on the controller. Set `false` to manage it manually. |

## Tier 0 Decommission (decommission_tier0.yml)

Removes nodes and volumes from Hammerspace. Touches no data on any host and
never connects to the storage nodes — every task is delegated to the
controller and talks only to the API.

| Variable | Default | Description |
|----------|---------|-------------|
| `decommission_confirm` | `false` | Safety gate — must be passed as `-e decommission_confirm=true` to proceed. Without it the play prints the plan and stops. |
| `decommission_explicit_volume_group_leave` | `false` | Remove volumes from their volume groups with an explicit PUT *before* deleting them. Not needed: deleting a volume removes it from its groups, and the playbook verifies that after the delete. Set to `true` only if the post-delete verification warns that references survived. |

Deleting a storage volume also removes it from its volume groups, so the
playbook relies on that and then re-reads the groups to confirm. If a group
still references a deleted volume it prints a warning (without failing — the
volumes and nodes are already gone) and names the flag above.

## Tier 0 Host Reset (reset-tier0-host.yml)

| Variable | Default | Description |
|----------|---------|-------------|
| `reset_confirm` | `false` | Safety gate — must be `true` to proceed. |
| `reset_hammerspace_cleanup` | `true` | Remove node/volumes from Hammerspace API before local teardown. |
| `reset_mount_root` | `"/hammerspace"` | Root path for Tier 0 mounts. |
| `reset_remove_mount_dirs` | `false` | Remove mount point directories and `/hammerspace` root. |
| `reset_run_blkdiscard` | `false` | Run blkdiscard on NVMe drives (fastest re-provision). |
| `reset_extra_mount_paths` | `[]` | Additional mount paths to tear down. |
| `reset_extra_md_devices` | `[]` | Additional md devices to destroy. |
| `reset_extra_member_drives` | `[]` | Additional member drives to wipe. |

## Vault Variables

Stored in `vars/vault.yml` (encrypt with `ansible-vault encrypt vars/vault.yml`).

| Variable | Description |
|----------|-------------|
| `vault_hammerspace_api_password` | Hammerspace API admin password. |
| `vault_ad_username` | Active Directory join username (if AD enabled). |
| `vault_ad_password` | Active Directory join password (if AD enabled). |
| `vault_s3_access_key` | S3 access key ID (if S3 enabled). |
| `vault_s3_secret_key` | S3 secret access key (if S3 enabled). |
| `vault_app_s3_key` | S3 user access key (if S3 users enabled). |
| `vault_app_s3_secret` | S3 user secret key (if S3 users enabled). |
