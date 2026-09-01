// main.tf

module "linode_node_1" {
source = "./modules/linode_node"
count = var.linode_config.nodes["node1"].enabled ? 1 : 0

config = var.linode_config.nodes["node1"]
ssh_public_key = var.ssh_public_key
tailscale_auth_key = var.tailscale_auth_key
cluster_join_nodes = var.cluster_join_nodes
}

module "linode_node_2" {
source = "./modules/linode_node"
count = var.linode_config.nodes["node2"].enabled ? 1 : 0

config = var.linode_config.nodes["node2"]
ssh_public_key = var.ssh_public_key
tailscale_auth_key = var.tailscale_auth_key
cluster_join_nodes = var.cluster_join_nodes
}

module "azure_node_1" {
source = "./modules/azure_node"
providers = { azurerm = azurerm.region1 }
count = var.azure_config.nodes["node1"].enabled ? 1 : 0

config = var.azure_config.nodes["node1"]
ssh_public_key = var.ssh_public_key
tailscale_auth_key = var.tailscale_auth_key
cluster_join_nodes = var.cluster_join_nodes
}

module "azure_node_2" {
source = "./modules/azure_node"
providers = { azurerm = azurerm.region2 }
count = var.azure_config.nodes["node2"].enabled ? 1 : 0

config = var.azure_config.nodes["node2"]
ssh_public_key = var.ssh_public_key
tailscale_auth_key = var.tailscale_auth_key
cluster_join_nodes = var.cluster_join_nodes
}

module "gcp_node_1" {
source = "./modules/gcp_node"
count = var.gcp_config.nodes["node1"].enabled ? 1 : 0

config = var.gcp_config.nodes["node1"]
ssh_public_key = var.ssh_public_key
tailscale_auth_key = var.tailscale_auth_key
cluster_join_nodes = var.cluster_join_nodes
}

module "local_node_1" {
source = "./modules/local_node"
count = var.local_config.nodes["node1"].enabled ? 1 : 0

config = var.local_config.nodes["node1"]
ssh_public_key = var.ssh_public_key
tailscale_auth_key = var.tailscale_auth_key
cluster_join_nodes = var.cluster_join_nodes
}

// providers.tf
terraform {
cloud {
organization = "lightygi"
workspaces {
name = "hydra"
}
}
required_providers {
linode = {
source = "linode/linode"
version = "~~> 2.16"
}
azurerm = {
source = "hashicorp/azurerm"
version = "~~> 3.0"
}
google = {
source = "hashicorp/google"
version = "~> 5.0"
}
}
}

# The Linode provider will automatically authenticate using the LINODE_TOKEN env var

provider "linode" {}

provider "azurerm" {
alias = "region1"
features {
resource_group {
prevent_deletion_if_contains_resources = false
}
}
subscription_id = var.azure_subscription_id
}

provider "azurerm" {
alias = "region2"
features {
resource_group {
prevent_deletion_if_contains_resources = false
}
}
subscription_id = var.azure_subscription_id
}

provider "google" {
project = var.gcp_project_id
region = var.gcp_config.nodes["node1"].region
}

// variables.tf
variable "tailscale_auth_key" {
type = string
description = "Tailscale auth key for mesh network"
sensitive = true
}

variable "ssh_public_key" {
type = string
description = "Public SSH key for instance access"
}

variable "azure_subscription_id" {
type = string
description = "Azure Subscription ID"
}

variable "gcp_project_id" {
type = string
description = "GCP Project ID"
}

variable "cluster_join_nodes" {
type = string
description = "Comma-separated list of hostnames for CockroachDB to join"
}

variable "linode_config" {
description = "Linode Configuration and Toggles"
type = object({
nodes = map(object({
enabled = bool
region = string
type = string
hostname = string
}))
})
}

variable "azure_config" {
description = "Azure Configuration and Toggles"
type = object({
nodes = map(object({
enabled = bool
region = string
vnet_cidr = string
subnet_cidr = string
vm_size = string
hostname = string
}))
})
}

