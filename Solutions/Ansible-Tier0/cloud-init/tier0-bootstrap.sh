#!/bin/bash
# =============================================================================
# Tier 0 + DI Cloud-Init Bootstrap Script
# =============================================================================
# Embed this in OCI instance user_data (or cloud-init) to fully automate
# Tier 0 storage and DI deployment on first boot. No SSH access required.
#
# The instance:
#   1. Installs Ansible + dependencies
#   2. Clones the Ansible-Tier0 repo (or uses a pre-staged bundle)
#   3. Runs the playbook locally (ansible_connection: local)
#   4. Logs everything to /var/log/tier0-bootstrap.log
#
# Usage in OCI:
#   - Paste into instance "Cloud-Init Script" field, OR
#   - Reference via Terraform: user_data = file("cloud-init/tier0-bootstrap.sh")
#
# Required environment variables (set below or via instance metadata):
#   HAMMERSPACE_API_HOST    — Anvil management IP
#   HAMMERSPACE_API_PASSWORD — API password (or use vault)
#   ANSIBLE_REPO_URL       — Git repo URL (HTTPS, no SSH key needed)
#
# =============================================================================

set -euo pipefail

# ---- Configuration (customize these) ----------------------------------------

# Git repo containing the Ansible playbooks (HTTPS — no SSH key required)
ANSIBLE_REPO_URL="${ANSIBLE_REPO_URL:-https://github.com/BeratUlualan/Ansible-Tier0.git}"
ANSIBLE_REPO_BRANCH="${ANSIBLE_REPO_BRANCH:-main}"

# Hammerspace cluster connection
HAMMERSPACE_API_HOST="${HAMMERSPACE_API_HOST:-10.0.10.15}"
HAMMERSPACE_API_USER="${HAMMERSPACE_API_USER:-admin}"
HAMMERSPACE_API_PASSWORD="${HAMMERSPACE_API_PASSWORD:-changeme}"

# OCI Vault: fetch password from OCI Vault if secret OCID is set
# (used by oci_deploy.py — avoids plaintext password in Run Command payload)
if [ -n "${OCI_VAULT_SECRET_OCID:-}" ]; then
    echo "Fetching Hammerspace password from OCI Vault (${OCI_VAULT_SECRET_OCID})..."
    HAMMERSPACE_API_PASSWORD=$(oci secrets secret-bundle get \
        --secret-id "$OCI_VAULT_SECRET_OCID" --auth instance_principal \
        --query 'data."secret-bundle-content".content' --raw-output 2>/dev/null \
        | base64 -d 2>/dev/null) || {
        echo "WARNING: OCI Vault fetch failed, falling back to HAMMERSPACE_API_PASSWORD env var"
    }
fi

# DI deployment (set to "true" to also deploy DI)
DEPLOY_DI="${DEPLOY_DI:-false}"
DI_DEPLOYMENT_TYPE="${DI_DEPLOYMENT_TYPE:-container}"
DI_IMAGE_SOURCE="${DI_IMAGE_SOURCE:-build}"

# Working directory
WORK_DIR="/opt/ansible-tier0"
LOG_FILE="/var/log/tier0-bootstrap.log"

# ---- Logging -----------------------------------------------------------------

exec > >(tee -a "$LOG_FILE") 2>&1
echo "============================================"
echo "TIER 0 BOOTSTRAP STARTED: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Hostname: $(hostname)"
echo "Architecture: $(uname -m)"
echo "============================================"

# ---- Wait for OS readiness ---------------------------------------------------
# The instance may have just booted — wait for systemd to finish initializing,
# cloud-init to complete, and the package manager to be available.

echo "[0/5] Waiting for OS readiness..."

# Wait for systemd to reach a stable state
echo "  Waiting for systemd..."
TRIES=0
MAX_TRIES=60
while [ $TRIES -lt $MAX_TRIES ]; do
    STATE=$(systemctl is-system-running 2>/dev/null || echo "not-ready")
    if [ "$STATE" = "running" ] || [ "$STATE" = "degraded" ]; then
        echo "  systemd: $STATE"
        break
    fi
    TRIES=$((TRIES + 1))
    sleep 5
done
if [ $TRIES -eq $MAX_TRIES ]; then
    echo "  WARNING: systemd did not reach running state (current: $STATE), proceeding anyway"
fi

# Wait for cloud-init to finish (if running)
if command -v cloud-init &>/dev/null; then
    echo "  Waiting for cloud-init to finish..."
    cloud-init status --wait 2>/dev/null || true
    echo "  cloud-init: $(cloud-init status 2>/dev/null || echo 'not available')"
fi

# Wait for package manager lock to be released
echo "  Waiting for package manager..."

# On Ubuntu: stop unattended-upgrades if running (common cause of apt lock)
if command -v apt-get &>/dev/null; then
    systemctl stop unattended-upgrades 2>/dev/null || true
    systemctl stop apt-daily.timer 2>/dev/null || true
    systemctl stop apt-daily-upgrade.timer 2>/dev/null || true
    # Kill any running apt/dpkg processes holding the lock
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
        echo "  apt lock held, waiting..."
        sleep 10
    done
fi

