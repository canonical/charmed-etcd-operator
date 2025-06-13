# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.


# integration endpoints
output "requires" {
  description = "Map of all \"requires\" endpoints: enppoint-name: interface-name"
  value = {
    peer-certificates   = "tls-certificates"
    client-certificates = "tls-certificates"
    client-cas          = "certificate_transfer"
  }
}

output "provides" {
  description = "Map of all \"provides\" endpoints: enppoint-name: interface-name"
  value = {
    etcd-client = "etcd_client"
    cos-agent   = "cos_agent"
  }
}

output "app_names" {
  description = "Output of all deployed application names."
  value = merge(
    module.etcd.app_names,
    {
      "data-integrator" : juju_application.data-integrator.name,
      "grafana-agent" : juju_application.grafana-agent.name,
      "backups-integrator" : juju_application.backups-integrator.name
    }
  )
}
