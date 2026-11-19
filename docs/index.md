---
relatedlinks: "[Charmhub](https://charmhub.io/charmed-etcd)"
myst:
  html_meta:
    description: "Official documentation for the Charmed etcd operator. Deploy and manage etcd on VMs using Juju."
---

# Charmed etcd documentation

Etcd is a distributed, reliable key-value store for the most critical data of distributed systems. This charmed operator deploys and operates etcd on virtual machines.

Charmed etcd is equipped with several features to securely store and scale complicated data workloads, including TLS encryption, horizontal scaling, password rotation, and easy integration with client applications.

## In this documentation

This documentation contains practical information about installing and operate Charmed etcd on VM.

* **Get started**: {ref}`Guided tutorial <tutorial>` • {ref}`migrate-to-charmed-etcd`
* **Lifecycle & operations**: {ref}`tune-settings` • {ref}`scale-horizontally` • {ref}`refresh`
* **Storage & data protection**: {ref}`manage-persistent-storage` • {ref}`configure-object-storage-provider` • {ref}`create-backup`
* **Security & access**: {ref}`client-relations` • {ref}`enable-tls` • {ref}`manage-passwords` • {ref}`cryptography_page`
* **Troubleshooting**: {ref}`enable-monitoring` • {ref}`advanced-statuses` • {ref}`disaster-recovery`

## How this documentation is organised

This documentation uses the [Diátaxis documentation structure](https://diataxis.fr/):

* The {ref}`tutorial` provides step-by-step guidance for a beginner through the basics of a deployment in a local machine.
* {ref}`how-to` are more focused, and assume you already have basic familiarity with the product.
* {ref}`reference` contains structured information for quick lookup, such as system requirements and configuration parameters
* {ref}`explanation` gives more background and context about key topics

## Project and community

Charmed etcd is an open-source project that welcomes community contributions, suggestions, fixes and constructive feedback.

### Get involved

* [Discourse forum](https://discourse.charmhub.io/tag/etcd)
* [Public Matrix channel](https://matrix.to/#/#charmhub-data-platform:ubuntu.com)
* [Report an issue](https://github.com/canonical/charmed-etcd-operator/issues/new/choose)
* [Contribute](https://github.com/canonical/charmed-etcd-operator/blob/main/CONTRIBUTING.md)

### Governance and policies

* [Code of Conduct](https://ubuntu.com/community/code-of-conduct)

```{toctree}
:titlesonly:
:hidden:

Home <self>
/tutorial/index
/how-to/index
/reference/index
/explanation/index
```
