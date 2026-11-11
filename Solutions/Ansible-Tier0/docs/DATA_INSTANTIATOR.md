# Data Instantiator (DI / NFS Mover) Deployment

**Who this is for:** anyone deploying Hammerspace *mover* nodes alongside
Tier 0. Skip this page if your cluster already has DSX nodes handling data
movement.

The Data Instantiator (DI) is the Hammerspace component that copies file
instances between storage volumes over NFS. DI nodes are usually separate
servers, but they can share a Tier 0 node. Either way they connect to the same
Hammerspace cluster.

**What you need**

| Requirement | Details |
|---|---|
| DI servers | RHEL / Rocky 9 (host mode) or any Linux with podman/docker (container mode) |
| pd-di packages | The `el9` RPM bundle and `add_node.py` from Hammerspace |
| Network | DI → Anvil on port 8443; Anvil → DI on ports 9095 and 9096 |
| Credentials | The same Hammerspace API credentials as Tier 0 |

---

## 1. Add DI nodes to the inventory

Put them in the `di_nodes` group. Nest them under `AZ1`, `AZ2`, … if you want
them spread across availability zones (the precheck warns if they are not).

```yaml
all:
  children:
    storage_servers:
      hosts:
        node101: { ansible_host: 10.200.101.216 }

    di_nodes:
      children:
        AZ1:
          hosts:
            mover101:
              ansible_host: 10.0.12.100
              di_node_ip: 10.0.12.100
              di_node_name: mover101
        AZ2:
          hosts:
            mover201:
              ansible_host: 10.0.12.200
              di_node_ip: 10.0.12.200
              di_node_name: mover201
      vars:
        ansible_user: root
```

To run DI on a Tier 0 node, list the same host in both groups.

**NFS exports are wired automatically.** Every DI node IP is added to the
Tier 0 servers' `/etc/exports` with `no_root_squash` on every run (`di_auto_export:
true`). You do not need to edit `mover_nodes`.

## 2. Choose a deployment mode

```yaml
deploy_di: true                     # master switch (or pass -e deploy_di=true)
di_deployment_type: "host"          # host | container | kubernetes
```

| Mode | What it does | Choose it when |
|---|---|---|
| `host` | Installs the pd-di RPM and its dependencies on the OS | DI servers run RHEL / Rocky 9 |
| `container` | Builds a Rocky 9 image with pd-di and runs it with podman or docker | The host OS is not el9, or you want an immutable unit |
| `kubernetes` | Runs pd-di as a DaemonSet on an existing containerd-based cluster | Your GPU nodes are already Kubernetes workers |

Set `di_deployment_type` explicitly in `vars/main.yml` — do not rely on the
shipped value.

## 3. Provide the packages

```yaml
di_rpm_source: "directory"          # directory (default) | url | local
```

| Source | Where the RPMs come from |
|---|---|
| `directory` | Loose RPMs in `payload/` on the control machine — best for air-gapped sites |
| `url` | Downloaded from `di_tarball_url` |
| `local` | A tarball on the control machine at `di_tarball_local_path` |

x86_64 and aarch64 RPMs can sit side by side in `payload/`; the playbook
picks the right architecture for each node:

```
payload/
  pd-di-5.3.0-4321.el9.x86_64.rpm
  jemalloc-5.3.0-6.el9.x86_64.rpm
  lttng-tools-2.12.11-1.el9.x86_64.rpm
  lttng-ust-2.12.0-6.el9.x86_64.rpm
  libtirpc-1.3.3-10.hs.el9.x86_64.rpm
  babeltrace-1.5.8-10.el9.x86_64.rpm
  pd-di-5.3.0-4321.el9.aarch64.rpm      # only for ARM nodes
  ...
  add_node.py
```

> `payload/` is stored with Git LFS. After cloning run `git lfs pull`, or the
> RPMs will be tiny pointer files and the install will fail.

### Container images — build once, deploy many

Building the image on every GPU node is slow. Build it once per
architecture and ship the tar:

```bash
ansible-playbook build_di_image.yml -i inventory.yml --limit build-host-x86
ansible-playbook build_di_image.yml -i inventory.yml --limit build-host-arm
# -> payload/hammerspace-di-x86_64.tar, payload/hammerspace-di-aarch64.tar
```

```yaml
di_image_source: "local"
di_image_local_path: "{{ repo_root }}/payload/hammerspace-di-{{ ansible_architecture }}.tar"
```

| `di_image_source` | Speed | Use |
|---|---|---|
| `build` (default) | Slow — builds on each node | First deployment, one node |
| `local` | Fast — copy + load | Multi-node rollout, air-gapped |
| `url` | Fast — download + load | Central image hosting |
| `registry` | Fast — pull | You have a container registry |

## 4. Deploy

```bash
# Tier 0 and DI in one run
ansible-playbook site.yml -i inventory.yml -e deploy_di=true

# DI only
ansible-playbook site.yml -i inventory.yml --tags di -e deploy_di=true

# One DI node
ansible-playbook site.yml -i inventory.yml --tags di --limit mover101 -e deploy_di=true

# Container mode, pre-built image
ansible-playbook site.yml -i inventory.yml -e deploy_di=true \
  -e di_deployment_type=container -e di_image_source=local

