# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

# integration endpoints
output "requires" {
  description = "Map of all \"requires\" endpoints"
  value = {
    peer_certificates   = "peer-certificates"
    client_certificates = "client-certificates"
    client_cas          = "client-cas"
  }
}

output "provides" {
  description = "Map of all \"provides\" endpoints"
  value = {
    etcd_client = "etcd-client"
    cos_agent   = "cos-agent"
  }
}

output "app_names" {
  description = "Output of all deployed application names."
  value = {
    etcd                     = juju_application.etcd.name
    self-signed-certificates = juju_application.self-signed-certificates.name
  }
}
