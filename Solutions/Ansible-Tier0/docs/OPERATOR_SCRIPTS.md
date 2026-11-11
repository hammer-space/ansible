# Operator Playbooks and Scripts

**Who this is for:** anyone running day-2 operations — adding nodes, taking
nodes down for maintenance, changing volume settings, or removing nodes.

Everything here lives in the repository root. The Python scripts talk only to
the Hammerspace API; they never log in to the storage servers. The
step-by-step maintenance procedures that use these tools are in
[RACK_OPERATIONS_RUNBOOK.md](../RACK_OPERATIONS_RUNBOOK.md).

---

## Credentials for the scripts

Every script takes `--host <ANVIL_IP> --user admin` plus a password. Create a
password file once and use `--password-file`:

```bash
echo 'your-password' > ~/.hs_password && chmod 600 ~/.hs_password
```

| Method | Visible to other users? | Use for |
|---|---|---|
| `--password-file ~/.hs_password` | No | Day-to-day work |
| `HAMMERSPACE_PASSWORD` environment variable | In `/proc` | CI pipelines |
| No flag → interactive prompt | No | One-off commands |
| `--password '...'` | **Yes** — in `ps` and shell history | Avoid |

Every script that changes something supports `--dry-run`. Use it first.

---

## Which tool do I need?

| I want to… | Tool | Logs in to servers? |
|---|---|---|
| Find out which servers are not yet in Hammerspace | `preflight_check.yml` | No |
| Deploy only the new servers | `deploy_new_instances.sh` | Yes |
| Check exports from a client | `verify_nfs.yml` | No |
| Take a node down for maintenance without data re-replication | `set_availability_drop.py` | No |
| Change the fill thresholds on existing volumes | `set_volume_thresholds.py` | No |
| Add existing volumes to a volume group | `add_volumes_to_group.py` | No |
| Assign availability zones from OCI GPU fabric | `collect_gpu_fabric.yml` + `assign_az_to_volumes.py` | Yes / No |
| Rename OCI instances with their AZ | `rename_oci_instances_az.py` | No |
| Remove a node from Hammerspace, keep the data on disk | `decommission_tier0.yml` or `cleanup_instance_nodes.py` | No |
| Wipe a node back to bare metal | `reset-tier0-host.yml` | Yes |
| Remove a DI node | `decommission_di.yml` | Yes |

---

## Deploying new servers

### preflight_check.yml — what is new?

Compares the inventory with the nodes registered in Hammerspace and writes
the list of servers that still need deployment.

```bash
ansible-playbook preflight_check.yml -i inventory.oci.yml
```

```
PREFLIGHT CHECK REPORT
Inventory hosts (storage_servers): 10
Hammerspace registered nodes:      7
New instances to deploy:           3

NEW INSTANCES (need deployment)
- instance-008
- instance-009
- instance-010

RECOMMENDED COMMANDS
ansible-playbook site.yml --limit "instance-008,instance-009,instance-010"
```

| Output file | Contents |
|---|---|
| `.new_instances_limit` | The new host names, ready for `--limit @.new_instances_limit` |
| `preflight_report.txt` | The full report |

### deploy_new_instances.sh — preflight + deploy in one step

```bash
./deploy_new_instances.sh -i inventory.oci.yml            # asks before deploying
./deploy_new_instances.sh -i inventory.oci.yml --check    # dry run
./deploy_new_instances.sh -i inventory.oci.yml --auto     # no prompt (CI)
./deploy_new_instances.sh -i inventory.oci.yml --precheck-only
```

### Targeting specific servers with `--limit`

```bash
ansible-playbook site.yml --limit "instance-008"                   # one host
ansible-playbook site.yml --limit "instance-008,instance-009"      # a list
ansible-playbook site.yml --limit "instance2026*"                  # a pattern
ansible-playbook site.yml --limit "all:!instance-003"              # everything except
ansible-playbook site.yml --limit @.new_instances_limit            # from a file
```