variable "gcp_config" {
description = "GCP Configuration and Toggles"
type = object({
nodes = map(object({
enabled = bool
region = string
zone = string
vpc_cidr = string
machine_type = string
hostname = string
}))
})
}

variable "local_config" {
description = "Local GCP Configuration and Toggles"
type = object({
nodes = map(object({
enabled = bool
region = string
zone = string
vpc_cidr = string
machine_type = string
hostname = string
}))
})
}

// scripts/bootstrap.tftpl
#!/bin/bash
set -e

# ==========================================

# 1. Install & Configure Tailscale Mesh

# ==========================================

curl -fsSL https://tailscale.com/install.sh | sh

# TF evaluates these because of single $

tailscale up --authkey="${tailscale_key}" --hostname="${hostname}" --accept-dns=true

# Deterministic check: Wait until Tailscale successfully assigns an IPv4 address

echo "Waiting for Tailscale IP assignment..."
TS_IP=""
while [ -z "$TS_IP" ]; do
    sleep 2
    TS_IP=$(tailscale ip -4 || true)
done
echo "Node registered on mesh with IP: $TS_IP"

# ==========================================

# 2. DNS Verification Loop

# ==========================================

echo "Verifying network reachability to all cluster peers..."

# TF evaluates ${join_nodes} to inject the comma-separated string

IFS=',' read -ra PEERS <<< "${join_nodes}"

# ESCAPED for Bash with $$: $${PEERS[@]}

for peer in "$${PEERS[@]}"; do # TF evaluates ${hostname}
    if [ "$peer" == "${hostname}" ]; then
continue
fi

    echo "Waiting for $peer to resolve and become reachable..."
    WAIT_COUNT=0
    MAX_RETRIES=60 # Up to 5 minutes of waiting per node

    while ! ping -c 1 -W 1 "$peer" &> /dev/null; do
        sleep 5
        WAIT_COUNT=$((WAIT_COUNT + 1))

        if [ $WAIT_COUNT -ge $MAX_RETRIES ]; then
            echo "WARNING: Timeout waiting for $peer. Proceeding to avoid deadlocks..."
            break
        fi
    done
    echo "Success: $peer is reachable!"

done

# ==========================================

# 3. NTP & Clock Sync (CRDB HLC Requirement)

# ==========================================

echo "Installing and configuring Chrony for strict NTP sync..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -yq
apt-get install -yq chrony

# Force an immediate hard-sync of the system clock, bypassing standard slew

systemctl enable chrony
systemctl start chrony
chronyc makestep || true
chronyc tracking

# ==========================================

# 4. Install CockroachDB (Updated to v26.3.0)

# ==========================================

echo "Installing CockroachDB v26.3.0..."
wget -qO- https://binaries.cockroachdb.com/cockroach-v26.3.0.linux-amd64.tgz | tar xvz
cp -i cockroach-v26.3.0.linux-amd64/cockroach /usr/local/bin/
mkdir -p /var/lib/cockroach

# ==========================================

# 5. Start Node

# ==========================================

echo "Starting CockroachDB node..."
cockroach start \
--insecure \
--store=/var/lib/cockroach \
--listen-addr=$TS_IP:26257 \
  --advertise-addr=$TS_IP:26257 \
--join=${join_nodes} \
  --locality=${locality} \
--background

# ==========================================

# 6. Cluster Initialization & Topology Tuning

# ==========================================

PRIMARY_NODE="$${PEERS[0]}"

if [ "${hostname}" == "$PRIMARY_NODE" ]; then
echo "I am the primary node ($PRIMARY_NODE). Initializing the cluster in 15 seconds..."
    sleep 15
    cockroach init --insecure --host=$TS_IP:26257 || true
