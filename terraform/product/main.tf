# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

#--------------------------------------------------------
# 1. DEPLOYMENTS
#--------------------------------------------------------

module "etcd" {
  source = "../charm"

  channel  = var.etcd.channel
  revision = var.etcd.revision
  base     = var.etcd.base

  app_name          = var.etcd.app_name
  units             = var.etcd.units
  config            = var.etcd.config
  model             = var.etcd.model
  constraints       = var.etcd.constraints
  storage           = var.etcd.storage
  endpoint_bindings = var.etcd.endpoint_bindings
  machines          = var.etcd.machines
  expose            = var.etcd.expose

  self-signed-certificates = var.self-signed-certificates
}

resource "juju_application" "data-integrator" {
  charm {
    name     = "data-integrator"
    channel  = var.data-integrator.channel
    revision = var.data-integrator.revision
    base     = var.data-integrator.base
  }
  model  = var.etcd.model
  config = tomap(var.data-integrator.config)

  constraints = var.data-integrator.constraints
}

resource "juju_application" "grafana-agent" {
  charm {
    name     = "grafana-agent"
    channel  = var.grafana-agent.channel
    revision = var.grafana-agent.revision
    base     = var.grafana-agent.base
  }
  model  = var.etcd.model
  config = var.grafana-agent.config
}


#--------------------------------------------------------
# 2. INTEGRATIONS
#--------------------------------------------------------


# Integrator apps and grafana-agent
resource "juju_integration" "data_integrator-etcd-integration" {
  model = var.etcd.model

  application {
    name = juju_application.data-integrator.name
  }

  application {
    name = var.etcd.app_name
  }

  depends_on = [
    module.etcd,
    juju_application.data-integrator,
  ]
}


resource "juju_integration" "grafana_agent-etcd" {
  model = var.etcd.model

  application {
    name = juju_application.grafana-agent.name
  }

  application {
    name = var.etcd.app_name
  }

  depends_on = [
    module.etcd,
    juju_application.grafana-agent,
  ]
}
