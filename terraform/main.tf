module "linode_node_1" {
  source = "./modules/linode_node"
  count  = var.linode_config.nodes["node1"].enabled ? 1 : 0

  config             = var.linode_config.nodes["node1"]
  ssh_public_key     = var.ssh_public_key
  tailscale_auth_key = var.tailscale_auth_key
  cluster_join_nodes = var.cluster_join_nodes
  database_engine    = var.database_engine
}

module "linode_node_2" {
  source = "./modules/linode_node"
  count  = var.linode_config.nodes["node2"].enabled ? 1 : 0

  config             = var.linode_config.nodes["node2"]
  ssh_public_key     = var.ssh_public_key
  tailscale_auth_key = var.tailscale_auth_key
  cluster_join_nodes = var.cluster_join_nodes
  database_engine    = var.database_engine
}

module "azure_node_1" {
  source    = "./modules/azure_node"
  providers = { azurerm = azurerm.region1 }
  count     = var.azure_config.nodes["node1"].enabled ? 1 : 0

  config             = var.azure_config.nodes["node1"]
  ssh_public_key     = var.ssh_public_key
  tailscale_auth_key = var.tailscale_auth_key
  cluster_join_nodes = var.cluster_join_nodes
  database_engine    = var.database_engine
}

module "azure_node_2" {
  source    = "./modules/azure_node"
  providers = { azurerm = azurerm.region2 }
  count     = var.azure_config.nodes["node2"].enabled ? 1 : 0

  config             = var.azure_config.nodes["node2"]
  ssh_public_key     = var.ssh_public_key
  tailscale_auth_key = var.tailscale_auth_key
  cluster_join_nodes = var.cluster_join_nodes
  database_engine    = var.database_engine
}

module "gcp_node_1" {
  source = "./modules/gcp_node"
  count  = var.gcp_config.nodes["node1"].enabled ? 1 : 0

  config             = var.gcp_config.nodes["node1"]
  ssh_public_key     = var.ssh_public_key
  tailscale_auth_key = var.tailscale_auth_key
  cluster_join_nodes = var.cluster_join_nodes
  database_engine    = var.database_engine
}

module "client_node_1" {
  source = "./modules/client_node"
  count  = var.client_config.nodes["node1"].enabled ? 1 : 0

  config             = var.client_config.nodes["node1"]
  ssh_public_key     = var.ssh_public_key
  tailscale_auth_key = var.tailscale_auth_key
  cluster_join_nodes = var.cluster_join_nodes
  database_engine    = var.database_engine
}
