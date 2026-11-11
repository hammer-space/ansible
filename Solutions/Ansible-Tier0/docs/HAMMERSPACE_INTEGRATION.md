# Hammerspace Integration

**Who this is for:** anyone connecting the Tier 0 servers to a Hammerspace
cluster — registering nodes and volumes, creating shares, making sure new
files land on Tier 0, and optional cluster settings.

After the storage is built, the playbook talks to the Anvil REST API and does
the registration for you. Nothing here requires the Hammerspace CLI, but the
equivalent CLI commands are listed at the end if you prefer to do it by hand.

---

## Connect to the cluster

Three values in `vars/main.yml` are required. Everything else is optional.

```yaml
hammerspace_api_host: "10.1.2.3"          # Anvil management IP — set this ONCE
hammerspace_api_user: "admin"             # needs the admin role
hammerspace_api_password: "{{ vault_hammerspace_api_password }}"   # from vars/vault.yml
hammerspace_api_validate_certs: false     # true once the Anvil has a trusted certificate
```

Put the password in `vars/vault.yml` and encrypt it:

```bash
ansible-vault encrypt vars/vault.yml
ansible-playbook site.yml --ask-vault-pass        # or --vault-password-file ~/.vault_pass
```

**Set the Anvil IP once.** `hammerspace_nodes` (NFS export list),
`network_test_targets` (jumbo-frame test) and `hammerspace_cluster_mgmt_ip`
(DI) all default to `hammerspace_api_host`. You only repeat the IP if your
cluster has additional Anvil or DSX nodes:

```yaml
hammerspace_nodes:
  - "{{ hammerspace_api_host }}"
  - "10.1.2.4"        # second Anvil
  - "10.1.2.10"       # DSX
```

