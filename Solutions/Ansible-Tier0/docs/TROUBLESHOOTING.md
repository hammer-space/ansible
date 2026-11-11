# Troubleshooting

**Who this is for:** anyone whose run stopped, or whose deployment does not
look right afterwards. Find the message you saw, read the cause, apply the
fix.

Every run made with `./scripts/run.sh` is logged to
`~/logs/ansible-<timestamp>.log`. When you contact Hammerspace support, send
that file — it contains far more than the last screen of output.

Cloud-specific problems are covered in the cloud guides:
[OCI](../DEPLOYMENT_GUIDE.md#12-troubleshooting) ·
[Azure](../DEPLOYMENT_GUIDE_AZURE.md#13-troubleshooting) ·
[Darksite / HDD](../DEPLOYMENT_GUIDE_DARKSITE_HDD.md#14-troubleshooting).

---

## Contents

- [Before the playbook starts](#before-the-playbook-starts)
- [Connecting to the servers](#connecting-to-the-servers)
- [Disk discovery and the safety gate](#disk-discovery-and-the-safety-gate)
- [RAID and filesystems](#raid-and-filesystems)
- [NFS](#nfs)
- [Hammerspace](#hammerspace)
- [Data Instantiator](#data-instantiator)
- [Useful commands](#useful-commands)

---

## Before the playbook starts

### `ERROR: Ansible could not initialize the preferred locale`

**Cause:** the control machine's locale is not UTF-8 (common on Korean,
Japanese and other non-English systems).

**Fix:** run through the wrapper, which picks an installed UTF-8 locale for
you:

```bash
./scripts/run.sh playbook site.yml -i inventory.yml
```

If the wrapper says `no UTF-8 locale is installed on this host`, generate one:

```bash
sudo locale-gen en_US.UTF-8                    # Debian / Ubuntu
sudo dnf install -y glibc-langpack-en          # RHEL / Rocky
sudo localedef -i en_US -f UTF-8 en_US.UTF-8   # anywhere
```

### My shell alias for `ansible-playbook` is ignored by `run.sh`

Aliases don't survive `exec`. The wrapper sets its own log path
(`~/logs/ansible-<timestamp>.log`) and prints it on every run. Override with:

```bash
ANSIBLE_LOG_PATH=/custom/path.log ./scripts/run.sh playbook site.yml
ANSIBLE_LOG_DIR=/var/log/ansible  ./scripts/run.sh playbook site.yml
```

### `Hammerspace API configuration missing` / `hammerspace_api_password is defined … evaluated_to: false`

**Cause:** the password could not be loaded from `vars/vault.yml`. The
variable in `vars/main.yml` points at `vault_hammerspace_api_password`, and
that is either commented out in the vault file (a fresh checkout ships it as
an example), or the vault is encrypted and no vault password was supplied.

**Fix:**

```bash
grep -n vault_hammerspace_api_password vars/vault.yml   # must be uncommented
ansible-playbook site.yml --ask-vault-pass               # if the file is encrypted
# one-off, without the vault:
ansible-playbook site.yml -e hammerspace_api_password='...'
```

### `Could not find or access '/…/plays/…'` or `…/Ansible-Tier0/../cleanup_instance_nodes.py`

**Cause:** a stale copy of `vars/main.yml`. That file carries both your site
settings and a path helper (`repo_root`); an old version resolves the path
incorrectly for some playbooks.

**Fix:** update the repository and re-apply your site values to `vars/main.yml`
(the playbooks that need it now detect and report a stale definition).

### Hammerspace API preflight fails in the first 30 seconds

The playbook checks the Anvil before doing anything else:

| Message | Meaning | Fix |
|---|---|---|
| `HTTP response code: 401` | Wrong user or password | Check `hammerspace_api_user` and the vault password |
| `HTTP response code: 403` | User lacks the admin role | Use an admin account |
| `HTTP response code: 404` | Old Anvil without the `/cntl` endpoint | Update the Anvil |
| `HTTP response code: 0` | TCP connected but TLS/HTTP failed | Check `hammerspace_api_port` (8443) and Anvil health |
| `wait_for` timeout | Anvil unreachable from the control machine | Check routing, security lists, VPN |

---

## Connecting to the servers

### `Permission denied (publickey)`

Test SSH by hand with the same key: `ssh -i /path/to/key ubuntu@<ip> hostname`.
Supply the key via `~/.ssh/config`, `ssh-agent`, `ANSIBLE_PRIVATE_KEY_FILE` or
`ansible_ssh_private_key_file` in the inventory — not a hard-coded path in
`ansible.cfg`.

### `Connection timed out`

Port 22 is blocked by a security list / NSG, or you are on the wrong network
(VPN, bastion). See [INVENTORY_SETUP.md](INVENTORY_SETUP.md#static-inventory)
for the bastion configuration.

### The inventory is empty or missing hosts

```bash
ansible-inventory -i <file> --graph    # what Ansible sees
```

- **OCI:** `pip3 install oci`; check the `shape` filter matches your instances
  (`oci compute instance list --compartment-id <ocid> --query "data[].shape"`).
- **AWS / GCP:** check the tag or label filter and the region/zone list.
- **Azure:** the file must be named `*.azure.yml`; check the resource group
  and VM-size filter.

---

## Disk discovery and the safety gate

The playbook refuses to format anything until it has identified the OS disk.
Two messages come from that gate.

### Playbook stops with `Boot / protected-disk detection returned an EMPTY list`

**Meaning:** the playbook could not work out which disk holds `/`, `/boot`,
swap, etc., so it will not proceed — running `mkfs` without that list could
wipe the operating system.

**What to do:**

1. **Read the failure message.** It prints the server's mounts, swap and
   block-device tree. Usually the reason is visible there.
2. **Reproduce on the server** if needed:
   ```bash
   bash roles/nvme_discovery/files/scan_disks.sh     # expect PROTECTED / ROOT lines
   findmnt -n -o TARGET,SOURCE,MAJ:MIN,FSTYPE
   cat /proc/swaps
   ```
   The root filesystem must report a real device number (`MAJ:MIN`). If it
   does not, the server is in a state the playbook cannot reason about — fix
   the server first.
3. **Only on a genuinely diskless / netboot server** (no OS disk at all) set
   `allow_empty_protected_disks: true`. Never use it to silence the message on
   a normal server.

### `REFUSING TO PROCEED — candidate storage devices overlap with protected disks`

**Meaning:** the OS disk is among the drives about to be used for RAID.

**Do not override this.** Check your exclusion lists (`nvme_exclude_*`,
`scsi_exclude_*`) and `storage_type`. If they look right, send the
`Overlap:` line and the output of `lsblk -nrpso NAME,TYPE /dev/<disk>` to
Hammerspace support.

### `Discovery produced 0 RAID arrays`

| Check | Fix |
|---|---|
| `storage_type` matches the drives (`nvme` for `/dev/nvme*`, `scsi` for `/dev/sd*`) | Set it in `vars/main.yml` |
| A cloud image mounted the drive as scratch (`/tmp`, `/nvme`, `/mnt`) | See "Cloud scratch mounts" in [STORAGE_CONFIGURATION.md](STORAGE_CONFIGURATION.md#cloud-scratch-mounts-that-occupy-the-drive) |
| Every drive is excluded by an `*_exclude_*` list | Review the exclusion summary in the output |
| The server has existing arrays mounted at `/hammerspace/*` and you are on an old version | Update — current versions adopt existing arrays |

### `No NVMe drives found`

On the server: `lsblk` and `ls /dev/nvme*`. If the drives are `/dev/sd*`, set
`storage_type` accordingly.

### `Drive already in use`

A data drive is mounted, in a RAID array or in LVM. `cat /proc/mdstat` and
`mount | grep nvme` on the server. If the drive is a leftover from a previous
deployment you want to discard, use `reset-tier0-host.yml`
([OPERATOR_SCRIPTS.md](OPERATOR_SCRIPTS.md#reset-tier0-hostyml--back-to-bare-metal)).

---

## RAID and filesystems

### `mdadm: cannot open /dev/sdb: Device or resource busy` on a re-run

**Cause:** the array was built on the first run, the server rebooted, and the
kernel re-assembled it under a different name (`/dev/md127` instead of
`/dev/md0`).

**Fix:** current versions detect this and reuse the existing array — you will
see `Planned array 'md0' is already assembled as /dev/md127 — skipping
mdadm --create`. Just re-run. If you are on an older version, update. Only set
`force_raid_recreate: true` if you genuinely want to destroy the array.

### Arrays do not come back after a reboot

```bash
cat /etc/mdadm.conf          # should list every array with its UUID
mdadm --detail --scan
dracut --force               # RHEL / Rocky — rebuild the initramfs
update-initramfs -u          # Debian / Ubuntu
```

### A volume is unmounted

The remount watchdog fixes this within a minute. To do it now: `mount -a`.
If a mount keeps disappearing, check `journalctl -t hammerspace-remount` and
`dmesg` for the underlying device error.

---

## NFS

### A client (or the DSX) gets root-squashed

Only IPs in `hammerspace_nodes` and `mover_nodes` get `no_root_squash`. Add
every data-path IP the DSX or mover uses to reach the node — not just its
management IP — and re-run with `--tags nfs-exports`.

### `exportfs` warned about an entry

`exportfs -ra` exits 0 even when it skips an entry, so the playbook treats
any warning as a failure (`nfs_exportfs_strict: true`). Read the warning: it
usually names a path that is not mounted (`mp` option) or a bad client
specification.

### Verify exports

```bash
exportfs -v                   # on the server
showmount -e <server-ip>      # from a client or the Anvil
```

### A bulk copy into an NFS mount appears to consume all the memory

Typical with `azcopy`, `rsync`, `cp` or `dd` writing into a Hammerspace share.
Check where the memory actually is before tuning anything:

```bash
# Kernel side: dirty page cache waiting to be written back over NFS.
grep -E 'Dirty|Writeback|NFS_Unstable' /proc/meminfo

# Process side: the tool's own buffers.
ps -o pid,rss,comm -C azcopy
```

**If `Dirty` + `NFS_Unstable` are large and the process RSS is modest**, it is the
kernel, not the tool. With no explicit cap the dirty-page limit is `vm.dirty_ratio`
= 20 % of available memory, so a large-memory host will happily hold hundreds of GB
of dirty NFS pages before it throttles the writer. Cap it:

```bash
ansible-playbook site.yml --tags writeback -e perf_vm_writeback_enabled=true
```

That writes `/etc/sysctl.d/99-hammerspace-vm-writeback.conf` with
`vm.dirty_background_bytes` = 256 MB (start flushing) and `vm.dirty_bytes` = 1 GB
(throttle the writer). Off by default — the knob is global, so on a Tier 0 node it
also throttles writeback to the local NVMe behind `/hammerspace/hsvol*`. Size it for
the host, and see
[`perf_vm_writeback` in VARIABLE_REFERENCE.md](../VARIABLE_REFERENCE.md#perf_vm_writeback--dirty-page-cache-limits-for-nfs-ingest)
for why only the `*_bytes` form is accepted.

**If the process RSS is what is large**, the kernel limits will not help — the tool
is buffering in user space. For `azcopy`, cap it with `AZCOPY_BUFFER_GB` and reduce
`AZCOPY_CONCURRENCY_VALUE`.

Check what the kernel actually kept afterwards. It reports the unit it is *not*
using as `0`, so a `0` in `dirty_ratio` is expected once `dirty_bytes` is set:

```bash
grep . /proc/sys/vm/dirty_background_bytes /proc/sys/vm/dirty_background_ratio \
       /proc/sys/vm/dirty_bytes /proc/sys/vm/dirty_ratio
```

If a value you set reads back as `0`, another file zeroed it — see the warning the
role prints, and check for `vm.dirty_*_ratio` lines in other `/etc/sysctl.d/*.conf`
files and in `/etc/sysctl.conf` (applied last by `sysctl --system`).

### The writeback tuning reverts after a reboot

Writing `vm.dirty_ratio` zeroes `vm.dirty_bytes` and vice versa. `sysctl --system`
applies `/etc/sysctl.d/*.conf` in lexical filename order and `/etc/sysctl.conf`
**last**, so any `vm.dirty_*_ratio` line applied after
`99-hammerspace-vm-writeback.conf` silently reverts the cap. Remove the `*_ratio`
lines — the kernel sets the ratio to `0` itself when the `_bytes` form is used.

---

## Hammerspace

### A volume shows **Suspected**

Common causes: the node was rebuilt; the NVMe was reformatted or mounted
somewhere else; the export is missing the `mp` option so an empty directory
was exported.

```bash
exportfs -v                          # export present?
mount | grep hsvol                   # volume mounted?
ls -la /hammerspace/hsvol0/          # Hammerspace's directory structure present?
```

After planned maintenance, re-enable availability-drop with
`set_availability_drop.py --enable` ([OPERATOR_SCRIPTS.md](OPERATOR_SCRIPTS.md#set_availability_droppy--planned-downtime)).

### `Node already exists`

Normal — the playbook skips nodes and volumes that are already registered.

### API call failed / HTTP 4xx during registration

The failure message includes the API response body. Test the API by hand:

```bash
curl -sk -u admin:'<password>' https://<anvil>:8443/mgmt/v1.2/rest/nodes | jq '.[].name'
curl -sk -u admin:'<password>' https://<anvil>:8443/mgmt/v1.2/rest/tasks | jq '.[-5:]'
nc -zv <anvil> 8443
```

### Writes from GPU nodes go to the DSX first

That is the default behaviour with only the standard objectives. Apply the
volume group's `place-on-` objective — see
[HAMMERSPACE_INTEGRATION.md](HAMMERSPACE_INTEGRATION.md#making-new-files-land-on-tier-0).

### Files are not being moved (mobility failures)

- DI / mover nodes must have `no_root_squash` on every export (`mover_nodes`
  or `di_nodes`).
- Ports 9095 and 9096 must be open on the DI nodes.
- Check the mover status in the Hammerspace GUI.

### Azure Key Vault lookup fails

See [DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md](../DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md#when-the-key-vault-lookup-fails).

---

## Data Instantiator

| Symptom | Check |
|---|---|
| `pd-di` service not running | `journalctl -u pd-di` on the node; in container mode `podman logs pd-di` |
| Registration failed | The node can reach the Anvil on 8443; `add_node.py` is in `payload/` |
| RPMs are 100-byte files | `git lfs pull` was not run after cloning |
| DI node not in `/etc/exports` on Tier 0 servers | `di_auto_export: true` and the node is in the `di_nodes` group; re-run `site.yml` |
| Kubernetes pods pending | Node label `hammerspace.io/di=true` present? Tolerations match the GPU taint? |

---

## Useful commands

```bash
# Re-run one part
ansible-playbook site.yml --tags precheck
ansible-playbook site.yml --tags raid
ansible-playbook site.yml --tags nfs
ansible-playbook site.yml --tags nfs-exports      # exports only, no restart
ansible-playbook site.yml --tags perf
ansible-playbook site.yml --tags hammerspace

# Skip a part
ansible-playbook site.yml --skip-tags hammerspace

# Dry run / verbose
ansible-playbook site.yml --check --diff
ansible-playbook site.yml -vvv

# One host
ansible-playbook site.yml --limit tier0-node-01
```

On a Tier 0 server:

```bash
cat /proc/mdstat                                   # RAID health
df -h /hammerspace/hsvol*                          # mounts
exportfs -v                                        # exports
systemctl status nfs-server                        # NFS
systemctl list-units 'hammerspace-*-guard.service' # mount guards
```
