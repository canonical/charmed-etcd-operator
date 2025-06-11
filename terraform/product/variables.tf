# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "etcd" {
  description = "etcd app definition"
  type = object({
    app_name          = optional(string, "etcd")
    model             = string
    base              = optional(string, "ubuntu@24.04")
    config            = optional(map(string), {})
    channel           = optional(string, "3.5/edge")
    revision          = optional(string, null)
    units             = optional(number, 3)
    constraints       = optional(string, "arch=amd64")
    machines          = optional(set(string), null)
    storage           = optional(map(string), {})
    endpoint_bindings = optional(map(string), {})
    expose            = optional(bool, false)
  })
}

variable "self-signed-certificates" {
  description = "self-signed-certificates app definition"
  type = object({
    channel     = optional(string, "1/stable")
    revision    = optional(string, null)
    base        = optional(string, "ubuntu@24.04")
    constraints = optional(string, "arch=amd64")
    machines    = optional(set(string), [])
    config      = optional(map(string), { "ca-common-name" : "CA" })
  })
  default = {}

  validation {
    condition     = length(var.self-signed-certificates.machines) <= 1
    error_message = "Machine count should be at most 1"
  }
}

variable "grafana-agent" {
  description = "Configuration for the grafana-agent"
  type = object({
    channel     = optional(string, "latest/stable")
    revision    = optional(string, null)
    base        = optional(string, "ubuntu@24.04")
    constraints = optional(string, "arch=amd64")
    config      = optional(map(string), {})
  })
  default = {}
}

variable "data-integrator" {
  description = "Configuration for the data-integrator"
  type = object({
    config      = optional(map(string), { "prefix-name" : "/test/" })
    channel     = optional(string, "latest/edge")
    base        = optional(string, "ubuntu@24.04")
    revision    = optional(string, null)
    constraints = optional(string, "arch=amd64")
    machines    = optional(set(string), [])
  })

  validation {
    condition     = length(var.data-integrator.machines) <= 1
    error_message = "Machine count should be at most 1"
  }

  default = {}
}

variable "backups-integrator" {
  description = "Configuration for the backup integrator"
  type = object({
    storage_type = optional(string, "s3")
    config       = map(string)
    channel      = optional(string, "latest/edge")
    base         = optional(string, "ubuntu@22.04")
    revision     = optional(string, null)
    constraints  = optional(string, "arch=amd64")
    machines     = optional(set(string), [])
  })

  validation {
    condition     = contains(["s3", "azure-storage"], var.backups-integrator.storage_type)
    error_message = "backup-integrator allows one of the values: 's3', 'azure' for storage_type."
  }

  validation {
    condition     = length(var.backups-integrator.machines) <= 1
    error_message = "Machine count should be at most 1"
  }
}