### verify_nfs.yml

Runs `showmount -e` against each storage server from the control machine.
Edit the `storage_servers` list at the top of the file first.

```bash
ansible-playbook verify_nfs.yml
```

---

## Maintenance

### set_availability_drop.py — planned downtime

By default, when a Tier 0 node goes offline Hammerspace assumes it is gone and
starts re-replicating its data elsewhere. Before a planned reboot, firmware
update or drive swap, **disable availability-drop** so Hammerspace waits for
the node instead. Re-enable it afterwards.

```bash
# What is the current setting?
python3 set_availability_drop.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node tier0-node-01 --check

# Before maintenance
python3 set_availability_drop.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node tier0-node-01 --disable --dry-run     # preview
python3 set_availability_drop.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node tier0-node-01 --disable

# After the node is back
python3 set_availability_drop.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node tier0-node-01 --health-check          # node, volumes and recent events
python3 set_availability_drop.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node tier0-node-01 --enable
```

Select nodes with `--node` (repeatable), `--prefix`, `--contains`, `--pattern`
(regex) or `--all-nodes`.

### set_volume_thresholds.py — change fill thresholds

Hammerspace starts moving data off a volume at the *high* threshold and stops
at the *low* one. Change them on existing volumes without redeploying:

```bash
python3 set_volume_thresholds.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --prefix tier0 --check                          # show current values
python3 set_volume_thresholds.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --prefix tier0 --high 0.98 --low 0.96 --dry-run # plan
python3 set_volume_thresholds.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --prefix tier0 --high 0.98 --low 0.96           # apply
```

Values accept `0.96`, `96` or `96%`. Node selection works as above, plus
`--instances-file` (one name per line).

### add_volumes_to_group.py — volume group membership

```bash
python3 add_volumes_to_group.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --group "tier0-az1-volumes" --list                                        # who is in it now
python3 add_volumes_to_group.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --group "tier0-az1-volumes" --instances-file tier0_instances_limit --dry-run
python3 add_volumes_to_group.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --group "tier0-az1-volumes" --instances-file tier0_instances_limit --az AZ1 --yes
```

`--az AZ1` limits the change to volumes with that prefix.

---

## Availability zones on OCI GPU clusters

