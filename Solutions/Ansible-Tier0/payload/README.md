# payload/ — RPMs not included in this copy

This copy of Ansible-Tier0 ships without the Hammerspace and dependency
RPMs that normally live here. Get them from your Hammerspace contact and
place them in this folder before running the playbooks that install the
Data Instantiator (DI) or Cloud Mover on hosts (`deploy_di: true`,
`di_deployment_type: "host"`) or build the DI container image
(`build_di_image.yml`).

Expected files (versions as of this copy):

| File | Arch |
|---|---|
| `pd-di-5.1.41-452.el9.x86_64.rpm` | x86_64 |
| `pd-di-5.3.0-4321.el9.aarch64.rpm` | aarch64 |
| `pd-cm-5.1.41-452.7eb4ff34.noarch.rpm` | noarch |
| `hs-s3-5.1.41-452.el9.x86_64.rpm` | x86_64 |
| `libtirpc-1.3.3-10.hs.el9.aarch64.rpm` | aarch64 |
| `richacl-0.0.1-87.x86_64.rpm`, `richacl-devel-0.0.1-87.x86_64.rpm` | x86_64 |
| `babeltrace-1.5.8-10.el9.{x86_64,aarch64}.rpm` | both |
| `jemalloc-5.3.0-6.el9.aarch64.rpm` | aarch64 |
| `lttng-tools-2.12.11-1.el9.aarch64.rpm`, `lttng-ust-2.12.0-6.el9.aarch64.rpm` | aarch64 |

`git lfs pull` (mentioned in the top-level README) fetches nothing in
this copy. `add_node.py` is included.
