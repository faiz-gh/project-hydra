terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}

data "google_compute_image" "ubuntu" {
  family  = "ubuntu-2204-lts"
  project = "ubuntu-os-cloud"
}

resource "google_compute_network" "this" {
  name                    = "${var.config.hostname}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "this" {
  name          = "${var.config.hostname}-subnet"
  ip_cidr_range = var.config.vpc_cidr
  region        = var.config.region
  network       = google_compute_network.this.id
}

resource "google_compute_firewall" "ingress" {
  name    = "${var.config.hostname}-fw-in"
  network = google_compute_network.this.name

  allow {
    protocol = "udp"
    ports    = ["41641"]
  }

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "egress" {
  name      = "${var.config.hostname}-fw-out"
  network   = google_compute_network.this.name
  direction = "EGRESS"

  allow {
    protocol = "udp"
  }

  destination_ranges = ["0.0.0.0/0"]
}

resource "google_compute_instance" "this" {
  name         = var.config.hostname
  machine_type = var.config.machine_type
  zone         = var.config.zone

  boot_disk {
    initialize_params {
      image = data.google_compute_image.ubuntu.self_link
      type  = "pd-ssd" # Upgraded to SSD
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
      hostname      = var.config.hostname
      join_nodes    = var.cluster_join_nodes
      locality      = "cloud=gcp,region=${var.config.region}"
    })
  }
}
