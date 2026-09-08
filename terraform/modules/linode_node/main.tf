terraform {
  required_providers {
    linode = { source = "linode/linode" }
  }
}

resource "linode_instance" "this" {
  label           = var.config.hostname
  image           = "linode/ubuntu22.04"
  region          = var.config.region
  type            = var.config.type
  authorized_keys = [var.ssh_public_key]

  metadata {
    user_data = base64encode(templatefile(var.database_engine == "cockroachdb" ? "${path.root}/scripts/bootstrap-cockroachdb.tftpl" : "${path.root}/scripts/bootstrap-patroni.tftpl", {
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
    label    = "allow-ssh"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "22"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  inbound {
    label    = "allow-tailscale"
    action   = "ACCEPT"
    protocol = "UDP"
    ports    = "41641"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  outbound {
    label    = "allow-tailscale-outbound"
    action   = "ACCEPT"
    protocol = "UDP"
    ports    = "1-65535"
    ipv4     = ["0.0.0.0/0"]
    ipv6     = ["::/0"]
  }

  inbound_policy  = "DROP"
  outbound_policy = "ACCEPT"
  linodes         = [linode_instance.this.id]
}