On OCI, instances that share a **GPU memory fabric** are one failure domain.
These two tools map each fabric to an AZ number and rename the volumes
accordingly. The full procedure, including replacing a fabric, is in
[DEPLOYMENT_GUIDE.md § 9](../DEPLOYMENT_GUIDE.md#9-availability-zone-az-configuration-with-gpu-fabric).

```bash
# 1. Collect the fabric ID of every instance -> gpu_fabric_data.txt
ansible-playbook collect_gpu_fabric.yml -i inventory.oci.yml

# 2. Assign AZ prefixes to the volumes
python3 assign_az_to_volumes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --gpu-fabric-file gpu_fabric_data.txt --dry-run
python3 assign_az_to_volumes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --gpu-fabric-file gpu_fabric_data.txt

# Force a fabric to a particular AZ (e.g. a replacement fabric keeps the old number)
python3 assign_az_to_volumes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --gpu-fabric-file gpu_fabric_data.txt --az-map "ocid1.computegpumemoryfabric...=AZ3"

# Report only (CSV)
python3 assign_az_to_volumes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --gpu-fabric-file gpu_fabric_data.txt --report-only
```

### rename_oci_instances_az.py

Prefixes OCI instance display names with their AZ
(`instance20260303192653` → `AZ3_instance20260303192653`), reading the AZ
from the Hammerspace volume names.

```bash
python3 rename_oci_instances_az.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --compartment-id <OCID> --name-pattern "^instance2026" --dry-run
python3 rename_oci_instances_az.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --compartment-id <OCID> --name-pattern "^instance2026" --skip-existing --yes
```

`--instances-file` selects by list instead of (or as well as) a pattern.

### generate_instance_report.yml

Writes `instance_report.csv` with each host's fault domain and AZ.

```bash
ansible-playbook generate_instance_report.yml -i inventory.oci.yml
column -s, -t instance_report.csv
```

---

## Removing nodes

Three tools, three levels of teardown. **Always remove a node from
Hammerspace before you terminate or wipe it** — otherwise the cluster keeps
trying to reach it.

| Tool | Removes from Hammerspace | Touches the server | Data on disk |
|---|---|---|---|
| `decommission_tier0.yml` | Yes | No | Untouched |
| `cleanup_instance_nodes.py` | Yes | No | Untouched |
| `reset-tier0-host.yml` | Yes (then wipes) | Yes | **Destroyed** |

### decommission_tier0.yml — unregister, keep the data

Deletes the node's volumes and the node from Hammerspace, then confirms the
volumes left their volume groups. Nothing on the server changes: NFS keeps
running, filesystems stay mounted. Use this for cloud instances you are about
to terminate, and for existing-mount deployments where the array belongs to
the cloud provider.

```bash
ansible-playbook decommission_tier0.yml -i inventory.yml --limit 'tier0-node-0[1-3]' --check
ansible-playbook decommission_tier0.yml -i inventory.yml --limit 'tier0-node-0[1-3]' -e decommission_confirm=true
```

Without `-e decommission_confirm=true` the playbook prints its plan and stops.

### cleanup_instance_nodes.py — the same, for servers that are already gone

Works from the control machine with only API access — the servers do not
need to exist any more. Volumes are deleted one at a time (or `--parallel N`
at a time), each waited on until Hammerspace confirms it is gone; a node is
only deleted after all its volumes are.

```bash
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password --list-nodes
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --contains "bu-test" --dry-run
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin --password-file ~/.hs_password \
  --node bu-test-01 --node bu-test-02 --parallel 5
```

| Option | Meaning |
|---|---|
| `--list-nodes` | Show every node and exit |
| `--node NAME` / `--prefix` / `--contains` / `--pattern` | Select nodes |
| `--parallel N` | Delete N volumes at a time (default 1) |
| `--dry-run` | Show the plan, change nothing |
| `--yes` | Skip the confirmation prompt |

### reset-tier0-host.yml — back to bare metal

Reverses everything `site.yml` did: unregisters from Hammerspace, removes the
mount guards, stops NFS, unmounts, destroys the RAID arrays and wipes the
drive signatures. **All data on the Tier 0 volumes is destroyed.**

```bash
ansible-playbook reset-tier0-host.yml -i inventory.yml --limit tier0-node-01 -e reset_confirm=true

# Also discard every block on the NVMe drives (fastest re-provision)
ansible-playbook reset-tier0-host.yml -i inventory.yml --limit tier0-node-01 \
  -e reset_confirm=true -e reset_run_blkdiscard=true

# Also delete the /hammerspace directories
ansible-playbook reset-tier0-host.yml -i inventory.yml --limit tier0-node-01 \
  -e reset_confirm=true -e reset_remove_mount_dirs=true

# The Anvil is unreachable — skip the Hammerspace step
ansible-playbook reset-tier0-host.yml -i inventory.yml --limit tier0-node-01 \
  -e reset_confirm=true -e reset_hammerspace_cleanup=false
```

| Phase | Action |
|---|---|
| 1 | Remove node and volumes from Hammerspace |
| 2 | Stop the mount guards and remount watchdog |
| 3 | Stop NFS |
| 4 | Unexport, unmount, remove fstab entries |
| 5 | Stop the arrays, zero superblocks, wipe signatures, optional `blkdiscard` |
| 6 | Remove systemd units and `mdadm.conf` entries |
| 7 | Remove mount directories (optional), verify the server is clean |

Do **not** use this on an existing-mount deployment (`existing_mount_paths`)
— the array there is not the playbook's to destroy.

### decommission_di.yml

See [DATA_INSTANTIATOR.md](DATA_INSTANTIATOR.md#decommission-a-di-node).
