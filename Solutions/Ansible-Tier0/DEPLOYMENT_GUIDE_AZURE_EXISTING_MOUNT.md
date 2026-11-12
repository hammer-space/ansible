# Tier 0 on Existing NVMe — Azure Runbook

Register Azure nodes whose local NVMe array is **already built and mounted**, and
unregister them again without touching the data.

**Who this is for:** operators of CycleCloud (or similar) clusters where the
VM image already assembles and mounts the local NVMe, typically at `/nvme`.
**Time:** 15 minutes for the first node once the inventory works.

Written for CycleCloud HB/L-series nodes where Azure has already assembled
`/dev/md0` and mounted it at `/nvme`. In this mode the playbook never runs
discovery, `mdadm` or `mkfs` — it creates one export directory inside the
existing filesystem and wires NFS and Hammerspace around it.

For the standard build-the-array-from-raw-disks flow, see
[DEPLOYMENT_GUIDE_AZURE.md](DEPLOYMENT_GUIDE_AZURE.md).

---

## 00 — Before you start

Three things must be true. The playbook checks all of them and stops with an
explanation if any is missing.

| Requirement | Check |
|---|---|
| `/nvme` is a real mountpoint | `findmnt -n -o TARGET,SOURCE /nvme` → `/nvme  /dev/md0` |
| The scheduler can reach the Anvil | `curl -sk -o /dev/null -w '%{http_code}\n' -u admin:… https://<anvil>:8443/mgmt/v1.2/rest/cntl` → `200` |
| Repo is current | `git -C ~/Ansible-Tier0 log --oneline -1` |

If `/nvme` is not a mountpoint the run is **refused by design** — exporting a
plain directory would hand clients space on the OS disk instead of the array.

---

## 01 — Configure

Everything lives in `vars/main.yml`.

### Point Tier 0 at the mounted array

```yaml
existing_mount_paths:
  - /nvme
existing_mount_subdir: tier0        # exports /nvme/tier0
```

That is the whole storage configuration. `storage_type`, `use_raid`,
`use_dynamic_discovery`, `mount_base_path` and the RAID sizing variables are
**not read** in this mode — setting `existing_mount_paths` gates off
`nvme_discovery`, `raid_setup` and `filesystem_setup` on its own.

> **Keep `hw_raid_devices` and `raid_arrays` commented out.** Either one active
> alongside `existing_mount_paths` configures two storage modes at once.

### Add the volumes to an existing volume group

```yaml
hammerspace_volume_group: "your-group-name"
```

Append-only:

- the group is **never created** — if it does not exist the run fails, rather
  than leaving volumes without your placement objectives
- volumes already in the group are preserved untouched
- a re-run adds nothing
- membership is re-read after the write to confirm nothing was lost

The update runs **once per play**, not once per host. `hammerspace_integration`
runs on every `storage_server` in parallel, and a volume-group update is a
read-modify-write over the whole membership list — per-host writes would race
and silently drop volumes another host just added.

### The admin password

Either put it in `vars/vault.yml`:

```yaml
vault_hammerspace_api_password: "<real password>"
```

…or pull it from Azure Key Vault and leave no password on the scheduler:

```yaml
hammerspace_keyvault_uri: "https://my-vault.vault.azure.net"
hammerspace_keyvault_secret_name: "hammerspace-admin-password"
hammerspace_keyvault_auth_source: "msi"   # auto | cli | credential_file | env | msi
```

The identity needs **Key Vault Secrets User** on the vault. The lookup runs on
the scheduler only (`delegate_to: localhost`, `run_once`), so target nodes need
no vault access.

> **Security note.** `vars/vault.yml` is tracked by git and is **not** in
> `.gitignore`. A real password there is one `git add` away from being
> committed. Encrypt it with `ansible-vault encrypt vars/vault.yml`, or use the
> Key Vault path above and leave the file as the shipped template.

#### When the Key Vault lookup fails

The lookup itself never prints anything — its result would contain the
plaintext admin password, and every run is written to a log file — so the
Azure error is reported by the task that follows it. If all you see is
`censored: 'the output has been hidden due to the fact that no_log: true was
specified'`, you are running an old version of the playbook: update and re-run,
and the real error will be printed.

Fastest way to split authentication from authorization — run it on the
**scheduler**, which is where the lookup executes:

