# Production Readiness Checklist — Hammerspace Tier 0 Ansible

**Date:** March 26, 2026
**Audience:** Customers deploying Hammerspace Tier 0 / LSS storage
**Purpose:** Ensure your deployment is secure, reliable, and maintainable

---

## Quick Reference

| Category | Items | Priority |
|----------|-------|----------|
| [Security Hardening](#1-security-hardening) | 6 | Must complete before production |
| [Operational Reliability](#2-operational-reliability) | 5 | Must complete before production |
| [Monitoring & Health Checks](#3-monitoring--health-checks) | 4 | Complete within first week |
| [Backup & Recovery](#4-backup--recovery) | 4 | Complete within first week |
| [Ongoing Maintenance](#5-ongoing-maintenance) | 5 | Recurring schedule |

---

## 1. Security Hardening

### 1.1 Encrypt the Ansible Vault

**Priority:** CRITICAL — do this first
**Risk:** API admin password stored in plaintext on disk

The vault file `vars/vault.yml` contains your Hammerspace API password. It must be encrypted before use in production or committing to any repository.

```bash
# Encrypt the vault file
ansible-vault encrypt vars/vault.yml

# Verify encryption (should show $ANSIBLE_VAULT;1.1;AES256)
head -1 vars/vault.yml

# Store vault password securely — choose one method:

# Option A: Password file (recommended for automation)
echo 'your-vault-password' > ~/.vault_pass
chmod 600 ~/.vault_pass

# Option B: Environment variable (good for CI/CD)
export ANSIBLE_VAULT_PASSWORD_FILE=~/.vault_pass

# Run playbooks with vault
ansible-playbook site.yml --vault-password-file ~/.vault_pass
```

**Verification:**
```bash
cat vars/vault.yml            # Should show encrypted content, not plaintext
ansible-vault view vars/vault.yml  # Should prompt for password, then show variables
```

**Never do:**
- Commit `vars/vault.yml` unencrypted to version control
- Store the vault password in the same repository as the vault file
- Share vault passwords over email or chat

### 1.2 Secure Script Credentials

**Priority:** CRITICAL
**Risk:** Passwords visible in `ps aux` output and shell history

All Python operational scripts support secure credential methods. **Do not use `--password` on the command line in production.**

```bash
# Setup (once per admin workstation)
echo 'your-api-password' > ~/.hs_password
chmod 600 ~/.hs_password

# Verify permissions
ls -la ~/.hs_password  # Should show -rw------- (owner-only read/write)
```

**Credential priority (highest to lowest):**

| Method | Visibility | Recommended For |
|--------|-----------|-----------------|
| `--password-file ~/.hs_password` | Not visible anywhere | Day-to-day operations |
| `HAMMERSPACE_PASSWORD` env var | Visible in `/proc/PID/environ` | CI/CD pipelines |
| Interactive prompt (no flag) | Not visible anywhere | Ad-hoc one-time use |
| `--password 'xxx'` | Visible in `ps aux` + shell history | **Avoid in production** |

**Example — correct usage:**
```bash
python3 set_availability_drop.py --host 10.0.10.15 --user admin \
  --password-file ~/.hs_password --node tier0-node-01 --check
```

### 1.3 Enable SSL Certificate Validation

**Priority:** HIGH
**Risk:** Man-in-the-middle attacks on API traffic

By default, SSL verification is disabled (`hammerspace_api_validate_certs: false`). In production with proper certificates:

```yaml
# vars/main.yml
hammerspace_api_validate_certs: true
```

If using self-signed certificates, distribute the CA bundle to all nodes:
```bash
# Copy CA cert to system trust store
sudo cp hammerspace-ca.pem /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

### 1.4 Restrict NFS Export Access

**Priority:** HIGH
**Risk:** Unauthorized access to storage volumes

Review and tighten NFS client subnets in `vars/main.yml`:

```yaml
# AVOID: Open to all (only acceptable for isolated networks)
client_subnets:
  - "0.0.0.0/0"

# RECOMMENDED: Restrict to specific subnets
client_subnets:
  - "10.200.104.0/24"   # GPU compute nodes
  - "10.200.105.0/24"   # Additional compute
```

Verify Hammerspace and mover node IPs are correct (these get `no_root_squash`):
```yaml
hammerspace_nodes:
  - "10.0.10.15"       # Anvil cluster management IP — verify this is correct

mover_nodes:
  - "10.0.12.242"      # DSX/DI nodes — verify all IPs
```

### 1.5 Review Firewall Rules

**Priority:** HIGH
**Risk:** Unnecessary ports exposed

After deployment, verify only required ports are open:

```bash
# Check what's exposed
sudo iptables -L -n          # or: sudo firewall-cmd --list-all
sudo ss -tlnp                # List listening ports
```

Required ports (NFS):

| Port | Protocol | Service |
|------|----------|---------|
| 111 | TCP/UDP | portmapper (rpcbind) |
| 2049 | TCP/UDP | NFS |
| 20048 | TCP/UDP | mountd |
| 20049 | TCP | NFS-RDMA (if RDMA enabled) |

### 1.6 Rotate Credentials Periodically

**Priority:** MEDIUM
**Frequency:** Every 90 days or after personnel changes

```bash
# Step 1: Update password in Hammerspace GUI/CLI first

# Step 2: Update Ansible vault
ansible-vault edit vars/vault.yml
# Change vault_hammerspace_api_password to new value

# Step 3: Update password file for scripts
echo 'new-password' > ~/.hs_password

# Step 4: Re-key the vault itself (change encryption password)
ansible-vault rekey vars/vault.yml

# Step 5: Verify connectivity
python3 set_availability_drop.py --host 10.0.10.15 --user admin \
  --password-file ~/.hs_password --check --all-nodes
```

---

## 2. Operational Reliability

### 2.1 Verify Mount Protection Is Active

**Priority:** CRITICAL
**Risk:** Accidental unmount causes storage unavailability

The playbook deploys systemd guard services that prevent accidental unmounts and auto-remount if a filesystem drops. Verify they are running:

```bash
# Check guard target is active
systemctl status hammerspace-guards.target

# Check individual guard services (one per mount point)
systemctl list-units 'hammerspace-guard-*' --all

# Check remount watchdog timer
systemctl status hammerspace-remount.timer
systemctl list-timers hammerspace-remount.timer

# Verify all mount points are mounted
df -h /hammerspace/hsvol*
```

**Expected output:** All guards active, timer running, all hsvol mounts present.

**If a guard is stopped:**
```bash
sudo systemctl restart hammerspace-guards.target
```

### 2.2 Verify RAID Array Health

**Priority:** CRITICAL
**Risk:** Silent drive failure degrades performance or causes data loss

```bash
# Check RAID status
cat /proc/mdstat

# Check each array for degraded state
sudo mdadm --detail /dev/md0
sudo mdadm --detail /dev/md1
# ... repeat for all arrays

# Check for failed drives
sudo mdadm --examine --scan
```

**Healthy output:** All arrays show `[UU]` (both members up). No `[_U]` or `[U_]`.

**If degraded:**
1. Identify failed drive from `mdadm --detail`
2. Follow the [RACK_OPERATIONS_RUNBOOK.md](RACK_OPERATIONS_RUNBOOK.md) RMA procedure

### 2.3 Verify NFS Exports

**Priority:** HIGH
**Risk:** Hammerspace cannot access volumes if exports are misconfigured

```bash
# Check exports are active
exportfs -v

# Verify from a client perspective
showmount -e localhost

# Test NFS connectivity from Hammerspace Anvil
# (run from Anvil node)
showmount -e <tier0-node-ip>
```

**Expected:** Each `/hammerspace/hsvolN` path appears with correct client IPs and options.

### 2.4 Verify Jumbo Frames (MTU)

**Priority:** HIGH
**Risk:** Poor network performance; packet fragmentation on large I/O

```bash
# Check interface MTU
ip link show | grep mtu

# Test jumbo frame connectivity to other nodes
ping -M do -s 8972 -c 3 <anvil-ip>
ping -M do -s 8972 -c 3 <other-tier0-node-ip>
```

**Expected:** All storage interfaces show MTU 9000. Ping succeeds with 8972-byte payload.

**If ping fails:** Check that all switches in the path have MTU >= 9216.

### 2.5 Verify Hammerspace Registration

**Priority:** HIGH
**Risk:** Node/volume not visible in Hammerspace cluster

```bash
# From Hammerspace Anvil CLI
anvil> node-list
anvil> volume-list
anvil> share-list

# Or via API
python3 set_availability_drop.py --host 10.0.10.15 --user admin \
  --password-file ~/.hs_password --check --all-nodes
```

**Expected:** All Tier 0 nodes listed, all volumes ONLINE, all shares exported.

---

## 3. Monitoring & Health Checks

### 3.1 Enable Prometheus Monitoring

**Priority:** HIGH

If not already enabled during deployment:

```yaml
# vars/main.yml
hammerspace_enable_prometheus: true
hammerspace_prometheus_config:
  port: 9100
```

Then re-run the playbook or manually verify:
```bash
curl -s http://localhost:9100/metrics | head -20
```

### 3.2 Set Up Automated Health Checks

**Priority:** HIGH
**Frequency:** Every 5 minutes via cron or monitoring system

Create `/usr/local/bin/tier0-healthcheck.sh`:
```bash
#!/bin/bash
# Tier 0 Health Check Script
ERRORS=0

# Check RAID arrays
if grep -q '_' /proc/mdstat 2>/dev/null; then
    echo "CRITICAL: Degraded RAID array detected"
    ERRORS=$((ERRORS + 1))
fi

# Check mount points
for vol in /hammerspace/hsvol*; do
    if ! mountpoint -q "$vol" 2>/dev/null; then
        echo "CRITICAL: $vol is not mounted"
        ERRORS=$((ERRORS + 1))
    fi
done

# Check NFS exports
EXPORT_COUNT=$(exportfs -v 2>/dev/null | grep -c hammerspace)
MOUNT_COUNT=$(ls -d /hammerspace/hsvol* 2>/dev/null | wc -l)
if [ "$EXPORT_COUNT" -lt "$MOUNT_COUNT" ]; then
    echo "WARNING: Only $EXPORT_COUNT/$MOUNT_COUNT exports active"
    ERRORS=$((ERRORS + 1))
fi

# Check guard services
if ! systemctl is-active --quiet hammerspace-guards.target; then
    echo "WARNING: Mount guard target is not active"
    ERRORS=$((ERRORS + 1))
fi

# Check NFS service
if ! systemctl is-active --quiet nfs-server; then
    echo "CRITICAL: NFS server is not running"
    ERRORS=$((ERRORS + 1))
fi

if [ $ERRORS -eq 0 ]; then
    echo "OK: All checks passed"
fi
exit $ERRORS
```

```bash
chmod +x /usr/local/bin/tier0-healthcheck.sh

# Add to cron (every 5 minutes)
echo '*/5 * * * * /usr/local/bin/tier0-healthcheck.sh >> /var/log/tier0-health.log 2>&1' | sudo crontab -
```

### 3.3 Monitor Disk Space

**Priority:** HIGH
**Risk:** Full filesystem causes I/O errors and Hammerspace evacuation events

```bash
# Check current usage
df -h /hammerspace/hsvol*

# Set up alert threshold (Hammerspace triggers evacuation at 98%)
# Alert at 90% to give time to respond
```

Hammerspace volume thresholds (configured in `vars/main.yml`):

| Threshold | Default | Behavior |
|-----------|---------|----------|
| `hammerspace_volume_high_threshold` | 0.98 (98%) | Triggers data evacuation |
| `hammerspace_volume_low_threshold` | 0.90 (90%) | Target after evacuation |

### 3.4 Monitor Network Performance

**Priority:** MEDIUM
**Frequency:** Weekly or after network changes

```bash
# If iperf testing is enabled:
ansible-playbook site.yml --tags precheck -e iperf_test_enabled=true \
  -e '{"iperf_test_targets": ["10.0.13.213"]}' --check

# Manual iperf test
iperf3 -c <target-ip> -P 64 -t 10
```

**Expected:** >= 40 Gbps for 50G networks, >= 80 Gbps for 100G+ networks.

---

## 4. Backup & Recovery

### 4.1 Back Up Ansible Configuration

**Priority:** CRITICAL
**Frequency:** After every configuration change

```bash
# What to back up (store securely, NOT on Tier 0 nodes):
# 1. vars/main.yml          — all configuration
# 2. vars/vault.yml          — encrypted credentials
# 3. inventory files          — node definitions
# 4. Any custom templates    — site-specific changes

# Example backup
tar czf tier0-ansible-backup-$(date +%Y%m%d).tar.gz \
  vars/ inventory*.yml ansible.cfg site*.yml \
  --exclude='*.pyc' --exclude='__pycache__'
```

**Store backups:**
- On a separate system (not the Tier 0 nodes being managed)
- Encrypted if containing vault files
- Retain at least 3 versions

### 4.2 Document Your Deployment Parameters

**Priority:** HIGH

Record these values for disaster recovery:

| Parameter | Your Value | Where to Find |
|-----------|-----------|---------------|
| Anvil API IP | ___________ | `vars/main.yml` → `hammerspace_api_host` |
| Storage type | ___________ | `vars/main.yml` → `storage_type` |
| RAID level | ___________ | `vars/main.yml` → `raid_level` |
| RAID grouping | ___________ | `vars/main.yml` → `raid_grouping_strategy` |
| NVMe count per node | ___________ | `expected_nvme_count` |
| Mount base path | ___________ | `vars/main.yml` → `mount_base_path` |
| NFS thread count | ___________ | `vars/main.yml` → `nfs_threads` |
| Network MTU | ___________ | `vars/main.yml` → `expected_mtu` |
| AZ mapping enabled | ___________ | `vars/main.yml` → `hammerspace_enable_az_mapping` |
| Volume groups | ___________ | `vars/main.yml` → `hammerspace_create_volume_groups` |

### 4.3 Know Your Recovery Procedures

**Priority:** HIGH

| Scenario | Action | Reference |
|----------|--------|-----------|
| Single drive failure | Replace drive, rebuild RAID | [RACK_OPERATIONS_RUNBOOK.md](RACK_OPERATIONS_RUNBOOK.md) |
| Node failure | Set availability-drop disabled, replace hardware, re-run playbook | [RACK_OPERATIONS_RUNBOOK.md](RACK_OPERATIONS_RUNBOOK.md) |
| Accidental unmount | Watchdog auto-remounts within 1 min; manual: `mount -a` | systemd guard services |
| NFS service crash | `systemctl restart nfs-server` | Node is auto-registered |
| Full re-deployment | `ansible-playbook site.yml --vault-password-file ~/.vault_pass` | [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) |
| Partial failure (some nodes) | `ansible-playbook site.yml --limit failed-node-01` | Ansible `--limit` flag |
| Password rotation | See [Section 1.6](#16-rotate-credentials-periodically) | This document |

### 4.4 Test Recovery Procedures

**Priority:** MEDIUM
**Frequency:** Quarterly

Run a `--check` mode deployment to verify your configuration is still valid:
```bash
ansible-playbook site.yml --check --diff --vault-password-file ~/.vault_pass
```

This shows what the playbook would change without actually changing anything.

---

## 5. Ongoing Maintenance

### 5.1 Pre-Maintenance Checklist (Before Any RMA/Shutdown)

```bash
# 1. Disable availability-drop on affected nodes
python3 set_availability_drop.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node <NODE_NAME> --disable

# 2. Verify it took effect
python3 set_availability_drop.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node <NODE_NAME> --check

# 3. Proceed with maintenance
```

### 5.2 Post-Maintenance Checklist (After Any RMA/Restart)

```bash
# 1. Verify node is back online
python3 set_availability_drop.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node <NODE_NAME> --health-check

# 2. Re-enable availability-drop
python3 set_availability_drop.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node <NODE_NAME> --enable

# 3. Verify mounts and exports
df -h /hammerspace/hsvol*
exportfs -v

# 4. Check RAID health
cat /proc/mdstat
```

### 5.3 Adding New Nodes

```bash
# 1. Add new node to inventory
# 2. Run playbook targeting only the new node
ansible-playbook site.yml --limit new-node-01 --vault-password-file ~/.vault_pass

# 3. Verify registration
python3 set_availability_drop.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node new-node-01 --check
```

### 5.4 Removing Nodes

```bash
# 1. Disable availability-drop
python3 set_availability_drop.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node <NODE_NAME> --disable

# 2. Remove from Hammerspace (dry run first)
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node <NODE_NAME> --dry-run

# 3. Execute removal
python3 cleanup_instance_nodes.py --host <ANVIL_IP> --user admin \
  --password-file ~/.hs_password --node <NODE_NAME>

# 4. Remove from inventory file
```

### 5.5 Updating Ansible Configuration

When changing `vars/main.yml`:

```bash
# 1. Preview changes (no modifications)
ansible-playbook site.yml --check --diff --vault-password-file ~/.vault_pass

# 2. Review the diff output carefully

# 3. Apply changes
ansible-playbook site.yml --vault-password-file ~/.vault_pass

# 4. Verify
/usr/local/bin/tier0-healthcheck.sh
```

**Safe to change at any time** (idempotent, non-destructive):
- NFS thread count, export options
- Hammerspace API timeouts, task queue thresholds
- Firewall rules
- Mount protection settings

**Require caution** (may disrupt service):
- `storage_type`, `raid_level`, `raid_grouping_strategy` — only for new deployments
- `force_raid_recreate: true` — **DESTRUCTIVE**, destroys existing RAID arrays
- `force_fs_recreate: true` — **DESTRUCTIVE**, reformats filesystems
- `format_nvme_to_4k: true` — **DESTRUCTIVE**, erases all NVMe data

---

## Verification Checklist

Run through this checklist after initial deployment and after any major change:

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | Vault encrypted | `head -1 vars/vault.yml` | `$ANSIBLE_VAULT;1.1;AES256` |
| 2 | Password file secured | `ls -la ~/.hs_password` | `-rw-------` (600) |
| 3 | RAID healthy | `cat /proc/mdstat` | All arrays `[UU]`, no `_` |
| 4 | Mounts present | `df -h /hammerspace/hsvol*` | All volumes mounted |
| 5 | Guards active | `systemctl is-active hammerspace-guards.target` | `active` |
| 6 | Watchdog running | `systemctl is-active hammerspace-remount.timer` | `active` |
| 7 | NFS exporting | `exportfs -v \| grep hammerspace \| wc -l` | Matches mount count |
| 8 | MTU correct | `ip link show \| grep 'mtu 9000'` | Storage interfaces at 9000 |
| 9 | Hammerspace registered | Anvil CLI: `node-list` | All nodes listed, ONLINE |
| 10 | Volumes online | Anvil CLI: `volume-list` | All volumes ONLINE |
| 11 | NFS service running | `systemctl is-active nfs-server` | `active` |
| 12 | Health script passes | `/usr/local/bin/tier0-healthcheck.sh` | `OK: All checks passed` |

---

## Support Resources

| Resource | Location |
|----------|----------|
| Deployment walkthrough | [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) |
| Air-gapped / HDD deployment | [DEPLOYMENT_GUIDE_DARKSITE_HDD.md](DEPLOYMENT_GUIDE_DARKSITE_HDD.md) |
| RMA and rack operations | [RACK_OPERATIONS_RUNBOOK.md](RACK_OPERATIONS_RUNBOOK.md) |
| All variables and defaults | [VARIABLE_REFERENCE.md](VARIABLE_REFERENCE.md) |
| Architecture and design | [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) |
