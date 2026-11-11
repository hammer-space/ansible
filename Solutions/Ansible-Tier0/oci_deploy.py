#!/usr/bin/env python3
"""
OCI Run Command Orchestrator for Hammerspace Tier 0 / DI Deployment

Automates Tier 0 and DI deployment on OCI GPU instances using OCI Run Command.
No SSH keys, no network access to instances, no human intervention.

Each target instance self-provisions: the Oracle Cloud Agent executes a bootstrap
script that installs Ansible, clones the repo, and runs the playbook locally.

Prerequisites:
    pip3 install oci requests

Usage:
    # Discover instances (dry run)
    python3 oci_deploy.py --compartment-id <OCID> --dry-run

    # Deploy Tier 0 + DI to all GPU instances
    python3 oci_deploy.py --compartment-id <OCID> --mode both \\
        --vault-secret-id <SECRET_OCID> --yes

    # Deploy to specific instances only
    python3 oci_deploy.py --compartment-id <OCID> \\
        --instance-id <OCID1> --instance-id <OCID2> --yes

    # Skip instances already registered in Hammerspace
    python3 oci_deploy.py --compartment-id <OCID> \\
        --hs-host 10.0.10.15 --hs-user admin --hs-password-file ~/.hs_password \\
        --skip-registered --yes
"""

import argparse
import base64
import getpass
import hashlib
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    import oci
except ImportError:
    print("ERROR: OCI SDK not installed. Run: pip3 install oci")
    sys.exit(1)

try:
    import requests
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    requests = None  # Only needed for Hammerspace pre-check


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class InstanceInfo:
    id: str
    display_name: str
    shape: str
    fault_domain: str
    private_ip: str
    lifecycle_state: str
    agent_ready: bool = False
    hs_registered: bool = False


@dataclass
class CommandResult:
    instance_id: str
    instance_name: str
    command_id: str
    status: str  # SUCCEEDED, FAILED, TIMED_OUT, CANCELED
    output: str = ""
    error: str = ""


@dataclass
class DeploymentReport:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    timed_out: int = 0
    skipped: int = 0
    results: List[CommandResult] = field(default_factory=list)


# =============================================================================
# Hammerspace Client (reused pattern from cleanup_instance_nodes.py)
# =============================================================================

