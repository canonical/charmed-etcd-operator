## Charmed etcd operator
[![CharmHub Badge](https://charmhub.io/charmed-etcd/badge.svg)](https://charmhub.io/charmed-etcd)

The Charmed etcd Operator deploys and operates the [etcd](https://etcd.io) 
software on VMs and machine clusters. This charm is a 
Python project that installs etcd from the [charmed-etcd snap](https://snapcraft.io/charmed-etcd),
and provides life cycle management and event handling.

Charmed etcd is equipped with several features to securely store and scale complicated data workloads,
including TLS encryption, horizontal scaling, password rotation, and easy integration with client applications.

## Basic usage

Bootstrap a [lxd controller](https://juju.is/docs/olm/lxd#heading--create-a-controller) and create a new Juju model:

```shell
juju add-model sample-model
```

To deploy a single unit of charmed etcd, run the following command:

```shell
juju deploy charmed-etcd --channel 3.6/edge
```

To deploy charmed etcd with multiple units, specify the number of desired units with the `-n` option:

```shell
juju deploy charmed-etcd --channel 3.6/edge -n 3
```

Charmed etcd can be scaled out using the `juju add-unit` command:

```shell
juju add-unit charmed-etcd -n <num_of_desired_units>
```

For example, to scale a deployment with three etcd units to five, run:

```shell
juju add-unit charmed-etcd -n 2
```

Even when scaling multiple units at the same time, the charmed operator uses a rolling restart 
sequence to make sure the cluster stays available and healthy during the operation.

## Download details

Charmed etcd is shipped in the track `3.6/beta`: [Charmed etcd 3.6](https://charmhub.io/charmed-etcd?channel=3.6/beta)

It is based on the following platform:
- Noble (Ubuntu 24.04)
- Supported architectures: `amd64` and `arm64`.

## Documentation

The [charmed etcd documentation](https://canonical-charmed-etcd.readthedocs-hosted.com) provides a 
tutorial for basic usage, multiple how-to guides about operational topics, and detailed 
information about supported interfaces and integrations.

## Community and support

The charmed etcd operator is an open-source project that welcomes community contributions, suggestions,
fixes and constructive feedback.

- Report [issues](https://github.com/canonical/charmed-etcd-operator/issues)
- [Contact us on Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com)
- Explore [Canonical Data & AI solutions](https://canonical.com/data)

Charmed etcd is covered by the [Ubuntu Code of
Conduct](https://ubuntu.com/community/ethos/code-of-conduct).

## Contributing

Please see the [Juju docs](https://documentation.ubuntu.com/juju/3.6/howto/manage-applications/) for 
guidelines and best practices, and the [contribution guide](CONTRIBUTING.md) for developer guidance.

## License and copyright

Charmed etcd is free software, distributed under the Apache Software License, version 2.0. See [LICENSE](LICENSE) for more information.
