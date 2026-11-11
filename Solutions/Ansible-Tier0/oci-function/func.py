"""
OCI Function: Auto-deploy Tier 0 + DI on new GPU instances.

Triggered by OCI Events when an instance reaches RUNNING state.
Sends an OCI Run Command to the instance to execute the bootstrap script.
Fire-and-forget — the ansible run happens on the instance, not in this function.

Environment variables (set in Function Application config):
    COMPARTMENT_ID     — OCI compartment for Run Command scope
    VAULT_SECRET_ID    — OCI Vault secret OCID with Hammerspace API password
    SHAPE_FILTER       — Comma-separated allowed shapes (empty = all)
    DEPLOY_MODE        — tier0 / di / both (default: both)
    REPO_URL           — Ansible repo URL
    REPO_BRANCH        — Git branch (default: main)
    HS_HOST            — Hammerspace Anvil IP
    HS_USER            — Hammerspace API user (default: admin)
"""

import io
import json
import logging
import os
import sys
import time

import fdk.response
from fdk import context

# Add the function directory to path so we can import oci_deploy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oci
from oci_deploy import OCIRunCommandDeployer, InstanceInfo

logger = logging.getLogger("tier0-auto-deploy")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def get_config():
    """Read configuration from function environment variables."""
    return {
        "compartment_id": os.environ.get("COMPARTMENT_ID", ""),
        "vault_secret_id": os.environ.get("VAULT_SECRET_ID", ""),
        "shape_filter": os.environ.get("SHAPE_FILTER", ""),
        "deploy_mode": os.environ.get("DEPLOY_MODE", "both"),
        "repo_url": os.environ.get("REPO_URL", "https://github.com/BeratUlualan/Ansible-Tier0.git"),
        "repo_branch": os.environ.get("REPO_BRANCH", "main"),
        "hs_host": os.environ.get("HS_HOST", "10.0.10.15"),
        "hs_user": os.environ.get("HS_USER", "admin"),
        "hs_password": "",  # Never store password in env — use VAULT_SECRET_ID
        "timeout": int(os.environ.get("COMMAND_TIMEOUT", "3600")),
        "poll_interval": 30,
    }


def parse_event(data):
    """Extract instance info from OCI Event payload."""
    try:
        body = json.loads(data.getvalue())
    except Exception:
        return None, None, None

    event_type = body.get("eventType", "")
    resource_id = body.get("data", {}).get("resourceId", "")
    compartment_id = body.get("data", {}).get("compartmentId", "")
    shape = body.get("data", {}).get("additionalDetails", {}).get("shape", "")
    instance_name = body.get("data", {}).get("additionalDetails", {}).get("instanceName", "")

    return {
        "event_type": event_type,
        "instance_id": resource_id,
        "compartment_id": compartment_id,
        "shape": shape,
        "instance_name": instance_name,
    }, resource_id, compartment_id


def wait_for_agent(deployer, instance_id, compartment_id, max_retries=10, delay=15):
    """Wait for the Run Command agent plugin to be ready."""
    for attempt in range(max_retries):
        try:
            plugins = deployer.compute_client.list_instance_agent_plugins(
                instanceagent_id=instance_id,
                compartment_id=compartment_id
            ).data
            for plugin in plugins:
                if plugin.name == "Run Command" and plugin.status == "RUNNING":
                    return True
        except oci.exceptions.ServiceError:
            pass

        if attempt < max_retries - 1:
            logger.info(f"Agent not ready, retry {attempt + 1}/{max_retries} in {delay}s...")
            time.sleep(delay)

    return False