class HammerspaceClient:
    def __init__(self, host: str, user: str, password: str, port: int = 8443,
                 verify_ssl: bool = False):
        self.base_url = f"https://{host}:{port}/mgmt/v1.2/rest"
        self.auth = (user, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = verify_ssl

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        response = self.session.get(f"{self.base_url}/nodes", timeout=30)
        response.raise_for_status()
        return response.json()


# =============================================================================
# OCI Run Command Deployer
# =============================================================================

class OCIRunCommandDeployer:
    def __init__(self, compartment_id: str, config_profile: str = "DEFAULT",
                 region: Optional[str] = None, signer=None):
        self.compartment_id = compartment_id

        if signer:
            # Resource Principal auth (OCI Functions) or Instance Principal
            self.compute_client = oci.core.ComputeClient(config={}, signer=signer)
            self.agent_client = oci.compute_instance_agent.ComputeInstanceAgentClient(config={}, signer=signer)
            self.vnet_client = oci.core.VirtualNetworkClient(config={}, signer=signer)
        else:
            # Config file auth (CLI usage)
            try:
                self.config = oci.config.from_file(profile_name=config_profile)
            except oci.exceptions.ConfigFileNotFound:
                print("ERROR: OCI config not found. Run: oci setup config")
                sys.exit(1)

            if region:
                self.config["region"] = region

            self.compute_client = oci.core.ComputeClient(self.config)
            self.agent_client = oci.compute_instance_agent.ComputeInstanceAgentClient(self.config)
            self.vnet_client = oci.core.VirtualNetworkClient(self.config)

    def discover_instances(self, shape_filter: Optional[str] = None,
                           name_pattern: Optional[str] = None,
                           instance_ids: Optional[List[str]] = None) -> List[InstanceInfo]:
        """Discover running instances in the compartment."""
        instances = []

        # If specific instance IDs provided, fetch each directly
        if instance_ids:
            for iid in instance_ids:
                try:
                    resp = self.compute_client.get_instance(iid)
                    inst = resp.data
                    if inst.lifecycle_state == "RUNNING":
                        ip = self._get_private_ip(inst.id, inst.compartment_id)
                        instances.append(InstanceInfo(
                            id=inst.id,
                            display_name=inst.display_name,
                            shape=inst.shape,
                            fault_domain=inst.fault_domain or "",
                            private_ip=ip,
                            lifecycle_state=inst.lifecycle_state
                        ))
                except oci.exceptions.ServiceError as e:
                    print(f"  WARNING: Could not fetch instance {iid}: {e.message}")
            return instances

        # Otherwise, list all instances in compartment
        all_instances = oci.pagination.list_call_get_all_results(
            self.compute_client.list_instances,
            compartment_id=self.compartment_id,
            lifecycle_state="RUNNING"
        ).data

        for inst in all_instances:
            # Shape filter
            if shape_filter and inst.shape != shape_filter:
                continue

            # Name pattern filter
            if name_pattern and not re.search(name_pattern, inst.display_name):
                continue

            ip = self._get_private_ip(inst.id, inst.compartment_id)
            instances.append(InstanceInfo(
                id=inst.id,
                display_name=inst.display_name,
                shape=inst.shape,
                fault_domain=inst.fault_domain or "",
                private_ip=ip,
                lifecycle_state=inst.lifecycle_state
            ))

        return instances

    def _get_private_ip(self, instance_id: str, compartment_id: str) -> str:
        """Get the primary private IP of an instance."""
        try:
            vnic_attachments = self.compute_client.list_vnic_attachments(
                compartment_id=compartment_id,
                instance_id=instance_id
            ).data
            if vnic_attachments:
                vnic = self.vnet_client.get_vnic(vnic_attachments[0].vnic_id).data
                return vnic.private_ip or ""
        except oci.exceptions.ServiceError:
            pass
        return ""

    def check_agent_status(self, instances: List[InstanceInfo]) -> List[InstanceInfo]:
        """Check if the Run Command plugin is available on each instance."""
        for inst in instances:
            try:
                plugins = self.compute_client.list_instance_agent_plugins(
                    instanceagent_id=inst.id,
                    compartment_id=self.compartment_id
                ).data
                for plugin in plugins:
                    if plugin.name == "Run Command" and plugin.status == "RUNNING":
                        inst.agent_ready = True
                        break
            except oci.exceptions.ServiceError:
                inst.agent_ready = False
        return instances

    def check_hammerspace_registration(self, instances: List[InstanceInfo],
                                       hs_client: HammerspaceClient) -> Tuple[List[InstanceInfo], List[InstanceInfo]]:
        """Cross-reference instances with Hammerspace registered nodes."""
        try:
            hs_nodes = hs_client.get_all_nodes()
            hs_node_names = {n.get("name", "") for n in hs_nodes}
        except Exception as e:
            print(f"  WARNING: Could not query Hammerspace: {e}")
            return [], instances

        registered = []
        unregistered = []
        for inst in instances:
            if inst.display_name in hs_node_names:
                inst.hs_registered = True
                registered.append(inst)
            else:
                unregistered.append(inst)

        return registered, unregistered

    def build_command_payload(self, instance: InstanceInfo, deploy_mode: str,
                               config: dict) -> Any:
        """Build the OCI Run Command payload with the bootstrap script."""
        script = self._build_bootstrap_script(instance, deploy_mode, config)

        command_details = oci.compute_instance_agent.models.CreateInstanceAgentCommandDetails(
            compartment_id=self.compartment_id,
            execution_time_limit_in_seconds=config.get("timeout", 3600),
            target=oci.compute_instance_agent.models.InstanceAgentCommandTarget(
                instance_id=instance.id
            ),
            content=oci.compute_instance_agent.models.InstanceAgentCommandContent(
                source=oci.compute_instance_agent.models.InstanceAgentCommandSourceViaTextDetails(
                    source_type="TEXT",
                    text=script,
                    text_sha256=hashlib.sha256(script.encode()).hexdigest()
                ),
                output=oci.compute_instance_agent.models.InstanceAgentCommandOutputViaTextDetails(
                    output_type="TEXT"
                )
            ),
            display_name=f"tier0-deploy-{instance.display_name}"
        )
        return command_details

    def _build_bootstrap_script(self, instance: InstanceInfo, deploy_mode: str,
                                 config: dict) -> str:
        """Construct the bootstrap script with injected configuration."""
        deploy_di = "true" if deploy_mode in ("di", "both") else "false"

        # Try to read the bootstrap script from the repo
        bootstrap_path = Path(__file__).parent / "cloud-init" / "tier0-bootstrap.sh"
        if bootstrap_path.exists():
            script = bootstrap_path.read_text()
            # Replace defaults with actual config
            replacements = {
                'ANSIBLE_REPO_URL="${ANSIBLE_REPO_URL:-https://github.com/BeratUlualan/Ansible-Tier0.git}"':
                    f'ANSIBLE_REPO_URL="${{ANSIBLE_REPO_URL:-{config["repo_url"]}}}"',
                'ANSIBLE_REPO_BRANCH="${ANSIBLE_REPO_BRANCH:-main}"':
                    f'ANSIBLE_REPO_BRANCH="${{ANSIBLE_REPO_BRANCH:-{config["repo_branch"]}}}"',
                'HAMMERSPACE_API_HOST="${HAMMERSPACE_API_HOST:-10.0.10.15}"':
                    f'HAMMERSPACE_API_HOST="${{HAMMERSPACE_API_HOST:-{config["hs_host"]}}}"',
                'HAMMERSPACE_API_USER="${HAMMERSPACE_API_USER:-admin}"':
                    f'HAMMERSPACE_API_USER="${{HAMMERSPACE_API_USER:-{config["hs_user"]}}}"',
                'DEPLOY_DI="${DEPLOY_DI:-false}"':
                    f'DEPLOY_DI="${{DEPLOY_DI:-{deploy_di}}}"',
            }
            for old, new in replacements.items():
                script = script.replace(old, new)

            # Inject vault secret OCID if provided
            if config.get("vault_secret_id"):
                script = script.replace(
                    'HAMMERSPACE_API_PASSWORD="${HAMMERSPACE_API_PASSWORD:-changeme}"',
                    f'HAMMERSPACE_API_PASSWORD="${{HAMMERSPACE_API_PASSWORD:-changeme}}"\n'
                    f'OCI_VAULT_SECRET_OCID="{config["vault_secret_id"]}"'
                )
            elif config.get("hs_password") and config["hs_password"] != "changeme":
                script = script.replace(
                    'HAMMERSPACE_API_PASSWORD="${HAMMERSPACE_API_PASSWORD:-changeme}"',
                    f'HAMMERSPACE_API_PASSWORD="${{HAMMERSPACE_API_PASSWORD:-{config["hs_password"]}}}"'
                )

            return script

        # Fallback: minimal inline script
        vault_block = ""
        if config.get("vault_secret_id"):
            vault_block = f"""
# Fetch password from OCI Vault
export HAMMERSPACE_API_PASSWORD=$(oci secrets secret-bundle get \\
    --secret-id "{config['vault_secret_id']}" --auth instance_principal \\
    --query 'data."secret-bundle-content".content' --raw-output | base64 -d)
"""

        return f"""#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/tier0-bootstrap.log) 2>&1
echo "=== Tier 0 Bootstrap via OCI Run Command: $(date -u) ==="

export HAMMERSPACE_API_HOST="{config['hs_host']}"
export HAMMERSPACE_API_USER="{config['hs_user']}"
export HAMMERSPACE_API_PASSWORD="{config.get('hs_password', 'changeme')}"
export DEPLOY_DI="{deploy_di}"
{vault_block}
# Install Ansible
if command -v dnf &>/dev/null; then
    dnf install -y epel-release 2>/dev/null || true
    dnf install -y ansible-core python3-pip git
else
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq ansible python3-pip git
fi
pip3 install jmespath requests 2>/dev/null || pip3 install --break-system-packages jmespath requests
ansible-galaxy collection install ansible.posix community.general 2>/dev/null || true

# Clone and run
WORK_DIR=/opt/ansible-tier0
git clone --branch {config['repo_branch']} {config['repo_url']} "$WORK_DIR" 2>/dev/null || (cd "$WORK_DIR" && git pull)
cd "$WORK_DIR"

NODE_NAME=$(hostname)
NODE_IP=$(ip route get 1 | awk '{{print $7; exit}}')
cat > inventory_local.yml << INVEOF
all:
  children:
    storage_servers:
      hosts:
        ${{NODE_NAME}}:
          ansible_connection: local
          ansible_python_interpreter: /usr/bin/python3
    di_nodes:
      hosts:
        ${{NODE_NAME}}:
          ansible_connection: local
          ansible_python_interpreter: /usr/bin/python3
          di_node_name: "${{NODE_NAME}}-mover"
          di_node_ip: "${{NODE_IP}}"
INVEOF

cat > vars/vault.yml << VEOF
vault_hammerspace_api_password: "$HAMMERSPACE_API_PASSWORD"
VEOF
chmod 600 vars/vault.yml

sed -i "s|^hammerspace_api_host:.*|hammerspace_api_host: \\"$HAMMERSPACE_API_HOST\\"|" vars/main.yml
sed -i "s|^deploy_di:.*|deploy_di: $DEPLOY_DI|" vars/main.yml

ansible-playbook site.yml -i inventory_local.yml --connection local
echo "=== Tier 0 Bootstrap COMPLETE: $(date -u) ==="
"""

    def send_command(self, payload: Any) -> str:
        """Send a Run Command and return the command OCID."""
        response = self.agent_client.create_instance_agent_command(payload)
        return response.data.id

    def poll_command(self, instance_id: str, command_id: str,
                     timeout: int = 3600, interval: int = 30) -> CommandResult:
        """Poll a Run Command until completion."""
        instance_name = ""
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                return CommandResult(
                    instance_id=instance_id,
                    instance_name=instance_name,
                    command_id=command_id,
                    status="TIMED_OUT",
                    error=f"Exceeded {timeout}s timeout"
                )

            try:
                resp = self.agent_client.get_instance_agent_command_execution(
                    instance_agent_command_id=command_id,
                    instance_id=instance_id
                )
                execution = resp.data
                instance_name = execution.display_name or ""

                if execution.lifecycle_state in ("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELED"):
                    output = ""
                    try:
                        output = execution.content.text if execution.content else ""
                    except Exception:
                        pass

                    return CommandResult(
                        instance_id=instance_id,
                        instance_name=instance_name,
                        command_id=command_id,
                        status=execution.lifecycle_state,
                        output=output
                    )

            except oci.exceptions.ServiceError as e:
                if e.status != 404:  # 404 = command not yet picked up
                    return CommandResult(
                        instance_id=instance_id,
                        instance_name=instance_name,
                        command_id=command_id,
                        status="FAILED",
                        error=f"API error: {e.message}"
                    )

            time.sleep(interval)

    def deploy_single(self, instance: InstanceInfo, deploy_mode: str,
                       config: dict) -> CommandResult:
        """Deploy to a single instance: send command + poll."""
        try:
            payload = self.build_command_payload(instance, deploy_mode, config)
            command_id = self.send_command(payload)
            print(f"  [{instance.display_name}] Command sent: {command_id[:30]}...")
            result = self.poll_command(
                instance.id, command_id,
                timeout=config.get("timeout", 3600),
                interval=config.get("poll_interval", 30)
            )
            result.instance_name = instance.display_name
            status_icon = "OK" if result.status == "SUCCEEDED" else "FAIL"
            print(f"  [{instance.display_name}] {status_icon}: {result.status}")
            return result
        except Exception as e:
            print(f"  [{instance.display_name}] ERROR: {e}")
            return CommandResult(
                instance_id=instance.id,
                instance_name=instance.display_name,
                command_id="",
                status="FAILED",
                error=str(e)
            )

    def deploy_batch(self, instances: List[InstanceInfo], deploy_mode: str,
                      config: dict, parallel: int = 5) -> DeploymentReport:
        """Deploy to multiple instances in parallel."""
        report = DeploymentReport(total=len(instances))

        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(self.deploy_single, inst, deploy_mode, config): inst
                for inst in instances
            }

            for future in as_completed(futures):
                result = future.result()
                report.results.append(result)
                if result.status == "SUCCEEDED":
                    report.succeeded += 1
                elif result.status == "TIMED_OUT":
                    report.timed_out += 1
                else:
                    report.failed += 1

        return report