TRIES=0
while [ $TRIES -lt 60 ]; do
    if command -v dnf &>/dev/null; then
        dnf check-update --quiet 2>/dev/null && break
    elif command -v apt-get &>/dev/null; then
        apt-get -qq check 2>/dev/null && break
    else
        break
    fi
    TRIES=$((TRIES + 1))
    sleep 5
done

echo "[0/5] OS ready"

# ---- Install Ansible + dependencies -----------------------------------------

echo "[1/5] Installing Ansible and dependencies..."

if command -v dnf &>/dev/null; then
    # RHEL/Rocky/Alma
    dnf install -y epel-release 2>/dev/null || true
    dnf install -y ansible-core python3-pip git
elif command -v apt-get &>/dev/null; then
    # Ubuntu/Debian
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ansible python3-pip git
else
    echo "ERROR: Unsupported OS — neither dnf nor apt found"
    exit 1
fi

# Install required Python packages
pip3 install jmespath requests 2>/dev/null || pip3 install --break-system-packages jmespath requests

# Install required Ansible collections
ansible-galaxy collection install ansible.posix community.general 2>/dev/null || true

echo "[1/5] Ansible installed: $(ansible --version | head -1)"

# ---- Clone the repo ---------------------------------------------------------

echo "[2/5] Cloning Ansible-Tier0 repo..."

if [ -d "$WORK_DIR/.git" ]; then
    echo "  Repo already exists at $WORK_DIR, pulling latest..."
    cd "$WORK_DIR"
    git pull origin "$ANSIBLE_REPO_BRANCH" || true
else
    git clone --branch "$ANSIBLE_REPO_BRANCH" "$ANSIBLE_REPO_URL" "$WORK_DIR"
    cd "$WORK_DIR"
fi

echo "[2/5] Repo ready at $WORK_DIR"

# ---- Generate local inventory ------------------------------------------------

echo "[3/5] Generating local inventory..."

NODE_NAME=$(hostname)
NODE_IP=$(ip route get 1 | awk '{print $7; exit}')

cat > "$WORK_DIR/inventory_local.yml" << EOF
---
all:
  children:
    storage_servers:
      hosts:
        ${NODE_NAME}:
          ansible_connection: local
          ansible_python_interpreter: /usr/bin/python3

    di_nodes:
      hosts:
        ${NODE_NAME}:
          ansible_connection: local
          ansible_python_interpreter: /usr/bin/python3
          di_node_name: "${NODE_NAME}-mover"
          di_node_ip: "${NODE_IP}"
EOF

echo "[3/5] Inventory generated: ${NODE_NAME} (${NODE_IP})"

# ---- Generate vault file (plaintext for cloud-init, encrypt later) -----------

echo "[4/5] Configuring vault..."

cat > "$WORK_DIR/vars/vault.yml" << EOF
---
vault_hammerspace_api_password: "${HAMMERSPACE_API_PASSWORD}"
EOF

chmod 600 "$WORK_DIR/vars/vault.yml"

# ---- Apply overrides to vars/main.yml ---------------------------------------

# Set the Hammerspace API host (sed in-place)
sed -i "s|^hammerspace_api_host:.*|hammerspace_api_host: \"${HAMMERSPACE_API_HOST}\"|" "$WORK_DIR/vars/main.yml"

# Set DI deployment vars
sed -i "s|^deploy_di:.*|deploy_di: ${DEPLOY_DI}|" "$WORK_DIR/vars/main.yml"
sed -i "s|^di_deployment_type:.*|di_deployment_type: \"${DI_DEPLOYMENT_TYPE}\"|" "$WORK_DIR/vars/main.yml"

# Set cluster mgmt IP for DI
sed -i "s|^hammerspace_cluster_mgmt_ip:.*|hammerspace_cluster_mgmt_ip: \"${HAMMERSPACE_API_HOST}\"|" "$WORK_DIR/vars/main.yml"

# Enable GPU fabric AZ detection (auto-detect AZ from GPU memory fabric)
GPU_FABRIC_AZ="${GPU_FABRIC_AZ:-true}"
sed -i "s|^hammerspace_gpu_fabric_az:.*|hammerspace_gpu_fabric_az: ${GPU_FABRIC_AZ}|" "$WORK_DIR/vars/main.yml"
sed -i "s|^hammerspace_volume_az_prefix_enabled:.*|hammerspace_volume_az_prefix_enabled: true|" "$WORK_DIR/vars/main.yml"

echo "[4/5] Configuration applied"

# ---- Run the playbook --------------------------------------------------------

echo "[5/5] Running Ansible playbook..."
echo "============================================"

cd "$WORK_DIR"

ansible-playbook site.yml \
    -i inventory_local.yml \
    --connection local \
    -e "ansible_python_interpreter=/usr/bin/python3" \
    2>&1 | tee -a "$LOG_FILE"

RC=${PIPESTATUS[0]}

echo "============================================"
if [ $RC -eq 0 ]; then
    echo "TIER 0 BOOTSTRAP COMPLETE: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "  Node: ${NODE_NAME}"
    echo "  IP: ${NODE_IP}"
    echo "  Hammerspace: ${HAMMERSPACE_API_HOST}"
    echo "  DI: ${DEPLOY_DI}"
else
    echo "TIER 0 BOOTSTRAP FAILED (rc=$RC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "  Check log: $LOG_FILE"
fi
echo "============================================"

exit $RC
