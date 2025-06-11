# Cryptography

This document describes the cryptography used by Charmed etcd.

## Resource checksums

Charmed etcd uses pinned versions of the Charmed etcd snap to provide reproducible and secure environments.

The snap packages its workload along with the necessary dependencies and utilities required for the operators’ lifecycle. For more details, see the snaps content in the `snapcraft.yaml` file for [charmed etcd](https://github.com/canonical/charmed-etcd-snap/blob/3.5/edge/snap/snapcraft.yaml).

Every artifact bundled into a snap is verified against its MD5, SHA256, or SHA512 checksum after download. The installation of certified snap into the rock is ensured by snap primitives that verify their squashfs filesystems images GPG signature. For more information on the snap verification process, refer to the [snapcraft.io documentation](https://snapcraft.io/docs/assertions).

## Sources verification

etcd is built by Canonical from upstream source codes on [Launchpad](https://launchpad.net/etcd).

The Charmed etcd charm and snap are published and released programmatically using release pipelines implemented via GitHub Actions in their respective repositories.

All repositories in GitHub are set up with branch protection rules, requiring:

* new commits to be merged to main branches via pull request with at least 2 approvals from repository maintainers
* new commits to be signed (e.g. using GPG keys)
* developers to sign the [Canonical Contributor License Agreement (CLA)](https://ubuntu.com/legal/contributors)

## Encryption

Charmed etcd can be used to deploy a secure etcd cluster that provides encryption-in-transit capabilities out of the box for:

* Cluster internal communications
* External clients connections

To set up a secure connection Charmed etcd need to be integrated with TLS Certificate Provider charms, e.g. self-signed-certificates operator. Certificate Signing Requests (CSRs) are generated for every unit using the tls_certificates_interface library that uses the cryptography Python library to create X.509 compatible certificates. The CSR is signed by the TLS Certificate Provider, returned to the units, and stored in Juju secret. The relation also provides the CA certificate, which is loaded into Juju secret.

Encryption at rest is currently not supported, although it can be provided by the substrate (cloud or on-premises).

## Authentication

In Charmed etcd, authentication layers can be enabled for:

1. Admin authentication to etcd, always enabled.
2. etcd cluster authentication, enabled by TLS certificate-based authentication.
3. Clients authentication to etcd, enabled by TLS certificate-based authentication.

### Admin authentication to etcd

Authentication of the admin user to etcd is based on the `bcrypt` go module. See the [etcd official documentation](https://etcd.io/docs/v3.5/learning/design-auth-v3/) for more details.

Credentials are exchanged via [Juju secrets](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-secrets/).

### etcd cluster authentication

Authentication among members of a etcd cluster is based on the TLS certificate-based authentication method. Each unit in the cluster gets issued a TLS certificate signed by the CA of the TLS Certificate Provider charm. The certificate is used to authenticate the unit to other members of the cluster.

### Clients authentication to etcd

Authentication of clients to etcd is based on the TLS certificate-based authentication method. Each client provides a TLS certificate they use to authenticate themselves to the etcd cluster. The certificate is stored on a local trusted certificate store, which is used to verify whether the client is allowed to access the etcd cluster.