```bash
az keyvault secret show --vault-name <vault> --name hammerspace-admin-password --query value -o tsv
```

| Symptom | Cause |
|---|---|
| `AADSTS` / no token / `DefaultAzureCredential failed` | `hammerspace_keyvault_auth_source` is wrong — use `msi` on a CycleCloud scheduler, `cli` under `az login` |
| `(Forbidden)` / 403 | auth worked; the identity lacks **Key Vault Secrets User** on the vault |
| `SecretNotFound` / empty result | wrong secret name (case-sensitive) |
| connection timeout | vault is behind a private endpoint the scheduler cannot reach |

Confirm an identity is actually attached to the scheduler:

```bash
curl -sS -H Metadata:true --max-time 5 \
  'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net'
```

---

## 02 — Choose the nodes

Inventory names are **Azure resource names**, not the hostnames you see on the
node.

```bash
cd ~/Ansible-Tier0
ansible-inventory -i inventory.azure_rm.yml --graph
```

A VMSS instance appears as `<scale-set>_<instance-id>`. On this cluster that is
something like `hb120v3-e6prmc26mzh3j_0`, while the OS hostname is
`43t6k000000` and Slurm calls it `<cluster>-hb120v3-2`. **Only the first works with
`--limit`.**

Use the prefix, not the full name — the random suffix changes every time
CycleCloud recreates the scale set, so a hardcoded name silently matches
nothing later:

```bash
--limit 'hb120v3*'
```

Confirm the selection resolves before committing to a run. `--list-hosts` never
connects to anything:

```bash
ansible -i inventory.azure_rm.yml --limit 'hb120v3*' --list-hosts storage_servers
```

An empty result means the hosts are not in `storage_servers` — check the
`conditional_groups` expression, not your `--limit`.

### If your automation only has IP addresses

`--limit` matches `inventory_hostname` and **group names only**, never
`ansible_host` — so `--limit 10.0.16.5` does not work as-is. To make the
inventory name hosts by IP, uncomment in `inventory.azure.yml`:

```yaml
hostnames:
  - private_ipv4_addresses | first
  - default
```

Entries are **Jinja2 expressions**, not keywords, so `| first` is required — a
bare `private_ipv4_addresses` yields the whole list as the name. Verify with
`ansible-inventory --graph --flush-cache` (the plugin caches).

> Not validated against a live Azure subscription. The simpler alternative that
> needs no inventory change is the name wildcard above.

---

## 03 — Deploy

```bash
cd ~/Ansible-Tier0
./scripts/run.sh playbook site.yml \
  -i inventory.azure_rm.yml \
  --limit 'hb120v3*'
```

`scripts/run.sh` pins the locale and sets a log path under `~/logs`.

| Role | In this mode |
|---|---|
| `nvme_discovery` | skipped |
| `raid_setup` | skipped |
| `filesystem_setup` | **skipped** — this is the one that runs `mkfs` |
| `nfs_setup` | runs |
| `hammerspace_integration` | runs |
| `perf_tuning` | runs |

> **On `--check`.** A dry run confirms the play parses and the hosts are
> reachable, but it will not create `/nvme/tier0` or write the export, so it
> cannot tell you the deployment is correct. Use it to validate targeting, then
> run for real.

---

## 04 — Verify

```bash
findmnt -n -o TARGET,SOURCE /nvme    # array untouched and still mounted
ls -ld /nvme/tier0                   # export directory created
showmount -e localhost               # export is live
grep /nvme/tier0 /etc/exports        # export options
```

The export line reads something like:

```
/nvme/tier0 10.0.10.15(rw,no_root_squash,sync,secure,no_subtree_check,mountpoint=/nvme)
```

### Why `mountpoint=` and not `mp`

`mp` means *"only export this path if it is itself a mountpoint"*. An export
**subdirectory** is not one, so leaving `mp` in place produces **no export at
all** — `/etc/exports` looks correct and `showmount` comes back empty.

It is automatically swapped for `mountpoint=/nvme`, which keeps the guarantee:
if the array ever fails to mount, the export disappears rather than serving an
empty directory on the OS disk. When `existing_mount_subdir` is `''` the export
path *is* the mountpoint and plain `mp` is kept.

