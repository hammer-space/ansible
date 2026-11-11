# =============================================================================
# OCI Events + Functions: Auto-Deploy Tier 0 + DI
# =============================================================================
# When a GPU instance reaches RUNNING, an event triggers this function to
# send an OCI Run Command that bootstraps Tier 0 and DI automatically.
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply
# =============================================================================

terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 5.0.0"
    }
  }
}

locals {
  event_compartment_id = var.event_compartment_id != "" ? var.event_compartment_id : var.compartment_id
}

# --- Dynamic Group for the Function -----------------------------------------

resource "oci_identity_dynamic_group" "tier0_functions" {
  compartment_id = var.compartment_id
  name           = "tier0-auto-deploy-functions"
  description    = "Dynamic group for Tier 0 auto-deploy OCI Function"
  matching_rule  = "ALL {resource.type = 'fnfunc', resource.compartment.id = '${var.compartment_id}'}"
}

# --- IAM Policies ------------------------------------------------------------

resource "oci_identity_policy" "tier0_function_policies" {
  compartment_id = var.compartment_id
  name           = "tier0-auto-deploy-policy"
  description    = "Policies for Tier 0 auto-deploy function"
  statements = [
    # Read instance details
    "Allow dynamic-group ${oci_identity_dynamic_group.tier0_functions.name} to inspect instances in compartment id ${var.compartment_id}",

    # Send Run Commands
    "Allow dynamic-group ${oci_identity_dynamic_group.tier0_functions.name} to use instance-agent-command-family in compartment id ${var.compartment_id}",

    # Check Cloud Agent plugin status
    "Allow dynamic-group ${oci_identity_dynamic_group.tier0_functions.name} to inspect instance-agent-plugins in compartment id ${var.compartment_id}",

    # Read VNIC details (for private IP lookup)
    "Allow dynamic-group ${oci_identity_dynamic_group.tier0_functions.name} to inspect vnics in compartment id ${var.compartment_id}",
    "Allow dynamic-group ${oci_identity_dynamic_group.tier0_functions.name} to inspect vnic-attachments in compartment id ${var.compartment_id}",

    # Read Vault secrets
    "Allow dynamic-group ${oci_identity_dynamic_group.tier0_functions.name} to read secret-bundles in compartment id ${var.compartment_id}",

    # Events service can invoke functions
    "Allow service cloudevents to use functions-family in compartment id ${var.compartment_id}",
  ]
}

# --- Function Application ----------------------------------------------------

resource "oci_functions_application" "tier0_app" {
  compartment_id = var.compartment_id
  display_name   = "tier0-auto-deploy"
  subnet_ids     = [var.subnet_id]

  config = {
    COMPARTMENT_ID  = var.compartment_id
    VAULT_SECRET_ID = var.vault_secret_id
    SHAPE_FILTER    = var.shape_filter
    DEPLOY_MODE     = var.deploy_mode
    REPO_URL        = var.repo_url
    REPO_BRANCH     = var.repo_branch
    HS_HOST         = var.hs_host
    HS_USER         = var.hs_user
    LOG_LEVEL       = "INFO"
  }
}

# --- Function ----------------------------------------------------------------

resource "oci_functions_function" "tier0_deploy" {
  application_id = oci_functions_application.tier0_app.id
  display_name   = "tier0-auto-deploy"
  image          = var.function_image
  memory_in_mbs  = 256
  timeout_in_seconds = 300
}

# --- Event Rule ---------------------------------------------------------------

resource "oci_events_rule" "instance_launch" {
  compartment_id = local.event_compartment_id
  display_name   = "tier0-auto-deploy-on-launch"
  description    = "Trigger Tier 0 + DI deployment when a GPU instance reaches RUNNING"
  is_enabled     = true

  condition = jsonencode({
    eventType = ["com.oraclecloud.computeapi.launchinstance.end"]
    data = {
      compartmentId = [local.event_compartment_id]
    }
  })

  actions {
    actions {
      action_type = "FAAS"
      function_id = oci_functions_function.tier0_deploy.id
      is_enabled  = true
    }
  }
}
