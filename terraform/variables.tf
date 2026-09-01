variable "tailscale_auth_key" {
  type        = string
  description = "Tailscale auth key for mesh network"
  sensitive   = true
}

variable "ssh_public_key" {
  type        = string
  description = "Public SSH key for instance access"
}

variable "azure_subscription_id" {
  type        = string
  description = "Azure Subscription ID"
}

variable "gcp_project_id" {
  type        = string
  description = "GCP Project ID"
}

variable "cluster_join_nodes" {
  type        = string
  description = "Comma-separated list of hostnames for CockroachDB to join"
}

variable "linode_config" {
  description = "Linode Configuration and Toggles"
  type = object({
    nodes = map(object({
      enabled  = bool
      region   = string
      type     = string
      hostname = string
    }))
  })
}

variable "azure_config" {
  description = "Azure Configuration and Toggles"
  type = object({
    nodes = map(object({
      enabled     = bool
      region      = string
      vnet_cidr   = string
      subnet_cidr = string
      vm_size     = string
      hostname    = string
    }))
  })
}

variable "gcp_config" {
  description = "GCP Configuration and Toggles"
  type = object({
    nodes = map(object({
      enabled      = bool
      region       = string
      zone         = string
      vpc_cidr     = string
      machine_type = string
      hostname     = string
    }))
  })
}

variable "local_config" {
  description = "Local GCP Configuration and Toggles"
  type = object({
    nodes = map(object({
      enabled      = bool
      region       = string
      zone         = string
      vpc_cidr     = string
      machine_type = string
      hostname     = string
    }))
  })
}
