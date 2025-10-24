# Charmed etcd versions

Charmed etcd is shipped in the following [tracks](https://documentation.ubuntu.com/juju/3.6/reference/charm/#track): 

* [Charmed etcd 3.6](https://charmhub.io/charmed-etcd?channel=3.6/stable) (channel `3.6/stable`)

## etcd 3.6

**Base:** Noble (Ubuntu 24.04)

**Supported architectures:** `amd64` and `arm64`.

### Releases

```{eval-rst}
+--------------+------------+----------+
| Charm        | etcd       | Snap     |
| revision     | Version    | revision |
+==============+============+==========+
| 119 (amd64)  | 3.6.5      | 25       |
+--------------+            +----------+
| 120 (arm64)  |            | 26       |
+--------------+------------+----------+
```            

### Supported features

* Automated deployment on VM
  * [Terraform charm module](https://github.com/canonical/charmed-etcd-operator/blob/3.6/edge/terraform/charm/README.md)
  * [Terraform product module](https://github.com/canonical/charmed-etcd-operator/blob/3.6/edge/terraform/product/README.md)
* [Scaling a cluster up and down](/how-to/scale-horizontally)
* High Availability and automated rolling restarts
* Authentication and authorisation by default
  * Automated user and permission management for client applications
* [Backup and restore](/how-to/backup-and-restore/index.md)
  * Integration with any AWS S3-compatible or Azure object storage
* [TLS encryption](/how-to/tls/index.md)
  * Automated certificate and CA rotation
  * mTLS for client applications
* [Support for client relations](/how-to/client-relations) 
* [Observability with Canonical Observability Stack (COS)](/how-to/enable-monitoring)
* [Persistent Storage](/how-to/manage-persistent-storage)
* [Juju user secrets](https://documentation.ubuntu.com/juju/latest/reference/secret/index.html#user-secret) for charm [internal passwords](/how-to/manage-passwords)
* [Recovery from majority failure](/how-to/disaster-recovery)
* [Tuning for configuration settings](/how-to/tune-settings)
* [Minor version upgrades without downtime](/how-to/refresh)

### Requirements and compatibility

* Juju v3.6.11+ 
  * Older minor versions of Juju 3 may be compatible, but are not officially supported. 
* LXD v6.4+
  * Older LXD versions may be compatible, but are not officially supported. 
* Integration with a TLS provider charm
  * `tls-certificates` interface v4
