# Set up the environment

In this step, we will set up a development environment with the required components for deploying Charmed etcd.

## Summary
* [Set up LXD](#set-up-lxd)
* [Set up Juju](#set-up-juju)

---

## Set up LXD

The simplest way to get started with charmed etcd is to set up a local LXD cloud.
[LXD](https://documentation.ubuntu.com/lxd/en/latest/) is a system container and
virtual machine manager that comes pre-installed on Ubuntu. Juju interfaces with
LXD to control the containers on which charmed etcd runs.

Verify if your Ubuntu system already has LXD installed with the command `which lxd`.
If there is no output, then install LXD with

```shell
sudo snap install lxd
```

After installation, `lxd init` is run to perform post-installation tasks. For this
tutorial, the default parameters are preferred and the network bridge should be set
to have no IPv6 addresses since Juju does not support IPv6 addresses with LXD:

```shell
lxd init --auto
lxc network set lxdbr0 ipv6.address none
```

You can list all LXD containers by executing the command `lxc list`. At this point
in the tutorial, none should exist, so you'll only see this as output:

```shell
+------+-------+------+------+------+-----------+
| NAME | STATE | IPV4 | IPV6 | TYPE | SNAPSHOTS |
+------+-------+------+------+------+-----------+
```

## Set up Juju

[Juju](https://juju.is/docs/juju) is an Operator Lifecycle Manager (OLM) for clouds,
bare metal, LXD or Kubernetes. We will be using it to deploy and manage charmed etcd. 

As with LXD, Juju is installed using a snap package:

```shell
sudo snap install juju --channel 3.6/stable --classic
```

Juju already has a built-in knowledge of LXD and how it works, so there is no
additional setup or configuration needed, however,  because Juju 3.x is a
[strictly confined snap](https://snapcraft.io/docs/classic-confinement), 
and is not allowed to create a `~/.local/share` directory, we need to create it
manually.

```shell
mkdir -p ~/.local/share
```

To list the clouds available to Juju, run the following command:

```shell
juju clouds
```

The output will look as follows:

```shell
Clouds available on the client:
Cloud      Regions  Default    Type  Credentials  Source    Description
localhost  1        localhost  lxd   1            built-in  LXD Container Hypervisor
```

Notice that Juju already has a built-in knowledge of LXD and how it works,
so there is no need for additional setup. A controller will be used to deploy
and control charmed etcd. 

Run the following command to bootstrap a Juju controller named `dev-controller` on LXD:

```shell
juju bootstrap localhost dev-controller
```

This bootstrapping process can take several minutes depending on your system
resources. The Juju controller exists within an LXD container. You can verify
this by entering the command `lxc list`.

This will output the following:

```shell
+---------------+---------+-----------------------+------+-----------+-----------+
|     NAME      |  STATE  |         IPV4          | IPV6 |   TYPE    | SNAPSHOTS |
+---------------+---------+-----------------------+------+-----------+-----------+
| juju-<id>     | RUNNING | 10.86.196.118 (eth0)  |      | CONTAINER | 0         |
+---------------+---------+-----------------------+------+-----------+-----------+
```

where `<id>` is a unique combination of numbers and letters such as `9d7e4e-0`

Set up a unique model for this tutorial named `etcd`:

```shell
juju add-model etcd
```

You can now view the model you created above by entering the command `juju status`
into the command line. You should see the following:

```shell
juju status
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  17:26:15Z
```