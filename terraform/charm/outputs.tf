# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

# integration endpoints
output "requires" {
  description = "Map of all \"requires\" endpoints"
  value = {
    peer-certificates   = "tls-certificates"
    client-certificates = "tls-certificates"
    client-cas          = "certificate_transfer"
  }
}

output "provides" {
  description = "Map of all \"provides\" endpoints"
  value = {
    etcd-client = "etcd_client"
    cos-agent   = "cos_agent"
  }
}

output "app_names" {
  description = "Output of all deployed application names."
  value = {
    etcd                     = juju_application.etcd.name
    self-signed-certificates = juju_application.self-signed-certificates.name
  }
}
