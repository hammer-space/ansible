# =============================================================================
# Terraform Example: OCI GPU Instance with Tier 0 Auto-Provisioning
# =============================================================================
# This instance self-configures on first boot via cloud-init:
#   - Installs Ansible
#   - Clones the Ansible-Tier0 repo
#   - Runs the playbook locally (no SSH needed)
#
# Usage:
#   terraform init
#   terraform plan -var="hammerspace_api_password=YourPassword"
#   terraform apply -var="hammerspace_api_password=YourPassword"
# =============================================================================

variable "compartment_id" {
  description = "OCI compartment OCID"
  type        = string
}

variable "subnet_id" {
  description = "Subnet OCID for the instance"
  type        = string
}

variable "availability_domain" {
  description = "Availability domain (e.g., 'Uocm:US-SANJOSE-1-AD-1')"
  type        = string
}

variable "shape" {
  description = "Instance shape"
  type        = string
  default     = "BM.GPU.GB200-v3.4"
}

variable "image_id" {
  description = "OS image OCID (Rocky Linux 9, Ubuntu 22.04, etc.)"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key (still required by OCI, but not used for Ansible)"
  type        = string
  default     = ""
}

variable "hammerspace_api_host" {
  description = "Hammerspace Anvil management IP"
  type        = string
}

variable "hammerspace_api_password" {
  description = "Hammerspace API password"
  type        = string
  sensitive   = true
}

variable "deploy_di" {
  description = "Deploy DI on this instance (true/false)"
  type        = string
  default     = "false"
}

variable "ansible_repo_url" {
  description = "Git repo URL for Ansible-Tier0"
  type        = string
  default     = "https://github.com/BeratUlualan/Ansible-Tier0.git"
}

variable "instance_count" {
  description = "Number of Tier 0 instances to provision"
  type        = number
  default     = 1
}

# --- Cloud-init user data ---------------------------------------------------

locals {
  cloud_init = templatefile("${path.module}/tier0-cloud-config.yaml.tftpl", {
    ansible_repo_url         = var.ansible_repo_url
    hammerspace_api_host     = var.hammerspace_api_host
    hammerspace_api_password = var.hammerspace_api_password
    deploy_di                = var.deploy_di
  })
}

# --- OCI Instance ------------------------------------------------------------

resource "oci_core_instance" "tier0" {
  count               = var.instance_count
  compartment_id      = var.compartment_id
  availability_domain = var.availability_domain
  display_name        = "tier0-node-${format("%02d", count.index + 1)}"
  shape               = var.shape

  source_details {
    source_type = "image"
    source_id   = var.image_id
  }

  create_vnic_details {
    subnet_id        = var.subnet_id
    assign_public_ip = false
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = base64encode(local.cloud_init)
  }

  # Preserve boot volume on termination for debugging
  preserve_boot_volume = false
}

output "instance_private_ips" {
  value = oci_core_instance.tier0[*].private_ip
}

output "instance_names" {
  value = oci_core_instance.tier0[*].display_name
}
