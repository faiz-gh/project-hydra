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
    # base64gzip, NOT base64encode. Linode caps user_data at 16384 bytes
    # *decoded*, and bootstrap-patroni.tftpl renders to ~18.9 kB, so a plain
    # base64 of it is rejected at create time with
    #   [400] [metadata.user_data] decoded user_data must not exceed 16384 bytes
    # and the two Linode nodes never come up. Only Linode binds here: GCP allows
    # 256 kB and Azure's custom_data 64 kB, which is why this surfaced as two
    # failed instances in an otherwise clean apply, and only on the PostgreSQL
    # path (bootstrap-cockroachdb.tftpl is ~6.5 kB and fits either way).
    #
    # base64gzip sends the same script gzip-compressed: 7244 bytes on the wire,
    # 56% of the budget still free. cloud-init detects the gzip magic bytes and
    # decompresses before executing, so the script that runs is byte-identical
    # to what the other providers get -- this is a transport encoding, not a
    # different bootstrap.
    #
    # The alternative was to delete comments until it fit. That was rejected:
    # comments are 62% of this template and they are where the reasons live
    # (why the config path is config.yml, why the heredocs are quoted, why the
    # primary is pinned). Compressing costs nothing and keeps them.
    user_data = base64gzip(templatefile(var.database_engine == "cockroachdb" ? "${path.root}/scripts/bootstrap-cockroachdb.tftpl" : "${path.root}/scripts/bootstrap-patroni.tftpl", {
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