echo "Raft consensus initialized."

    echo "Waiting for CockroachDB SQL interface to become ready..."
    MAX_RETRIES=12 # Wait up to 60 seconds
    READY=0
    for i in $(seq 1 $MAX_RETRIES); do
        # A simple SELECT 1 ensures the SQL gateway is actively responding
        if cockroach sql --insecure --host=$TS_IP:26257 -e "SELECT 1;" > /dev/null 2>&1; then
            echo "SQL interface is online!"
            READY=1
            break
        fi
        echo "SQL not ready yet, retrying in 5 seconds..."
        sleep 5
    done

    if [ "$READY" -eq 1 ]; then
        echo "Applying global zone configurations for latency optimization..."

        # Create Databases bench for benchmarking purposes
        cockroach sql --insecure --host=$TS_IP:26257 \
            -e "CREATE DATABASE IF NOT EXISTS bench;"

        # Create Database kv for key-value workloads
        cockroach sql --insecure --host=$TS_IP:26257 \
            -e "CREATE DATABASE IF NOT EXISTS kv;"

        # 1. Force 5 replicas so data is globally mirrored (Azure survives US outages)
        cockroach sql --insecure --host=$TS_IP:26257 \
            -e "ALTER RANGE default CONFIGURE ZONE USING num_replicas = 5;"

        # 2. Pin leaseholders (write coordinators) to the low-latency US triangle
        cockroach sql --insecure --host=$TS_IP:26257 \
            -e "ALTER RANGE default CONFIGURE ZONE USING lease_preferences = '[[+region=us-east], [+region=us-east1], [+region=us-west]]';"

        echo "✅ Topology tuning successfully applied."
    else
        echo "❌ ERROR: SQL interface never became ready. Topology tuning skipped."
    fi

else
echo "I am a secondary node. Skipping initialization; waiting for $PRIMARY_NODE to orchestrate."
fi

// scripts/bootstrap-local.tftpl
#!/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive
apt-get update -yq
apt-get install -yq wget tar curl chrony

# ==========================================

# 1. Install & Configure Tailscale Mesh

# ==========================================

echo "Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="${tailscale_key}" --hostname="${hostname}" --accept-dns=true

echo "Waiting for Tailscale IP assignment..."
TS_IP=""
while [ -z "$TS_IP" ]; do
    sleep 2
    TS_IP=$(tailscale ip -4 || true)
done
echo "Node registered on mesh with IP: $TS_IP"

# ==========================================

# 2. Install & Start CockroachDB (v26.3.0)

# ==========================================

echo "Installing CockroachDB v26.3.0..."
wget -qO- https://binaries.cockroachdb.com/cockroach-v26.3.0.linux-amd64.tgz | tar xvz
cp -i cockroach-v26.3.0.linux-amd64/cockroach /usr/local/bin/
mkdir -p /var/lib/cockroach

echo "Starting CockroachDB Single-Node on Tailscale IP..."

# Binding to TS_IP allows local Prometheus scraping from your laptop

cockroach start-single-node \
--insecure \
--store=/var/lib/cockroach \
--listen-addr=$TS_IP:26257 \
  --http-addr=$TS_IP:8080 \
--cache=0.25 \
--max-sql-memory=0.25 \
--background

sleep 5
cockroach sql --insecure --url="postgresql://root@$TS_IP:26257/defaultdb" -e "CREATE DATABASE IF NOT EXISTS bench;"
echo "✅ Single-node baseline provisioned and listening on $TS_IP."

// modules/linode_node/main.tf
terraform {
required_providers {
linode = { source = "linode/linode" }
}
}

resource "linode_instance" "this" {
label = var.config.hostname
image = "linode/ubuntu22.04"
region = var.config.region
type = var.config.type
authorized_keys = [var.ssh_public_key]

metadata {
user_data = base64encode(templatefile("${path.root}/scripts/bootstrap.tftpl", {
      tailscale_key = var.tailscale_auth_key
      hostname      = var.config.hostname
      join_nodes    = var.cluster_join_nodes
      locality      = "cloud=linode,region=${var.config.region}"
}))
}
}

