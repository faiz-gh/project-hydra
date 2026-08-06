terraform {
  cloud {
    organization = "lightygi"
    workspaces {
      name = "hydra"
    }
  }
  required_providers {
    linode = {
      source  = "linode/linode"
      version = "~> 2.16"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

# The Linode provider will automatically authenticate using the LINODE_TOKEN env var
provider "linode" {}

provider "azurerm" {
  alias = "region1"
  features {}
  subscription_id = var.azure_subscription_id
}

provider "azurerm" {
  alias = "region2"
  features {}
  subscription_id = var.azure_subscription_id
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_config.nodes["node1"].region
}
