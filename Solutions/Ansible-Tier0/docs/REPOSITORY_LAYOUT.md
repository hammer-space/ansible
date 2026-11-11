# Repository Layout

**Who this is for:** anyone who wants to know what the files in this
repository are for before editing one.

You will normally only touch three things: an inventory file, `vars/main.yml`
and `vars/vault.yml`.

```
Ansible-Tier0/
│
│  ── Files you edit ──────────────────────────────────────────────────────
├── vars/
│   ├── main.yml               # All settings for your site (Anvil IP, RAID, NFS, …)
│   └── vault.yml              # Passwords — encrypt with ansible-vault
├── inventory.yml              # Static list of servers
├── inventory.oci.yml          # …or auto-discover from OCI
├── inventory.aws.yml          # …or AWS
├── inventory.gcp.yml          # …or GCP
├── inventory.azure.yml        # …or Azure
├── inventory.az.yml           # Static list pre-grouped by availability zone (NOT Azure)
├── ansible.cfg                # Which inventory to use by default, SSH options
│
│  ── Playbooks you run ─────────────────────────────────────────────────
├── site.yml                   # THE deployment: storage + NFS + Hammerspace (+ DI)
├── site-onprem-nvme.yml       # site.yml with on-prem defaults (no firewall changes)
├── preflight_check.yml        # Which inventory hosts are not yet in Hammerspace?
├── deploy_new_instances.sh    # preflight_check + site.yml on the new hosts
├── verify_nfs.yml             # showmount against every server
├── decommission_tier0.yml     # Unregister nodes from Hammerspace, keep data
├── reset-tier0-host.yml       # Wipe a node back to bare metal
├── decommission_di.yml        # Remove a DI node
├── build_di_image.yml         # Build the DI container image once, ship the tar
├── collect_gpu_fabric.yml     # OCI: record each instance's GPU fabric
├── generate_instance_report.yml  # OCI: CSV of fault domain / AZ per host
│
│  ── Operator scripts (Hammerspace API only, no SSH) ───────────────────
├── cleanup_instance_nodes.py  # Remove nodes + volumes from Hammerspace
├── set_availability_drop.py   # Prepare nodes for planned maintenance
├── set_volume_thresholds.py   # Change fill thresholds on existing volumes
├── add_volumes_to_group.py    # Volume-group membership
├── assign_az_to_volumes.py    # OCI: AZ prefix from GPU fabric
├── rename_oci_instances_az.py # OCI: put the AZ in the instance name
├── oci_deploy.py              # OCI: deploy via Run Command, no SSH
│
│  ── Helpers ───────────────────────────────────────────────────────────
├── scripts/run.sh             # Wrapper: fixes the locale, logs every run to ~/logs
├── requirements.yml           # Ansible collections (ansible-galaxy install -r)
├── requirements.txt           # Python packages for the scripts
├── payload/                   # DI RPMs + add_node.py (Git LFS — run git lfs pull)
├── container/Containerfile    # DI container image
├── cloud-init/                # Bootstrap script + cloud-config for self-provisioning
├── oci-function/              # OCI Events + Functions for hands-off provisioning
│
│  ── The automation itself (no changes needed) ─────────────────────────
├── plays/                     # The plays site.yml is made of
│   ├── storage.yml            #   RAID, filesystem, NFS, perf, firewall, Hammerspace
│   ├── hammerspace_serial.yml #   Throttled registration for large clusters
│   ├── di_exports.yml         #   Add DI IPs to the NFS exports
│   └── di.yml                 #   Deploy DI
├── roles/
│   ├── nvme_discovery/        #   Find drives, protect the OS disk, plan arrays
│   ├── precheck/              #   Validate drives, network, packages
│   ├── raid_setup/            #   mdadm arrays + mdadm.conf
│   ├── filesystem_setup/      #   XFS, fstab, mount protection
│   ├── nfs_setup/             #   nfs.conf + exports
│   ├── perf_tuning/           #   Hammerspace performance settings
│   ├── firewall_setup/        #   firewalld / UFW / iptables
│   ├── hammerspace_integration/  # Anvil API: nodes, volumes, groups, shares, S3, cluster
│   └── di/                    #   Data Instantiator (host / container / kubernetes)
├── tests/                     # Self-contained regression tests (run on a laptop)
│   └── run_all.sh
│
│  ── Documentation ─────────────────────────────────────────────────────
├── README.md                  # Start here
├── DEPLOYMENT_GUIDE*.md       # Step-by-step per platform
├── CLEANUP_PLAN.md            # Production readiness checklist
├── RACK_OPERATIONS_RUNBOOK.md # Maintenance procedures
├── AUTOMATION_GUIDE.md        # OCI event-driven provisioning
├── VARIABLE_REFERENCE.md      # Every setting
└── docs/                      # Topic pages (this directory)
```

## How `site.yml` runs

`site.yml` is four plays in a row. Each runs only when it applies:

| Play | Hosts | Runs when |
|---|---|---|
| `plays/storage.yml` | `storage_servers` | Always |
| `plays/hammerspace_serial.yml` | `storage_servers`, N at a time | `hammerspace_serial > 0` |
| `plays/di_exports.yml` | `storage_servers` | The `di_nodes` group is not empty |
| `plays/di.yml` | `di_nodes` | `deploy_di: true` |

Within `plays/storage.yml` the roles run in the order shown in
[STORAGE_CONFIGURATION.md](STORAGE_CONFIGURATION.md#what-happens-on-a-server-in-order),
and each has a tag so it can be run alone.

## Files the tools create

These are generated locally and ignored by git:

| File | Created by |
|---|---|
| `.new_instances_limit`, `preflight_report.txt` | `preflight_check.yml` |
| `gpu_fabric_data.txt` | `collect_gpu_fabric.yml` |
| `instance_report.csv` | `generate_instance_report.yml`, `plays/storage.yml` |
| `tier0_instances_limit` | You — a list of host names for the `--instances-file` options |
| `~/logs/ansible-*.log` | `scripts/run.sh` |
