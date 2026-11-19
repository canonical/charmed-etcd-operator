# Terraform product module for charmed-etcd

This is a Terraform module facilitating the deployment of the etcd charm with [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs). 

## Requirements
This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

## API

### Inputs
The module offers the following configurable inputs:

| Name                       | Type                                                                                                                                                                    | Description                              | Required |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | -------- |
| `etcd`                     | object <br/>(structure as defined in etcd input variables)                                                                                                              | `etcd` application                       | **True** |
| `backups-integrator`       | object <br/>(structure as defined in the `azure-storage-integrator`/`s3-integrator` charms, with the addition of an attribute: <br/>- `storage_type` = "s3" or "azure") | Backup (s3/azure) integrator application | False    |
| `data-integrator`          | object <br/>(structure as defined in the `data-integrator` charm)                                                                                                       | `data-integrator` application            | False    |
| `self-signed-certificates` | object <br/>(structure as defined in the self-signed-certificates charm)                                                                                                | `self-signed-certificates` application   | False    |
| `grafana-agent`            | object <br/>(structure as defined in the grafana-agent charm)                                                                                                           | `grafana-agent` application              | False    |


### Outputs
When applied, the module exports the following outputs:

| Name        | Description                       |
| ----------- | --------------------------------- |
| `app_names` | Map of deployed application names |
| `provides`  | Map of `provides` endpoints       |
| `requires`  | Map of `requires` endpoints       |

Example output:
```
app_names = {
  "backups-integrator" = "s3-integrator"
  "data-integrator" = "data-integrator"
  "etcd" = "etcd"
  "grafana-agent" = "grafana-agent"
  "self-signed-certificates" = "self-signed-certificates"
}
provides = {
  "cos_agent" = "cos-agent"
  "etcd_client" = "etcd-client"
}
requires = {
  "client_cas" = "client-cas"
  "client_certificates" = "client-certificates"
  "peer_certificates" = "peer-certificates"
}

```

## Usage

This module is intended to be a product module, deploying all components for a proper etcd deployment.

It may be used as-is and directly as follows:
```
terraform apply \
  -var 'etcd={"model_uuid": "12345678-1234-5678-9012-123456789012"}' 
  -var 'backups-integrator={"config": {"bucket": "test"}}'\
  -out terraform.out
  
tf apply terraform.out
```