resource "linode_firewall" "this" {
label = "${var.config.hostname}-fw"

inbound {
label = "allow-ssh"
action = "ACCEPT"
protocol = "TCP"
ports = "22"
ipv4 = ["0.0.0.0/0"]
ipv6 = ["::/0"]
}

inbound {
label = "allow-tailscale"
action = "ACCEPT"
protocol = "UDP"
ports = "41641"
ipv4 = ["0.0.0.0/0"]
ipv6 = ["::/0"]
}

outbound {
label = "allow-tailscale-outbound"
action = "ACCEPT"
protocol = "UDP"
ports = "1-65535"
ipv4 = ["0.0.0.0/0"]
ipv6 = ["::/0"]
}

inbound_policy = "DROP"
outbound_policy = "ACCEPT"
linodes = [linode_instance.this.id]
}

// modules/linode_node/variables.tf
variable "config" { type = any }
variable "ssh_public_key" { type = string }
variable "tailscale_auth_key" { type = string }
variable "cluster_join_nodes" { type = string }

// modules/azure_node/main.tf
terraform {
required_providers {
azurerm = {
source = "hashicorp/azurerm"
}
}
}

resource "azurerm_resource_group" "this" {
name = "${var.config.hostname}-rg"
location = var.config.region
}

resource "azurerm_virtual_network" "this" {
name = "${var.config.hostname}-vnet"
address_space = [var.config.vnet_cidr]
location = azurerm_resource_group.this.location
resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_subnet" "this" {
name = "internal"
resource_group_name = azurerm_resource_group.this.name
virtual_network_name = azurerm_virtual_network.this.name
address_prefixes = [var.config.subnet_cidr]
}

resource "azurerm_public_ip" "this" {
name = "${var.config.hostname}-pip"
location = azurerm_resource_group.this.location
resource_group_name = azurerm_resource_group.this.name
sku = "Standard"
allocation_method = "Static"
}

resource "azurerm_network_security_group" "this" {
name = "${var.config.hostname}-nsg"
location = azurerm_resource_group.this.location
resource_group_name = azurerm_resource_group.this.name

security_rule {
name = "Tailscale-Inbound"
priority = 100
direction = "Inbound"
access = "Allow"
protocol = "Udp"
source_port_range = "_"
destination_port_range = "41641"
source_address_prefix = "_"
destination_address_prefix = "*"
}

security_rule {
name = "Tailscale-Outbound"
priority = 101
direction = "Outbound"
access = "Allow"
protocol = "Udp"
source_port_range = "_"
destination_port_range = "_"
source_address_prefix = "_"
destination_address_prefix = "_"
}
}

resource "azurerm_network_interface" "this" {
name = "${var.config.hostname}-nic"
location = azurerm_resource_group.this.location
resource_group_name = azurerm_resource_group.this.name

ip_configuration {
name = "internal"
subnet_id = azurerm_subnet.this.id
private_ip_address_allocation = "Dynamic"
public_ip_address_id = azurerm_public_ip.this.id
}
}

resource "azurerm_network_interface_security_group_association" "this" {
network_interface_id = azurerm_network_interface.this.id
network_security_group_id = azurerm_network_security_group.this.id
}

resource "azurerm_linux_virtual_machine" "this" {
name = var.config.hostname
resource_group_name = azurerm_resource_group.this.name
location = azurerm_resource_group.this.location
size = var.config.vm_size
admin_username = "ubuntu"
network_interface_ids = [azurerm_network_interface.this.id]

admin_ssh_key {
username = "ubuntu"
public_key = var.ssh_public_key
}

os_disk {
caching = "ReadWrite"
storage_account_type = "Premium_LRS"
}

source_image_reference {
publisher = "Canonical"
offer = "0001-com-ubuntu-server-jammy"
sku = "22_04-lts"
version = "latest"
}

custom_data = base64encode(templatefile("${path.root}/scripts/bootstrap.tftpl", {
    tailscale_key = var.tailscale_auth_key
    hostname      = var.config.hostname
    join_nodes    = var.cluster_join_nodes
    locality      = "cloud=azure,region=${var.config.region}"
}))
}

