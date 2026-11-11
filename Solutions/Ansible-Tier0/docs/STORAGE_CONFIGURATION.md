# Storage Configuration

**Who this is for:** anyone who wants to understand or change *how the playbook
turns the drives in a server into Tier 0 storage* — which drives it picks, how
it groups them into RAID arrays, what it checks first, and how it keeps the
result mounted.

Most deployments never change anything in this document. The defaults follow
the Hammerspace Tier 0 Deployment Guide. Read on when you need to exclude a
drive, use hardware RAID, reuse an existing filesystem, or tune a check.

Every setting below lives in `vars/main.yml`; the full list with defaults is in
[VARIABLE_REFERENCE.md](../VARIABLE_REFERENCE.md).

---

## What happens on a server, in order

| Step | What it does | Tag to run it alone |
|---|---|---|
| 1. Discovery | Finds the data drives, identifies and protects the OS disk, groups drives by NUMA node | `discovery` |
| 2. Precheck | Validates drive count, sector size, NUMA balance, jumbo frames, packages | `precheck` |
| 3. RAID | Builds one `mdadm` array per NUMA node (RAID 0 by default) and makes it persistent | `raid` |
| 4. Filesystem | Formats each array as XFS and mounts it under `/hammerspace/hsvolN` (by UUID) | `filesystem` |
| 5. NFS | Configures the NFS server and exports every volume | `nfs` |
| 6. Performance | Applies Hammerspace tuning (RPC slots, NFSD block size, read-ahead, IRQ pinning, TCP) | `perf` |
| 7. Firewall | Opens the NFS ports (firewalld / UFW / iptables, detected automatically) | `firewall` |
| 8. Hammerspace | Registers the node and its volumes with the cluster | `hammerspace` |

```bash
ansible-playbook site.yml --tags discovery,precheck   # look before you leap
ansible-playbook site.yml --check                     # dry run of everything
```

---

## How drives are chosen

With `use_dynamic_discovery: true` (default) the playbook:

