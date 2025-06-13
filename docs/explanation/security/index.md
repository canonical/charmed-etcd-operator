# Security hardening guide

This document provides an overview of security features and guidance for hardening the security of [Charmed etcd](https://charmhub.io/charmed-etcd) deployments, including setting up and managing a secure environment.

## Environment

The environment where Charmed etcd operates can be divided into two components:

1. Cloud
2. Juju

### Cloud

Charmed etcd can be deployed on top of several clouds and virtualisation layers:

| Cloud     | Security guides                                                                                                                                                                                                                                                        |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OpenStack | [OpenStack Security Guide](https://docs.openstack.org/security-guide/)                                                                                                                                                                                                 |
| AWS       | [Best Practices for Security, Identity and Compliance](https://aws.amazon.com/architecture/security-identity-compliance), [AWS security credentials](https://docs.aws.amazon.com/IAM/latest/UserGuide/security-creds.html)                                             |
| Azure     | [Azure security best practices and patterns](https://learn.microsoft.com/en-us/azure/security/fundamentals/best-practices-and-patterns), [Managed identities for Azure resource](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/) |
| GCP       | [Google security overview](https://cloud.google.com/docs/security)                                                                                                                                                                                                     |  |

### Juju 

Juju is the component responsible for orchestrating the entire life cycle, from deployment to Day 2 operations. For more information on Juju security hardening, see the
[Juju security page](https://documentation.ubuntu.com/juju/latest/explanation/juju-security/index.html) and the [How to harden your deployment](https://documentation.ubuntu.com/juju/3.6/howto/manage-your-deployment/#harden-your-deployment) guide.

#### Cloud credentials

When configuring cloud credentials to be used with Juju, ensure that users have the correct permissions to operate at the required level. Juju superusers responsible for bootstrapping and managing controllers require elevated permissions to manage several kinds of resources, such as virtual machines, networks, storage, etc. Please refer to the links below for more information on the policies required to be used depending on the cloud. 

| Cloud     | Cloud user policies                                                                                                                                                                                                                            |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OpenStack | [OpenStack cloud and Juju](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/cloud/list-of-supported-clouds/the-openstack-cloud-and-juju/)                                                                                |
| AWS       | [Juju AWS Permission](https://discourse.charmhub.io/t/juju-aws-permissions/5307), [AWS Instance Profiles](https://discourse.charmhub.io/t/using-aws-instance-profiles-with-juju-2-9/5185), [Juju on AWS](https://juju.is/docs/juju/amazon-ec2) |
| Azure     | [Juju Azure Permission](https://juju.is/docs/juju/microsoft-azure), [How to use Juju with Microsoft Azure](https://discourse.charmhub.io/t/how-to-use-juju-with-microsoft-azure/15219)                                                         |
| GCP       | <spellexception>[Google GCE cloud and Juju](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/cloud/list-of-supported-clouds/the-google-gce-cloud-and-juju/)</spellexception>                                             |

#### Juju users

It is very important that Juju users are set up with minimal permissions depending on the scope of their operations. Please refer to the [User access levels](https://documentation.ubuntu.com/juju/3.6/reference/user/#user-access-levels) documentation for more information on the access levels and corresponding abilities.

Juju user credentials must be stored securely and rotated regularly to limit the chances of unauthorised access due to credentials leakage.

## Applications

In the following, we provide guidance on how to harden your deployment using:

1. Operating system
2. Security upgrades
3. Encryption 
4. Authentication
5. Authorisation
6. Monitoring and auditing

### Operating system

Charmed etcd runs on top of Ubuntu 24.04. Deploy a [Landscape Client Charm](https://charmhub.io/landscape-client?) to connect the underlying VM to a Landscape User Account to manage security upgrades and integrate [Ubuntu Pro](https://ubuntu.com/pro) subscriptions. 

### Security upgrades

`charmed-etcd-operator` uses the `charmed-etcd-snap`, where each revision of the charm pins a revision of the snap to provide reproducible environments.

Currently, the charm is available on the `edge` track, the snap is patched and updated regularly to ensure that the latest security fixes from the upstream etcd project are applied.

<!-- New versions (revisions) of charmed operators can be released to upgrade workloads, the operator's code, or both. It is important to refresh the charm regularly to make sure the workload is as secure as possible.

For more information on upgrading the charm, see the [How to upgrade etcd]() guides, as well as the [Release notes](). -->

### Encryption

By default, encryption is optional for both external connections and internal communication between cluster members. To enforce encryption in transit, integrate Charmed etcd with a TLS certificate provider. Please refer to the [Charming Security page](https://charmhub.io/topics/security-with-x-509-certificates) for more information on how to select the right certificate provider for your use case.

Encryption in transit for backups is provided by the storage (Charmed etcd is a client for the S3 storage).

For more information on encryption, see the [Cryptography](cryptography) explanation page and [How to enable TLS](../../how-to/tls/enable-tls) guide.

### Authentication

etcd saves and checks a configured password and a given password using Go’s [`bcrypt`](https://pkg.go.dev/golang.org/x/crypto/bcrypt) package. 
For client authentication, Charmed etcd relies on TLS client certificate authentication. 

### Authorisation

etcd supports [role-based access control (RBAC)](https://etcd.io/docs/v3.6/op-guide/authentication/rbac/) to restrict access to resources based on the roles assigned to users. Charmed etcd enables this feature by default. it creates a default admin user with full access to the etcd cluster. Additional users are created for each client relation. Charmed etcd creates and assigns a role to each user to restrict its access to only the range of keys specified in the relation through the `prefix` field.

### Monitoring and auditing

Charmed etcd provides native integration with the [Canonical Observability Stack (COS)](https://charmhub.io/topics/canonical-observability-stack). To reduce the blast radius of infrastructure disruptions, the general recommendation is to deploy COS and the observed application into separate environments, isolated from one another. Refer to the [COS production deployments best practices](https://charmhub.io/topics/canonical-observability-stack/reference/best-practices) for more information.

For instructions, see the [How to enable monitoring](../../how-to/enable-monitoring) guide.

Logging is enabled by default. The logs are stored in the `/var/snap/charmed-etcd/common/var/log/etcd` directory of the etcd container. Log rotation is enabled by default. It’s recommended to integrate the charm with [COS](https://discourse.charmhub.io/t/9900), from where the logs can be easily persisted and queried using [Loki](https://charmhub.io/loki-k8s)/[Grafana](https://charmhub.io/grafana).

## Additional Resources

Charmed etcd also implements all CIS hardening checks for etcd as defined in the <spellexception>[Aqua Security kube-bench configuration for CIS 1.24](https://github.com/aquasecurity/kube-bench/blob/main/cfg/cis-1.24/etcd.yaml)</spellexception> to ensure compliance and security best practices.

For details on the cryptography used by Charmed etcd, see the [Cryptography](cryptography) explanation page.


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Cryptography <cryptography>