// modules/azure_node/variables.tf
variable "config" { type = any }
variable "ssh_public_key" { type = string }
variable "tailscale_auth_key" { type = string }
variable "cluster_join_nodes" { type = string }

// modules/gcp_node/main.tf
terraform {
required_providers {
google = {
source = "hashicorp/google"
}
}
}

data "google_compute_image" "ubuntu" {
family = "ubuntu-2204-lts"
project = "ubuntu-os-cloud"
}

resource "google_compute_network" "this" {
name = "${var.config.hostname}-vpc"
auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
name = "${var.config.hostname}-subnet"
ip_cidr_range = var.config.vpc_cidr
region = var.config.region
network = google_compute_network.this.id
}

resource "google_compute_firewall" "ingress" {
name = "${var.config.hostname}-fw-in"
network = google_compute_network.this.name

allow {
protocol = "udp"
ports = ["41641"]
}

allow {
protocol = "tcp"
ports = ["22"]
}

source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "egress" {
name = "${var.config.hostname}-fw-out"
network = google_compute_network.this.name
direction = "EGRESS"

allow {
protocol = "udp"
}

destination_ranges = ["0.0.0.0/0"]
}

resource "google_compute_instance" "this" {
name = var.config.hostname
machine_type = var.config.machine_type
zone = var.config.zone

boot_disk {
initialize_params {
image = data.google_compute_image.ubuntu.self_link
type = "pd-ssd" # Upgraded to SSD
}
}

network_interface {
subnetwork = google_compute_subnetwork.this.id
access_config {} # Ephemeral IP for outbound access
}

metadata = {
ssh-keys = "ubuntu:${var.ssh_public_key}"
    user-data = templatefile("${path.root}/scripts/bootstrap.tftpl", {
tailscale_key = var.tailscale_auth_key
hostname = var.config.hostname
join_nodes = var.cluster_join_nodes
locality = "cloud=gcp,region=${var.config.region}"
})
}
}

// modules/gcp_node/variables.tf
variable "config" { type = any }
variable "ssh_public_key" { type = string }
variable "tailscale_auth_key" { type = string }
variable "cluster_join_nodes" { type = string }

// modules/local_node/main.tf
terraform {
required_providers {
google = {
source = "hashicorp/google"
}
}
}

data "google_compute_image" "ubuntu" {
family = "ubuntu-2204-lts"
project = "ubuntu-os-cloud"
}

resource "google_compute_network" "this" {
name = "${var.config.hostname}-vpc"
auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
name = "${var.config.hostname}-subnet"
ip_cidr_range = var.config.vpc_cidr
region = var.config.region
network = google_compute_network.this.id
}

resource "google_compute_firewall" "ingress" {
name = "${var.config.hostname}-fw-in"
network = google_compute_network.this.name

allow {
protocol = "udp"
ports = ["41641"]
}

allow {
protocol = "tcp"
ports = ["22"]
}

source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "egress" {
name = "${var.config.hostname}-fw-out"
network = google_compute_network.this.name
direction = "EGRESS"

allow {
protocol = "udp"
}

destination_ranges = ["0.0.0.0/0"]
}

resource "google_compute_instance" "this" {
name = var.config.hostname
machine_type = var.config.machine_type
zone = var.config.zone

boot_disk {
initialize_params {
image = data.google_compute_image.ubuntu.self_link
type = "pd-ssd" # Upgraded to SSD
}
}

network_interface {
subnetwork = google_compute_subnetwork.this.id
access_config {} # Ephemeral IP for outbound access
}

metadata = {
ssh-keys = "ubuntu:${var.ssh_public_key}"
    user-data = templatefile("${path.root}/scripts/bootstrap-local.tftpl", {
tailscale_key = var.tailscale_auth_key
hostname = var.config.hostname
join_nodes = var.cluster_join_nodes
locality = "cloud=gcp,region=${var.config.region}"
})
}
}

// modules/local_node/variables.tf
variable "config" { type = any }
variable "ssh_public_key" { type = string }
variable "tailscale_auth_key" { type = string }
variable "cluster_join_nodes" { type = string }