def handler(ctx: context.InvokeContext, data: io.BytesIO = None):
    """OCI Function entry point — triggered by OCI Events."""

    # Parse event
    event_info, instance_id, event_compartment = parse_event(data)
    if not event_info or not instance_id:
        return fdk.response.Response(
            ctx, response_data=json.dumps({"status": "ERROR", "message": "Invalid event payload"}),
            headers={"Content-Type": "application/json"}, status_code=400
        )

    logger.info(f"Event: {event_info['event_type']} for {event_info['instance_name']} ({instance_id})")

    # Load config
    config = get_config()
    compartment_id = config["compartment_id"] or event_compartment

    if not compartment_id:
        return fdk.response.Response(
            ctx, response_data=json.dumps({"status": "ERROR", "message": "COMPARTMENT_ID not set"}),
            headers={"Content-Type": "application/json"}, status_code=500
        )

    # Shape filter
    if config["shape_filter"]:
        allowed_shapes = [s.strip() for s in config["shape_filter"].split(",")]
        if event_info["shape"] and event_info["shape"] not in allowed_shapes:
            msg = f"Skipped: shape {event_info['shape']} not in filter {allowed_shapes}"
            logger.info(msg)
            return fdk.response.Response(
                ctx, response_data=json.dumps({"status": "SKIPPED_SHAPE", "message": msg}),
                headers={"Content-Type": "application/json"}
            )

    # Initialize deployer with Resource Principal
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
    except Exception as e:
        return fdk.response.Response(
            ctx, response_data=json.dumps({"status": "ERROR", "message": f"Resource Principal auth failed: {e}"}),
            headers={"Content-Type": "application/json"}, status_code=500
        )

    deployer = OCIRunCommandDeployer(compartment_id=compartment_id, signer=signer)

    # Verify instance is RUNNING
    try:
        inst_resp = deployer.compute_client.get_instance(instance_id)
        inst = inst_resp.data
        if inst.lifecycle_state != "RUNNING":
            msg = f"Instance {inst.display_name} is {inst.lifecycle_state}, not RUNNING"
            logger.warning(msg)
            return fdk.response.Response(
                ctx, response_data=json.dumps({"status": "SKIPPED_NOT_RUNNING", "message": msg}),
                headers={"Content-Type": "application/json"}
            )
    except oci.exceptions.ServiceError as e:
        return fdk.response.Response(
            ctx, response_data=json.dumps({"status": "ERROR", "message": f"Cannot get instance: {e.message}"}),
            headers={"Content-Type": "application/json"}, status_code=500
        )

    # Wait for Cloud Agent
    logger.info(f"Waiting for Run Command agent on {inst.display_name}...")
    if not wait_for_agent(deployer, instance_id, compartment_id):
        msg = f"Run Command agent not ready on {inst.display_name} after retries"
        logger.warning(msg)
        return fdk.response.Response(
            ctx, response_data=json.dumps({"status": "SKIPPED_AGENT_NOT_READY", "message": msg}),
            headers={"Content-Type": "application/json"}
        )

    # Build instance info
    private_ip = deployer._get_private_ip(instance_id, compartment_id)
    instance_info = InstanceInfo(
        id=instance_id,
        display_name=inst.display_name,
        shape=inst.shape,
        fault_domain=inst.fault_domain or "",
        private_ip=private_ip,
        lifecycle_state=inst.lifecycle_state,
        agent_ready=True
    )

    # Build and send Run Command (fire-and-forget)
    try:
        payload = deployer.build_command_payload(instance_info, config["deploy_mode"], config)
        command_id = deployer.send_command(payload)
        logger.info(f"Run Command sent to {inst.display_name}: {command_id}")

        result = {
            "status": "COMMAND_SENT",
            "instance_id": instance_id,
            "instance_name": inst.display_name,
            "command_id": command_id,
            "deploy_mode": config["deploy_mode"],
        }
        return fdk.response.Response(
            ctx, response_data=json.dumps(result),
            headers={"Content-Type": "application/json"}
        )

    except Exception as e:
        logger.error(f"Failed to send Run Command to {inst.display_name}: {e}")
        return fdk.response.Response(
            ctx, response_data=json.dumps({"status": "ERROR", "message": str(e)}),
            headers={"Content-Type": "application/json"}, status_code=500
        )