# =============================================================================
# CLI
# =============================================================================

def resolve_hs_password(args) -> Optional[str]:
    """Resolve Hammerspace password from CLI args / env / prompt."""
    if getattr(args, 'hs_password', None):
        return args.hs_password
    if getattr(args, 'hs_password_file', None):
        return Path(args.hs_password_file).read_text().strip()
    if os.environ.get('HAMMERSPACE_PASSWORD'):
        return os.environ['HAMMERSPACE_PASSWORD']
    if getattr(args, 'hs_host', None):
        return getpass.getpass('Hammerspace API password: ')
    return None


def print_instance_table(instances: List[InstanceInfo]) -> None:
    """Print a formatted table of discovered instances."""
    if not instances:
        print("  (no instances found)")
        return

    # Column widths
    name_w = max(len(i.display_name) for i in instances)
    name_w = max(name_w, 12)
    ip_w = max(len(i.private_ip) for i in instances)
    ip_w = max(ip_w, 10)

    header = f"  {'Name':<{name_w}}  {'Private IP':<{ip_w}}  {'Shape':<25}  {'Fault Domain':<18}  {'Agent':<7}  {'HS Reg':<6}"
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for i in instances:
        agent = "Ready" if i.agent_ready else "N/A"
        hs = "Yes" if i.hs_registered else "No"
        print(f"  {i.display_name:<{name_w}}  {i.private_ip:<{ip_w}}  {i.shape:<25}  {i.fault_domain:<18}  {agent:<7}  {hs:<6}")


