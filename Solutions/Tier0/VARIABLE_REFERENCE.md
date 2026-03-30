# Variable Reference

Complete reference for all configurable variables in the Hammerspace Tier 0 Ansible automation.

**Priority**: Variables in `vars/main.yml` override role `defaults/main.yml`.
Vault-encrypted values live in `vars/vault.yml`.

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
| `storage_type` | `"nvme"` | Device types to discover: `nvme`, `hdd`, `ssd`, `scsi`, `all`. |
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
| `additional_exclude_devices` | `[]` | Role-level additional excludes (controller names). |

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
| `hammerspace_api_host` | `"10.0.10.15"` | Anvil management IP for API access. |
| `hammerspace_api_user` | `"admin"` | API username (admin role required). |
| `hammerspace_api_password` | `"{{ vault_hammerspace_api_password }}"` | API password. Sourced from Ansible Vault. |
| `hammerspace_api_port` | `8443` | API port. |
| `hammerspace_api_validate_certs` | `false` | Validate SSL certs. Set `true` for production CAs. |
| `hammerspace_api_curl_timeout` | `60` | Per-request curl timeout in seconds. |
| `hammerspace_api_method` | `"auto"` | API HTTP method: `auto` (detect Python version), `curl` (always), `uri` (always). |
| `hammerspace_debug` | `false` | Enable debug output for API calls. |

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

## Volume Groups

| Variable | Default | Description |
|----------|---------|-------------|
| `hammerspace_create_volume_groups` | `false` | Create volume groups for organizing volumes. |
| `hammerspace_volume_groups` | *(undefined)* | List of groups with `name` and `location_pattern`. |
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
| `nfs_vers3` | `"y"` | Enable NFSv3. |
| `nfs_vers4_0` | `"n"` | Enable NFSv4.0. |
| `nfs_vers4_1` | `"n"` | Enable NFSv4.1. |
| `nfs_vers4_2` | `"y"` | Enable NFSv4.2. |
| `nfs_rdma_enabled` | `"y"` | Enable NFS over RDMA. |
| `nfs_rdma_port` | `20049` | RDMA port. |
| `hammerspace_nodes` | `["10.0.10.15"]` | Anvil/DSX IPs requiring `no_root_squash`. |
| `mover_nodes` | `["10.0.12.242"]` | Mover/DI node IPs requiring `no_root_squash`. |
| `client_subnets` | `["0.0.0.0/0"]` | Client subnets with `root_squash`. |
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

## Safety Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `skip_confirmation` | `false` | Skip confirmation prompts. Use with caution. |
| `force_raid_recreate` | `false` | Force RAID recreation. **DESTRUCTIVE**. |
| `force_fs_recreate` | `false` | Force filesystem recreation. **DESTRUCTIVE**. |

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
| `network_test_targets` | `["10.0.2.222"]` | IPs for jumbo frame connectivity tests. |
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
