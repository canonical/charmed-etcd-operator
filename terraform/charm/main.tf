# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.


#--------------------------------------------------------
# 1. DEPLOYMENTS
#--------------------------------------------------------
resource "juju_application" "etcd" {
  charm {
    name     = "charmed-etcd"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }
  config             = var.config
  model              = var.model
  name               = var.app_name
  units              = var.units
  constraints        = var.constraints
  storage_directives = var.storage
  machines           = var.machines

  dynamic "expose" {
    for_each = var.expose ? [1] : []
    content {}
  }

  endpoint_bindings = [
    for k, v in var.endpoint_bindings : {
      endpoint = k, space = v
    }
  ]
}

resource "juju_application" "self-signed-certificates" {
  for_each = var.tls ? { "deployed" = true } : {}

  model = var.model

  charm {
    name     = "self-signed-certificates"
    channel  = var.self-signed-certificates.channel
    revision = var.self-signed-certificates.revision
    base     = var.self-signed-certificates.base
  }

  config = var.self-signed-certificates.config

  units       = 1
  constraints = var.self-signed-certificates.constraints
}


#--------------------------------------------------------
# 2. INTEGRATIONS
#--------------------------------------------------------

resource "juju_integration" "tls-etcd-peer" {
  # This integration is only created if TLS is enabled
  for_each = var.tls ? { "deployed" = true } : {}

  model = var.model

  application {
    name     = "self-signed-certificates"
    endpoint = "certificates"
  }

  application {
    name     = juju_application.etcd.name
    endpoint = "peer-certificates"
  }

  depends_on = [
    juju_application.self-signed-certificates,
    juju_application.etcd,
  ]
}

resource "juju_integration" "tls-etcd-client" {
  # This integration is only created if TLS is enabled
  for_each = var.tls ? { "deployed" = true } : {}

  model = var.model

  application {
    name     = juju_application.self-signed-certificates["deployed"].name
    endpoint = "certificates"
  }

  application {
    name     = juju_application.etcd.name
    endpoint = "client-certificates"
  }

  depends_on = [
    juju_application.self-signed-certificates,
    juju_application.etcd,
  ]
}