# Dry run
ansible-playbook site.yml -i inventory.yml --tags di -e deploy_di=true --check
```

What the role does, in order: precheck (OS, architecture, network) →
dependencies → install → SELinux (RedHat, disabled) → firewall (9095/9096) →
services → register with Hammerspace via `add_node.py`. In container mode
the install and service steps become "install runtime → build/load image →
run container".

## 5. Verify

```bash
ssh mover101 "systemctl status pd-di"          # host mode
ssh mover101 "podman ps"                        # container mode
anvil> node-list --name mover101                # registered in Hammerspace
```

---

## Install now, activate later

Pre-stage DI on nodes without starting the service or registering — useful
when you want movers ready for a future decommission:

```bash
# Install only
ansible-playbook site.yml --tags di -e deploy_di=true -e di_activate=false

# Activate later, one node at a time
ansible-playbook site.yml --tags di-activate -e deploy_di=true -e di_activate=true --limit mover101
```

## Availability-zone spread

```yaml
di_min_az_count: 2                  # warn if DI nodes span fewer AZs
di_enforce_az_distribution: false   # true = fail instead of warn
```

AZ is read from the inventory group name (`AZ1`, `AZ2`, …) or set per host
with `di_node_az`.

## Decommission a DI node

```bash
ansible-playbook decommission_di.yml -i inventory.yml --limit mover101 --check   # preview
ansible-playbook decommission_di.yml -i inventory.yml --limit mover101           # one node at a time
```

The playbook evacuates the node's volumes (`di_decommission_evacuate_data`),
waits for each evacuation to finish, deletes the volumes from Hammerspace,
confirms each one is gone, then removes the node and stops the pd-di service
(`di_decommission_stop_services`).

If Hammerspace rejects any step, the run stops there and the node stays
registered, so a volume is never deleted before its data has left it.

`--check` sends nothing to Hammerspace. It looks up the node's volumes and
reports what it would decommission and remove.

---

## Kubernetes mode

For sites where the GPU nodes are workers in an existing Kubernetes cluster
with containerd. pd-di runs as a privileged DaemonSet with host networking.

**Your cluster must provide**

- Kubernetes ≥ 1.24, containerd as the CRI, `SystemdCgroup = true` in
  `/etc/containerd/config.toml`.
- Node names that match the `di_nodes` inventory hostnames (or set
  `di_k8s_target_nodes`).
- A registry the cluster can pull from (defaults assume OCIR).
- A kubeconfig on the control machine that can create namespaces, label nodes,
  and create Secrets and DaemonSets.
- One build host per architecture in a `di_k8s_build_hosts` inventory group,
  with `podman`, `docker`, `buildah` or `nerdctl` installed.

**Configure**

```yaml
# vars/main.yml
deploy_di: true
di_deployment_type: "kubernetes"
di_k8s_registry_namespace: "<your-oci-tenancy-namespace>"
di_k8s_tolerations:
  - { key: "nvidia.com/gpu", operator: "Exists", effect: "NoSchedule" }
di_k8s_priority_class: "system-node-critical"

# vars/vault.yml
di_k8s_registry_username: "<tenancy>/oracleidentitycloudservice/<user>"
di_k8s_registry_password: "<oci-auth-token>"
```

**Deploy** — one command builds, pushes, labels the nodes, creates the
namespace and secrets, applies the DaemonSet, registers with Hammerspace and
writes a status label on each node:

```bash
ansible-playbook site.yml -i inventory.yml -e deploy_di=true
```

**Verify**

```bash
kubectl get nodes -L hammerspace.io/di,hammerspace.io/di-status
kubectl -n hammerspace-di get pods -o wide
# pd-di logs (the image runs systemd, so use journalctl inside the pod)
kubectl -n hammerspace-di exec ds/hammerspace-di -- journalctl -u pd-di -n 100
```

Refresh the status labels later without redeploying:

```bash
ansible-playbook plays/di.yml -e deploy_di=true --tags di-k8s-status
```

**Decommission** removes the node labels (which evicts the pods), deletes the
DaemonSet and Secrets, then deregisters from Hammerspace. Add
`-e di_k8s_decommission_delete_namespace=true` to remove the namespace too:

```bash
ansible-playbook decommission_di.yml -i inventory.yml -e deploy_di=true
```

The full list of `di_k8s_*` settings is in
[VARIABLE_REFERENCE.md](../VARIABLE_REFERENCE.md#di-kubernetes-mode-di_deployment_type-kubernetes).

---

## All DI settings at a glance

```yaml
deploy_di: false                      # master switch
di_activate: true                     # false = install only
di_auto_export: true                  # add DI IPs to Tier 0 NFS exports
di_deployment_type: "host"            # host | container | kubernetes
di_image_source: "build"              # container mode: build | local | url | registry
di_rpm_source: "directory"            # directory | url | local
di_payload_local_dir: "{{ repo_root }}/payload"
hammerspace_cluster_mgmt_ip: "{{ hammerspace_api_host }}"
hammerspace_cluster_hostname: "data-cluster"
di_node_netmask_prefix: 24
di_min_az_count: 2
di_enforce_az_distribution: false
di_decommission_evacuate_data: true
di_decommission_stop_services: true
```

Everything, with defaults, is in [VARIABLE_REFERENCE.md](../VARIABLE_REFERENCE.md#data-instantiator-di-deployment).