Finally, confirm in Hammerspace that the volume exists and is in your volume
group.

---

## 05 — Decommission

Removes the node and its volumes from Hammerspace. The array, the export
directory and every file on it are left exactly as they are.

Preview first — changes nothing, prints the full plan:

```bash
ansible-playbook decommission_tier0.yml \
  -i inventory.azure_rm.yml \
  --limit 'hb120v3*' --check
```

Then execute. The confirmation flag is mandatory:

```bash
ansible-playbook decommission_tier0.yml \
  -i inventory.azure_rm.yml \
  --limit 'hb120v3*' \
  -e decommission_confirm=true
```

Order: **delete the volumes → delete the node → verify the volume groups**.
Deleting a volume removes it from its volume groups; the playbook then
re-reads the groups and warns if any still references a deleted volume.

### Node, volumes and volume group in one command

A per-job group (`tier0-<job>_<timestamp>`, created by the prolog with
`hammerspace_create_volume_groups=true`) is not deleted by default. Add
`decommission_delete_volume_groups=true` to delete the groups this run empties:

```bash
ansible-playbook decommission_tier0.yml \
  -i inventory.azure_rm.yml \
  --limit '<node-ip>' \
  -e hammerspace_api_host=<same Anvil as the prolog> \
  -e decommission_confirm=true \
  -e decommission_delete_volume_groups=true \
  -e decommission_volume_group_match='^tier0-<job>_'
```

A group is deleted only when it held this node's volumes and nothing else.
A group shared with other nodes still in use is kept and reported. The
`--check` preview lists the groups that would be deleted.

### Targeting is identical to deployment

`decommission_tier0.yml` takes the same `--limit` as `site.yml` — the same
wildcard, or IPs if the inventory is IP-named per step 02.

Targets are then resolved against Hammerspace by **registered node name OR
registered management IP** (nodes are registered with `mgmtIpAddress`), and
everything downstream uses the name Hammerspace actually knows.

That second path matters on CycleCloud: VMSS instance names change on every
scale-set recreation, so a node deployed as `hb120v3-abc_0` may present a
different inventory name later. Matching on the IP as well means decommission
still finds it.

If a selected host matches **neither** name nor IP, the run **stops** and lists
what it searched for. It does not quietly delete nothing — `--limit` narrowing
to zero registered nodes would otherwise look like success.

### What survives

The playbook never connects to the storage nodes at all — every task is
delegated to the controller and talks only to the Anvil API. Untouched:

- every filesystem and mount on every node
- `/nvme/tier0` and all of its contents
- RAID arrays, `/etc/fstab`, NFS exports
- the Azure VMs / VMSS instances
- all volume groups, including ones this empties

A useful side effect: decommission works even for a node that is already
powered off or deleted in Azure.

> **Do not confuse these playbooks.** `reset-tier0-host.yml` unmounts
> filesystems, destroys RAID arrays and wipes drive superblocks. **Never run it
> on this deployment** — the array at `/nvme` belongs to Azure, and that
> playbook would destroy it.

To bring a node back afterwards, re-run `site.yml`.

---

## 06 — If it fails

| Message | Cause and fix |
|---|---|
| `--limit leaves us with no hosts to target` | The name does not exist in the inventory. Run `ansible-inventory --graph` and use the prefix wildcard. Also check you are running from **inside** `~/Ansible-Tier0` — outside it, neither the inventory path nor `ansible.cfg` resolves. |
| `Discovery produced 0 RAID arrays` | Discovery ran, which means `existing_mount_paths` is not set. The message prints what it found on each path and names the most likely cause. |
| `... is not a mountpoint` | `/nvme` is not mounted. Mount the array (and add it to `/etc/fstab`) before deploying. |
| `'vault_hammerspace_api_password' is undefined` | The key is still commented out in `vars/vault.yml`. The error names `vars/main.yml`, but that file only holds the reference — it is not the problem. |
| `HTTP 401` at the API preflight | Wrong password, or still the `PASSWORD` placeholder. Test the credential with the `curl` in step 00. |
| `Volume group '…' does not exist` | Deliberate hard stop. Create the group first, or clear `hammerspace_volume_group` to skip membership. |

Every run is logged under `~/logs/` by `scripts/run.sh`. Send that file along
with the failing task when raising an issue.