def print_report(report: DeploymentReport) -> None:
    """Print deployment summary."""
    print()
    print("=" * 60)
    print("DEPLOYMENT SUMMARY")
    print("=" * 60)
    print(f"  Total:      {report.total}")
    print(f"  Succeeded:  {report.succeeded}")
    print(f"  Failed:     {report.failed}")
    print(f"  Timed out:  {report.timed_out}")
    print(f"  Skipped:    {report.skipped}")
    print()

    if report.failed > 0 or report.timed_out > 0:
        print("FAILURES:")
        for r in report.results:
            if r.status != "SUCCEEDED":
                print(f"  [{r.instance_name}] {r.status}")
                if r.error:
                    print(f"    Error: {r.error}")
        print()

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Deploy Hammerspace Tier 0 / DI to OCI instances via Run Command (no SSH)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover instances (dry run)
  python3 oci_deploy.py --compartment-id <OCID> --dry-run

  # Deploy with OCI Vault for credentials
  python3 oci_deploy.py --compartment-id <OCID> --vault-secret-id <SECRET_OCID> --yes

  # Deploy to specific instances
  python3 oci_deploy.py --compartment-id <OCID> --instance-id <OCID1> --instance-id <OCID2>

  # Skip already-registered instances
  python3 oci_deploy.py --compartment-id <OCID> --hs-host 10.0.10.15 --skip-registered
        """
    )

    # OCI connection
    parser.add_argument('--compartment-id', required=True, help='OCI compartment OCID')
    parser.add_argument('--oci-profile', default='DEFAULT', help='OCI config profile (default: DEFAULT)')
    parser.add_argument('--region', help='Override OCI region')

    # Instance selection
    parser.add_argument('--shape', default=None,
                        help='Filter by instance shape (e.g., BM.GPU.GB200-v3.4)')
    parser.add_argument('--name-pattern', help='Filter by display name regex')
    parser.add_argument('--instance-id', action='append', dest='instance_ids',
                        metavar='OCID', help='Target specific instance (repeatable)')

    # Deploy mode
    parser.add_argument('--mode', choices=['tier0', 'di', 'both'], default='both',
                        help='What to deploy (default: both)')

    # Hammerspace connection (for pre-check)
    parser.add_argument('--hs-host', help='Hammerspace Anvil IP (for registration check)')
    parser.add_argument('--hs-port', type=int, default=8443, help='Hammerspace API port')
    parser.add_argument('--hs-user', default='admin', help='Hammerspace API user')
    parser.add_argument('--hs-password', help='Hammerspace API password')
    parser.add_argument('--hs-password-file', help='Path to Hammerspace password file')
    parser.add_argument('--skip-registered', action='store_true',
                        help='Skip instances already registered in Hammerspace')

    # Credentials delivery
    parser.add_argument('--vault-secret-id', help='OCI Vault secret OCID for Hammerspace password')

    # Repo
    parser.add_argument('--repo-url', default='https://github.com/BeratUlualan/Ansible-Tier0.git',
                        help='Git repo URL')
    parser.add_argument('--repo-branch', default='main', help='Git branch (default: main)')

    # Execution
    parser.add_argument('--parallel', type=int, default=5, help='Max concurrent deployments (default: 5)')
    parser.add_argument('--timeout', type=int, default=3600, help='Per-instance timeout in seconds (default: 3600)')
    parser.add_argument('--poll-interval', type=int, default=30, help='Status check interval (default: 30s)')

    # Safety
    parser.add_argument('--dry-run', action='store_true', help='Show plan without executing')
    parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    # --- Resolve Hammerspace password ---
    hs_password = resolve_hs_password(args)

    # --- Validate credentials delivery ---
    if not args.vault_secret_id and not hs_password:
        print("WARNING: No --vault-secret-id or Hammerspace password provided.")
        print("  Instances will use default password from tier0-bootstrap.sh.")
        print("  For production, use --vault-secret-id <OCI_SECRET_OCID>")
        print()

    # --- Initialize deployer ---
    print(f"Initializing OCI client (compartment: {args.compartment_id[:30]}...)")
    deployer = OCIRunCommandDeployer(
        compartment_id=args.compartment_id,
        config_profile=args.oci_profile,
        region=args.region
    )

    # --- Discover instances ---
    print("Discovering instances...")
    instances = deployer.discover_instances(
        shape_filter=args.shape,
        name_pattern=args.name_pattern,
        instance_ids=args.instance_ids
    )
    print(f"  Found {len(instances)} instance(s)")

    if not instances:
        print("No matching instances found. Check --compartment-id, --shape, --name-pattern filters.")
        sys.exit(2)

    # --- Check Cloud Agent status ---
    print("Checking Oracle Cloud Agent status...")
    deployer.check_agent_status(instances)
    ready = [i for i in instances if i.agent_ready]
    not_ready = [i for i in instances if not i.agent_ready]

    if not_ready:
        print(f"  WARNING: {len(not_ready)} instance(s) have Run Command agent not ready:")
        for i in not_ready:
            print(f"    - {i.display_name}")

    # --- Hammerspace registration check ---
    if args.hs_host and hs_password:
        if not requests:
            print("  WARNING: 'requests' module not available, skipping Hammerspace check")
        else:
            print(f"Checking Hammerspace registration at {args.hs_host}...")
            hs_client = HammerspaceClient(args.hs_host, args.hs_user, hs_password, port=args.hs_port)
            registered, unregistered = deployer.check_hammerspace_registration(ready, hs_client)
            print(f"  Already registered: {len(registered)}")
            print(f"  New (unregistered): {len(unregistered)}")

            if args.skip_registered:
                ready = unregistered
                print(f"  Targeting {len(ready)} unregistered instance(s)")

    # --- Build target list (only agent-ready instances) ---
    targets = [i for i in ready if i.agent_ready]

    if not targets:
        print("No deployable instances (all skipped or agent not ready).")
        sys.exit(2)

    # --- Display plan ---
    print()
    print("=" * 60)
    print("DEPLOYMENT PLAN")
    print("=" * 60)
    print(f"  Mode:        {args.mode}")
    print(f"  Targets:     {len(targets)}")
    print(f"  Parallel:    {args.parallel}")
    print(f"  Timeout:     {args.timeout}s")
    print(f"  Credentials: {'OCI Vault' if args.vault_secret_id else 'env/inline'}")
    print(f"  Repo:        {args.repo_url} ({args.repo_branch})")
    print()
    print_instance_table(targets)
    print()

    if args.dry_run:
        print("[DRY RUN] No commands sent.")
        sys.exit(0)

    # --- Confirm ---
    if not args.yes:
        answer = input(f"Deploy to {len(targets)} instance(s)? [y/N]: ").strip().lower()
        if answer != 'y':
            print("Aborted.")
            sys.exit(0)

    # --- Build config ---
    config = {
        "repo_url": args.repo_url,
        "repo_branch": args.repo_branch,
        "hs_host": args.hs_host or "10.0.10.15",
        "hs_user": args.hs_user,
        "hs_password": hs_password or "changeme",
        "vault_secret_id": args.vault_secret_id,
        "timeout": args.timeout,
        "poll_interval": args.poll_interval,
    }

    # --- Deploy ---
    print()
    print(f"Deploying to {len(targets)} instance(s) (parallel={args.parallel})...")
    report = deployer.deploy_batch(targets, args.mode, config, parallel=args.parallel)

    # --- Report ---
    print_report(report)

    sys.exit(0 if report.failed == 0 and report.timed_out == 0 else 1)


if __name__ == "__main__":
    main()
