terraform {
  cloud {
    organization = "lightygi"
    workspaces {
      name = "hydra"
    }
  }
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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

provider "aws" {
  alias  = "region1"
  region = var.aws_config.nodes["node1"].region
}

provider "aws" {
  alias  = "region2"
  region = var.aws_config.nodes["node2"].region
}

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