1. **Protects the OS disk.** Every system mountpoint (`/`, `/boot`,
   `/boot/efi`, `/usr`, `/var`, `/home`, …) and active swap is traced back to
   its physical disk. Those disks are never touched. If the playbook cannot
   identify any protected disk it stops — that is a safety feature, not a
   configuration problem (see [Troubleshooting](TROUBLESHOOTING.md#playbook-stops-with-boot--protected-disk-detection-returned-an-empty-list)).
2. **Finds the data drives** — NVMe by default (`storage_type: nvme`), or
   SCSI/SATA when you set `hdd`, `ssd`, `scsi` or `all`.
3. **Applies your exclusions** (below).
4. **Groups by NUMA node**, so each RAID array only uses drives attached to
   one CPU socket. This is what gives Tier 0 its local-NVMe performance.
5. **Adopts existing arrays.** If a drive is already a member of an `md`
   array (for example after a reboot renamed `md0` to `md127`), the playbook
   uses that array instead of failing or rebuilding it.

Before anything is formatted, a final check confirms that no protected disk is
among the drives about to be used.

### Choosing the device type

| `storage_type` | Scans | Use for |
|---|---|---|
| `nvme` (default) | `/dev/nvme*n1` | GPU servers, storage-optimised cloud VMs |
| `hdd` | `/dev/sd*` reported as rotational | Spinning disks |
| `ssd` | `/dev/sd*` reported as non-rotational | SATA/SAS SSDs on bare metal |
| `scsi` | all `/dev/sd*`, no rotational filter | Cloud managed disks (Azure reports every disk as rotational, so use this rather than `ssd`) |
| `all` | NVMe + SCSI | Mixed servers |

### Excluding drives

The OS disk is always excluded. To keep other drives out of Tier 0:

```yaml
nvme_exclude_devices: [nvme0n1]                # by name
nvme_exclude_paths: [/dev/nvme0n1]             # by path
nvme_exclude_serials: ["S5XXXX0123456789"]     # by serial (stable across reboots)
nvme_exclude_models: ["Samsung SSD 980 PRO"]   # every drive of a model
nvme_exclude_numa_nodes: [0]                   # every drive on a NUMA node
nvme_exclude_pcie_addresses: ["0000:03:00.0"]  # by PCIe address
nvme_exclude_pcie_prefixes: ["0000:03"]        # every drive on a PCIe bus
```

The same options exist for SCSI drives (`scsi_exclude_devices`,
`scsi_exclude_paths`, `scsi_exclude_serials`, `scsi_exclude_models`).

Typical reasons: keep a drive for swap or scratch, leave slow drives out,
separate a fast tier from a capacity tier.

The playbook prints what it excluded and why:

```
NVMe Exclusion Summary:
============================================
Boot device (always excluded): nvme0
Excluded by PCIe prefix: 0000:0

Devices excluded from RAID:
  - /dev/nvme0n1 (Micron_7450_MTFDKBG1T9TFR, Serial: 242649B2918C, NUMA: 0, PCIe: 0000:03:00.0)
  - /dev/nvme1n1 (Micron_7450_MTFDKBG1T9TFR, Serial: 242649B28F32, NUMA: 0, PCIe: 0000:04:00.0)
============================================
```

---

## How arrays are sized

```yaml
raid_level: 0                     # 0 = stripe (Tier 0 default), 1, 5, 10
raid_grouping_strategy: numa      # numa (default) | single | per_drive
raid_max_drives_per_array: 0      # 0 = all drives on the NUMA node in one array
raid_min_drives_per_array: 2      # fewer than this -> no array
raid_power_of_2_drives: false     # round down to 2/4/8/16 drives (good for RAID 0)
raid_leftover_drives: skip        # skip | separate | add_last | individual
```

| Drives | Strategy | Max per array | Result |
|---|---|---|---|
| 16 (8 per NUMA) | `numa` | 0 | 2 arrays of 8 → `hsvol0`, `hsvol1` |
| 16 (8 per NUMA) | `numa` | 4 | 4 arrays of 4 |
| 16 | `single` | — | 1 array of 16 |
| 8 | `per_drive` | — | no RAID, 8 individual XFS mounts |

What happens to drives that don't fit (`raid_max_drives_per_array: 4`, 7 drives):

| `raid_leftover_drives` | Result |
|---|---|
| `skip` (default) | 1 array of 4, 3 drives unused |
| `separate` | arrays of 4 + 3 |
| `add_last` | 1 array of 7 |
| `individual` | 1 array of 4 + 3 single-drive mounts |

Example discovery output:

```
Boot device (excluded): nvme8

Devices by NUMA node:
NUMA 0: /dev/nvme4n1, /dev/nvme5n1, /dev/nvme6n1, /dev/nvme7n1
NUMA 1: /dev/nvme0n1, /dev/nvme1n1, /dev/nvme2n1, /dev/nvme3n1

DYNAMIC RAID CONFIGURATION:
md0 (/dev/md0):  NUMA Node: 0  RAID Level: 0  Drives (4): nvme4n1 nvme5n1 nvme6n1 nvme7n1
md1 (/dev/md1):  NUMA Node: 1  RAID Level: 0  Drives (4): nvme0n1 nvme1n1 nvme2n1 nvme3n1
```

### CPU-aware tuning

With `cpu_optimized_raid: true` (default) the playbook detects the CPU and
picks a RAID chunk size, NVMe queue depth and I/O scheduler to match:

| CPU | Chunk | Queue depth | Scheduler |
|---|---|---|---|
| AMD EPYC Genoa (9004) | 512 KB | 1024 | none |
| AMD EPYC Milan / Rome | 512 KB | 512 | none |
| Intel Xeon Sapphire Rapids | 256 KB | 512 | none |
| Intel Xeon (other) | 256 KB | 256 | mq-deadline |
| NVIDIA Grace, Ampere Altra, ARM Neoverse V2 / Graviton4 | 512 KB | 1024 | none |
| AWS Graviton2 / 3, generic ARM | 512 KB | 512 | none |
| Anything else | 512 KB | 256 | none |

Override when you know better:

```yaml
# cpu_vendor_profile: amd_epyc_genoa   # force a profile
# raid_chunk_size: 512                 # KB
# nvme_queue_depth: 1024
# nvme_io_scheduler: none              # none | mq-deadline | kyber
```

---

## Alternatives to building RAID from raw drives

### Hardware RAID controller

Skip software RAID and format the controller's logical volumes directly:

```yaml
use_raid: false
hw_raid_devices:
  - /dev/sda        # -> /hammerspace/hsvol0
  - /dev/sdb        # -> /hammerspace/hsvol1
```

### Filesystems that already exist

If the array is already built and mounted — by your provisioning system, a
previous deployment, or a SAN — point Tier 0 at it. The playbook creates one
export directory inside each mount and wires NFS and Hammerspace around it.
It does **not** discover disks, run `mdadm` or `mkfs`, edit `/etc/fstab`, or
mount anything.

```yaml
existing_mount_paths:
  - /mnt/raid0                    # exported as /mnt/raid0/tier0
  - path: /data/array1
    subdir: tier0
  - path: /data/array2
    subdir: ''                    # export the mountpoint itself
existing_mount_subdir: tier0
```

Paths that are not mountpoints, and system mountpoints, are refused. The
Azure walkthrough for this mode is
[DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md](../DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md).

### Manual layout

Turn discovery off and describe the arrays yourself:

```yaml
use_dynamic_discovery: false

raid_arrays:
  - name: md0
    device: /dev/md0
    level: 0
    drives: [/dev/nvme0n1, /dev/nvme1n1, /dev/nvme2n1, /dev/nvme3n1]

mount_points:
  - path: /hammerspace/hsvol0
    device: /dev/md0
    fstype: xfs
    label: hammerspace-hsvol0
    mount_opts: defaults,nofail,discard
```

### Cloud scratch mounts that occupy the drive

Some cloud images (Azure CycleCloud HB-series, for example) mount the local
NVMe as scratch at `/tmp` or `/nvme` before you get to it. Because `/tmp` is a
system mountpoint the drive is protected and discovery finds nothing. To
release it:

```yaml
release_ephemeral_mountpoints:
  - /tmp
  - /nvme
```

The mountpoint is unmounted and removed from `/etc/fstab` before discovery.
A mountpoint whose disk also holds `/`, `/boot`, swap, etc. is always refused.
`/tmp` is usually busy on a running system, so expect to reboot once and
re-run.

---

## Precheck — what is validated before anything is written

| Check | What it looks for | Settings |
|---|---|---|
| Drive count | The number of drives you expect (including boot) | `expected_nvme_count`, `enforce_drive_count` |
| Drives in use | Data drives already mounted, in RAID or in LVM | `fail_on_drives_in_use` |
| NUMA balance | Equal drive counts per NUMA node | `warn_on_numa_imbalance` |
| Sector size | 4096-byte sectors, as Hammerspace recommends | `expected_sector_size`, `require_4k_sectors` |
| Jumbo frames | 9000-byte MTU end-to-end to the Anvil | `expected_mtu`, `network_test_targets`, `enforce_mtu_test` |
| Bandwidth (optional) | iperf throughput to Hammerspace nodes | `iperf_test_enabled`, … |
| Packages | `mdadm`, `xfsprogs`, `nvme-cli`, … | `fail_on_missing_packages` |

```yaml
expected_nvme_count: 9          # warn (or fail) if the count differs
enforce_drive_count: false
warn_on_numa_imbalance: true
expected_sector_size: 4096
require_4k_sectors: false
expected_mtu: 9000
network_test_targets:
  - "{{ hammerspace_api_host }}" # the Anvil; add other Tier 0 nodes if you like
enforce_mtu_test: false
```

Example summary:

```
PRE-SETUP VALIDATION SUMMARY
============================================
NVMe Drives:     Total found: 9   Expected: 9   In use (non-boot): 0   Boot device: nvme8
NUMA Balance:    NUMA 0: 4 drives   NUMA 1: 4 drives   Status: BALANCED
Sector Size:     Expected: 4096 bytes   Drives needing format: 0
Network:         MTU tests: 2/2 passed
Packages:        Missing: none
============================================
```

### Bandwidth test (optional)

Start an iperf server on the Hammerspace nodes you want to test against
(`iperf3 -s -D`, port 5201), then:

```yaml
iperf_test_enabled: true
iperf_version: "iperf3"                # or "iperf" (legacy, port 5001)
iperf_test_targets:
  - "10.0.13.213"                      # Anvil
  - "10.0.0.93"                        # DSX / mover
iperf_test_duration: 10
iperf_test_parallel: 64                # streams — 64 for 200 Gbps networks
iperf_min_bandwidth_mbps: 40000
iperf_enforce_bandwidth: false         # true = fail below the minimum
```

If the test cannot connect, check that the server is running and the port is
open. Low numbers usually mean an MTU mismatch or a switch setting.

### Formatting drives to 4K sectors (optional, destructive)

Hammerspace recommends 4096-byte sectors. The precheck can reformat drives
that use 512-byte sectors — this **erases the drives** and is for new
deployments only:

```yaml
format_nvme_to_4k: true
nvme_format_confirm: "YES_I_UNDERSTAND_THIS_IS_DESTRUCTIVE"
```

---

## Filesystem and mounts

- XFS with `agcount=512` (`xfs_agcount`), as Hammerspace recommends.
- Mounted by **UUID** in `/etc/fstab`, so a device rename after reboot does
  not break the mount:

  ```
  UUID=a1b2c3d4-...  /hammerspace/hsvol0  xfs  defaults,nofail,discard  0 0
  ```

- `/etc/mdadm.conf` is written with each array's UUID, `mdmonitor` is enabled
  and the initramfs is refreshed so arrays assemble at boot.

### Mount protection (on by default)

`hammerspace_mount_protection: true` installs a small systemd safety net so a
volume cannot be unmounted by accident and comes back on its own if it is:

| Piece | What it does |
|---|---|
| fstab options | `nofail,x-systemd.automount,x-systemd.device-timeout=10` — the server boots even if a volume is missing |
| `hammerspace-<vol>-guard.service` | Keeps a process inside each mount so `umount` reports "target is busy" |
| `hammerspace-remount.timer` | Every minute, remounts anything that is unmounted |

```yaml
hammerspace_mount_protection: true
hammerspace_mount_guard_enabled: true
hammerspace_remount_watchdog_enabled: true
hammerspace_remount_watchdog_interval: "1min"
hammerspace_automount_timeout: 10
```

Useful commands:

```bash
systemctl list-units 'hammerspace-*-guard.service'   # guards
systemctl status hammerspace-remount.timer            # watchdog
journalctl -t hammerspace-remount                     # what the watchdog did

# Unmount on purpose:
systemctl kill hammerspace-hsvol0-guard.service
umount /hammerspace/hsvol0
```

---

## NFS server settings

`/etc/nfs.conf` is written with the Hammerspace-recommended values:

```ini
[nfsd]
threads=128
vers3=y
vers4.0=n
vers4.1=n
vers4.2=y
rdma=y
rdma-port=20049
```

Exports are generated from three lists in `vars/main.yml`:

| List | Who | Export options |
|---|---|---|
| `hammerspace_nodes` | Anvil and DSX nodes (defaults to the Anvil in `hammerspace_api_host`) | `rw,no_root_squash,sync,secure,mp,no_subtree_check` |
| `mover_nodes` | DI / mover servers (DI nodes in the `di_nodes` group are added automatically) | same as above |
| `client_subnets` | Everything else (`0.0.0.0/0` by default) | `rw,root_squash,sync,secure,mp,no_subtree_check` |

The `mp` option stops an empty directory from being exported if the volume
underneath is not mounted.

> **How NFS picks the matching entry.** Exports match by *type* — a single IP
> beats a subnet, which beats `0.0.0.0/0` — regardless of the order on the
> line. So the Anvil and mover IPs always win over the client subnet. But a
> second subnet listed after `0.0.0.0/0` is never used, so give hosts that
> need `no_root_squash` their own entry in `hammerspace_nodes` or
> `mover_nodes`; don't rely on a subnet.

Never edit `/etc/exports` by hand — the playbook regenerates it on every run.
To push an export change without restarting anything:

```bash
ansible-playbook site.yml --tags nfs-exports
```

---

## Performance tuning

The `perf_tuning` role applies the Hammerspace Tier 0 best-known methods.
Every section can be turned off individually:

| Setting | Default | What it does |
|---|---|---|
| `perf_tuning_enabled` | `true` | Master switch |
| `perf_sunrpc_tunables_enabled` | `true` | RPC slot table 128 / 65536 (kernel default is 2) |
| `perf_nfsd_tunables_enabled` | `true` | NFSD 1 MB block size, direct I/O |
| `perf_readahead_enabled` | `true` | 4 MB read-ahead on the volume devices |
| `perf_irq_pinning_enabled` | `true` | Pin 25 Gbps+ NIC interrupts to NUMA-local CPUs; stops `irqbalance` |
| `perf_tcp_sysctl_enabled` | `true` | High-throughput TCP buffers |

Re-tune existing nodes without touching storage:

```bash
ansible-playbook site.yml --tags perf
```

---

## Firewall

The playbook detects firewalld, UFW or plain iptables and opens:

| Port | Protocol | Service |
|---|---|---|
| 111 | TCP/UDP | rpcbind |
| 2049 | TCP/UDP | NFS |
| 20048 | TCP/UDP | mountd |
| 20049 | TCP | NFS over RDMA |

`flush_iptables: true` (default) clears stale iptables rules first — a common
cause of "NFS worked yesterday" on cloud images. Set `configure_firewall:
false` if your firewall is managed elsewhere.
