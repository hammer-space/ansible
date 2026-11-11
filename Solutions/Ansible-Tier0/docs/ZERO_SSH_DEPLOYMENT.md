# Zero-SSH Deployment on OCI

**Who this is for:** OCI customers who do not want to manage SSH keys or a
bastion for their GPU fleet.

Instead of SSH, `oci_deploy.py` uses **OCI Run Command**: the Oracle Cloud
Agent already running on every instance receives a bootstrap script through
the OCI control plane, installs Ansible locally, clones this repository and
runs `site.yml` against itself. Your workstation only needs OCI API access.

```
Your workstation            OCI control plane            GPU instance
oci_deploy.py ── OCI API ──► Run Command service ──► Cloud Agent → bootstrap.sh → ansible (local)
```

For fully hands-off provisioning — every new instance deploys itself the
moment it starts — see [AUTOMATION_GUIDE.md](../AUTOMATION_GUIDE.md), which
adds an OCI Event and Function in front of the same mechanism.

---

## Before you start

| Requirement | Details |
|---|---|
| OCI CLI config | `~/.oci/config` with a user that can list instances and run commands |
| Python SDK | `pip3 install oci` |
| Cloud Agent | The *Run Command* plugin enabled on the instances (it is by default on Oracle images) |
| Outbound access from instances | To GitHub (or your mirror of this repository) and the OS package repositories |
| Hammerspace password | Ideally in an **OCI Vault secret** (see below) |

## Deploy

```bash
# 1. See which instances would be deployed — nothing runs
python3 oci_deploy.py --compartment-id <OCID> --shape "BM.GPU.GB200-v3.4" --dry-run

# 2. Deploy, reading the Hammerspace password from OCI Vault (recommended)
python3 oci_deploy.py --compartment-id <OCID> --vault-secret-id <SECRET_OCID> --yes

# Only specific instances
python3 oci_deploy.py --compartment-id <OCID> --instance-id <OCID1> --instance-id <OCID2>

# Skip instances that are already registered in Hammerspace
python3 oci_deploy.py --compartment-id <OCID> \
  --hs-host 10.0.10.15 --hs-password-file ~/.hs_password --skip-registered --yes

# Only the DI part
python3 oci_deploy.py --compartment-id <OCID> --mode di --yes
```

| Option | Meaning |
|---|---|
| `--shape`, `--name-pattern`, `--instance-id` | Which instances to target |
| `--mode tier0 \| di \| both` | What to deploy (default `both`) |
| `--skip-registered` | Query Hammerspace first and leave registered nodes alone |
| `--repo-url`, `--repo-branch` | Where the instances clone the playbook from |
| `--parallel` | How many instances at once (default 5) |
| `--timeout` | Per-instance limit in seconds (default 3600) |
| `--dry-run` / `--yes` | Plan only / skip the confirmation |

The script polls each Run Command until it finishes and prints a per-instance
result. Instance-side logs are in the Run Command output (OCI Console →
Compute → Instance → Run Command) — no SSH needed.

## Keeping the password out of the payload

| Method | How | Where the password ends up |
|---|---|---|
| **OCI Vault** (recommended) | `--vault-secret-id <OCID>` | The instance fetches it itself; it never appears in the Run Command payload |
| Password file | `--hs-password-file ~/.hs_password` | Injected as an environment variable, encrypted in transit |
| Environment | `HAMMERSPACE_PASSWORD` | Same as above |

One-time Vault setup:

```bash
oci vault secret create-base64 --compartment-id <OCID> \
  --vault-id <VAULT_OCID> --key-id <KEY_OCID> \
  --secret-name "hammerspace-api-password" \
  --secret-content-content "$(echo -n 'YourPassword' | base64)"
```

The instances need an IAM policy allowing them to read the secret, for
example: `Allow dynamic-group gpu-instances to read secret-bundles in
compartment <name>`.

## What happens on each instance

1. `oci_deploy.py` lists the GPU instances in the compartment.
2. It checks the Cloud Agent's Run Command plugin is active on each.
3. Optionally it asks Hammerspace which nodes are already registered.
4. It sends the bootstrap script via the Run Command API.
5. The instance installs Ansible, clones the repository, and runs `site.yml`
   with `ansible_connection: local`.
6. The script polls for completion and reports the result.