**On Azure** you can pull the password from Key Vault instead of the vault
file — see [DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md](../DEPLOYMENT_GUIDE_AZURE_EXISTING_MOUNT.md#the-admin-password).

**The API is tested first.** In the first 30 seconds of every run the playbook
checks that the Anvil is reachable and the credentials work. A wrong password
fails immediately with `HTTP 401`, not two hours later after the RAID build.

---

## What gets registered

| Step | API call | Result |
|---|---|---|
| Add node | `POST /nodes` | The server appears in Hammerspace as an NFS storage system (type `OTHER`) |
| Add volumes | `POST /storage-volumes` | One volume per mount point: `node-name::/hammerspace/hsvol0`, `hsvol1`, … |
| Join volume group (optional) | `PUT /volume-groups/<uuid>` | The new volumes are appended to an existing group |
| Create shares (optional) | `POST /shares` | Shares are created with the export options you list |
| Apply placement (optional) | `objective-set` | `place-on-<group>` is applied so new files land on Tier 0 |

Example of the end-of-run summary:

```
HAMMERSPACE INTEGRATION COMPLETE
============================================
Anvil API: 10.1.2.3
Storage System: AZ1:tier0-node01
Volumes Added: 2
  - AZ1:tier0-node01::/hammerspace/hsvol0
  - AZ1:tier0-node01::/hammerspace/hsvol1

Verify in Hammerspace GUI or CLI:
  anvil> node-list
  anvil> storage-volume-list
============================================
```

Re-running the playbook is safe: nodes and volumes that already exist are
skipped. To register without touching the storage:

```bash
ansible-playbook site.yml --tags hammerspace
```

### Node naming

```yaml
hammerspace_node_name: "{{ inventory_hostname }}"   # default
# hammerspace_node_name: "AZ1:tier0-node01"          # with an availability-zone prefix
```

Availability-zone prefixes on volume names, and how they are detected on each
cloud, are covered in [INVENTORY_SETUP.md](INVENTORY_SETUP.md#availability-zone-prefixes-on-volume-names).

Hammerspace recommends, when data lives only on Tier 0: at least 4 AZs,
ideally 6, with the same number of nodes and volumes in each.

### Volume settings

These map directly to the options in the Hammerspace Tier 0 Deployment Guide:

```yaml
hammerspace_volume_high_threshold: 0.98        # start evacuating data at 98 % full
hammerspace_volume_low_threshold: 0.90         # stop at 90 %
hammerspace_volume_max_suspected_seconds: 0    # how long a volume may be "suspected" before "unavailable"
hammerspace_volume_unavailable_multiplier: 0   # 0 = availability drops when the node is down (re-replicate now)
                                               # 1 = wait for the node to come back (planned maintenance)
hammerspace_volume_availability: 2
hammerspace_volume_durability: 3
hammerspace_skip_performance_test: true        # faster registration
```

| Setting | Hammerspace CLI equivalent |
|---|---|
| `hammerspace_volume_high_threshold` | `--high-threshold` |
| `hammerspace_volume_low_threshold` | `--low-threshold` |
| `hammerspace_volume_max_suspected_seconds` | `--max-suspected-time` |
| `hammerspace_volume_unavailable_multiplier` | `--availability-drop-enabled` (0) / `--availability-drop-disabled` (1) |

To change thresholds on volumes that already exist, use
`set_volume_thresholds.py` — see [OPERATOR_SCRIPTS.md](OPERATOR_SCRIPTS.md).

---

## Volume groups

A volume group is how you tell Hammerspace "these volumes are Tier 0". Create
the group in the Hammerspace GUI first (that is where its placement objectives
are defined), then have every run add its new volumes to it:

```yaml
hammerspace_volume_group: "tier0-nvme"
```

- The group must already exist — a missing group fails the run rather than
  deploying volumes that would never pick up the objectives.
- Existing members are kept; the update only appends.
- Re-running is a no-op once the volumes are members.

### Choosing the group per run, or per AZ

Set it for a single run — command-line values override `vars/main.yml`:

```bash
ansible-playbook site.yml -i inventory.yml -e hammerspace_volume_group=tier0-nvme
```

Or set it differently per host or per inventory group. Each host's volumes go
to the group that host names, and each group is updated once:

```yaml
# group_vars/AZ1.yml
hammerspace_volume_group: "tier0-az1"

# group_vars/AZ2.yml
hammerspace_volume_group: "tier0-az2"
```

The plan is printed before anything is written, so you can confirm which
volumes are going to which group:

```
Volume group join plan (2 group(s)):
  tier0-az1 <- 2 volume(s) from node101, node102
  tier0-az2 <- 2 volume(s) from node201, node202
```

### Creating the group in the same run

By default the group must already exist. To have the playbook create it, set
`hammerspace_create_volume_groups: true`. A group that does not exist is then
created with this run's volumes as its members — useful for one group per
job, set from a scheduler prolog:

```bash
ansible-playbook site.yml -i inventory.azure_rm.yml --limit "$NODE_IP" \
  -e hammerspace_volume_group="tier0-${SLURM_JOB_ID}" \
  -e hammerspace_create_volume_groups=true \
  -e hammerspace_tier0_place_on_group=true
```

- The run reports the group as created only once the cluster lists it. A
  rejected request stops the run with the API's response.
- A group that already exists is used as-is, so re-running is safe.
- A new group carries only the objectives Hammerspace creates for every group
  (`place-on-<group>`, `confine-to-<group>`, `exclude-from-<group>`). With
  `hammerspace_tier0_place_on_group: true` the run waits briefly for
  `place-on-<group>` to appear before applying it to the shares.
- Nothing removes the group or its objective from the shares afterwards.
  Plan for that when creating one group per job: share objectives are
  cluster-wide, so concurrent jobs each add their own `place-on-<group>` to
  the same shares.

You can also create simple groups by name pattern, or add specific volumes:

```yaml
hammerspace_create_volume_groups: true
hammerspace_volume_groups:
  - name: "tier0-az1-volumes"
    location_pattern: "AZ1:*"

hammerspace_volume_group_members:
  - volume_name: "AZ1:tier0-node-01::/hammerspace/hsvol0"
    group_name: "tier0-az1-volumes"
```

---

## Shares

```yaml
hammerspace_create_shares: true
hammerspace_shares:
  - name: checkpoints
    path: /checkpoints
    export_options:
      - subnet: "10.200.104.0/24"
        accessPermissions: "RW"
        rootSquash: true
    objective: "availability-3-nines"     # optional
  - name: models
    path: /models
    export_options:
      - subnet: "*"
        accessPermissions: "RW"
        rootSquash: false
```

Objectives can also be applied separately to shares created elsewhere:

```yaml
hammerspace_apply_share_objectives: true
hammerspace_share_objectives:
  - share_name: "checkpoints"
    objective: "availability-3-nines"
    applicability: "TRUE"
```

Clients mount shares from the Anvil, not from the Tier 0 nodes:

```bash
mount -o vers=4.2,nconnect=8,noatime <anvil_IP>:/checkpoints /mnt/checkpoints
```

---

## Making new files land on Tier 0

Registering volumes and joining a volume group does **not** by itself make
writes go to Tier 0. With only the default objectives, Hammerspace spreads new
files across *every* volume with free space — including DSX-backed ones — so a
write from a GPU node may land on the DSX first and be copied to local NVMe
later. An objective has to point at the Tier 0 group.

Hammerspace automatically creates a `place-on-<group>` objective for every
volume group. One flag applies it to your shares:

```yaml
hammerspace_volume_group: "tier0-nvme"
hammerspace_tier0_place_on_group: true
# hammerspace_tier0_place_on_shares: ["checkpoints"]          # shares created outside this playbook
# hammerspace_tier0_place_on_applicability: "LAST_USE_AGE<1*HOURS?TRUE"
```

A missing objective, share or group is a hard failure — a silent skip would
reproduce exactly the "writes go to the DSX" symptom.

A share objective is cluster-wide and can name only one volume group, so
unlike the volume join this cannot follow a run that uses several (per-AZ
groups, for example). Such a run stops and offers three ways forward: name the
objective explicitly with `hammerspace_tier0_place_on_objective`, apply one
that names a group *containing* the others (a volume group may hold other
volume groups), or run one AZ at a time with `--limit`.

This selects the **tier**, not the node: the Anvil balances new files across
the group's volumes. If you need each GPU node to write to *its own* NVMe
first, that is a per-node `place-on-<node>` objective on per-node
directories — see "Configure Tier 0 Nodes to Write Local" in the Hammerspace
Tier 0 Deployment Guide.

---

## Large clusters — protecting the Anvil

Ten nodes with four volumes each is forty simultaneous API operations. Two
controls keep the Anvil comfortable:

```yaml
hammerspace_serial: 2            # register 2 nodes at a time (0 = all at once)

hammerspace_task_queue_throttle: true
hammerspace_max_queued_tasks: 10 # pause when the Anvil's task queue is this long
hammerspace_min_queued_tasks: 5  # resume when it drops to this
```

`hammerspace_serial` can also be passed on the command line:
`-e hammerspace_serial=2`.

---

## Optional cluster settings

Each of these is off by default and applied once per run:

```yaml
hammerspace_update_dns: true
hammerspace_dns_config:
  primary_server: "10.0.0.53"
  secondary_server: "10.0.0.54"
  search_domains: ["example.com"]

hammerspace_join_ad: true
hammerspace_ad_config:
  domain: "corp.example.com"
  username: "{{ vault_ad_username }}"
  password: "{{ vault_ad_password }}"
  ou: "OU=Servers,DC=corp,DC=example,DC=com"

hammerspace_update_site_name: true
hammerspace_site_config: { name: "datacenter-east" }

hammerspace_set_location: true
hammerspace_location_config:
  datacenter: "DC-EAST"
  room: "Server Room A"
  row: "R1"
  rack: "Rack-01"
  position: "U1"

hammerspace_enable_prometheus: true
hammerspace_prometheus_config: { port: 9100 }
```

### S3 / object storage

Add S3 buckets as Hammerspace volumes, or expose an S3 endpoint:

```yaml
hammerspace_add_s3_nodes: true
hammerspace_s3_nodes:
  - name: "aws-archive"
    region: "us-west-2"
    access_key_id: "{{ vault_s3_access_key }}"
    secret_access_key: "{{ vault_s3_secret_key }}"
    # endpoint: "s3.us-west-2.amazonaws.com"    # for S3-compatible storage

hammerspace_add_object_volumes: true
hammerspace_object_volumes:
  - name: "archive-vol"
    node_name: "aws-archive"
    bucket_name: "my-archive-bucket"
    prefix: "hammerspace/"

hammerspace_create_s3_server: true
hammerspace_s3_server: { name: "internal-s3", port: 9000, tls_enabled: false, enabled: true }

hammerspace_create_s3_users: true
hammerspace_s3_users:
  - name: "app-user"
    access_key_id: "{{ vault_app_s3_key }}"
    secret_access_key: "{{ vault_app_s3_secret }}"
    enabled: true
```

---

## Doing it by hand (Hammerspace CLI)

If you leave `hammerspace_api_host` unset the playbook builds the storage and
NFS only. Register manually from the Anvil CLI:

```bash
# 1. Add the node
anvil> node-add --type OTHER --name AZ1:tier0-node01 --ip 10.200.100.101 --create-placement-objectives

# 2. Add each export as a volume
anvil> volume-add --name AZ1:tier0-node01::/hsvol0 \
  --node-name AZ1:tier0-node01 --access-type read_write \
  --logical-volume-name /hsvol0 --low-threshold 90 --high-threshold 95 \
  --skip-performance-test

# 3. Create a share
anvil> share-create --name checkpoints --path /checkpoints \
  --export-option "10.200.104.0/24,rw,root-squash"
```

---

## Checking the result

```bash
# Hammerspace CLI
anvil> node-list
anvil> volume-list --node-name tier0-node01

# API
curl -sk -u admin:'<password>' https://<anvil>:8443/mgmt/v1.2/rest/nodes | jq '.[].name'
curl -sk -u admin:'<password>' https://<anvil>:8443/mgmt/v1.2/rest/storage-volumes | jq '.[].name'
```

If a volume shows as **Suspected** or an API call fails, see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#hammerspace).
