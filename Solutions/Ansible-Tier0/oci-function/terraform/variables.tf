variable "compartment_id" {
  description = "OCI compartment OCID"
  type        = string
}

variable "vcn_id" {
  description = "VCN OCID for the Function Application"
  type        = string
}

variable "subnet_id" {
  description = "Subnet OCID for the Function Application"
  type        = string
}

variable "function_image" {
  description = "OCIR image path (e.g., us-sanjose-1.ocir.io/tenancy/repo/tier0-auto-deploy:latest)"
  type        = string
}

variable "vault_secret_id" {
  description = "OCI Vault secret OCID containing Hammerspace API password"
  type        = string
}

variable "shape_filter" {
  description = "Comma-separated instance shapes to trigger on (empty = all)"
  type        = string
  default     = "BM.GPU.GB200-v3.4"
}

variable "deploy_mode" {
  description = "What to deploy: tier0, di, or both"
  type        = string
  default     = "both"
}

variable "repo_url" {
  description = "Ansible-Tier0 git repo URL"
  type        = string
  default     = "https://github.com/BeratUlualan/Ansible-Tier0.git"
}

variable "repo_branch" {
  description = "Git branch"
  type        = string
  default     = "main"
}

variable "hs_host" {
  description = "Hammerspace Anvil management IP"
  type        = string
}

variable "hs_user" {
  description = "Hammerspace API username"
  type        = string
  default     = "admin"
}

variable "event_compartment_id" {
  description = "Compartment to monitor for instance events (defaults to compartment_id)"
  type        = string
  default     = ""
}
