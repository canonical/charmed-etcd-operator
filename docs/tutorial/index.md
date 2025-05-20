# Tutorial

Whether you have experience with Etcd databases, Juju orchestration, or neither, this tutorial will walk you through the essential aspects of deploying and operating Charmed Etcd.

**What you'll need**

<!--* A computer that fulfils the [minimum system requirements](#system-requirements)-->
* Some experience using a Linux-based CLI
* A basic understanding of the [Juju orchestration engine](https://documentation.ubuntu.com/juju/latest/)

**What you'll do**

* Install and set up LXD and Juju
* Deploy a small Charmed Etcd cluster
* Connect to the cluster through a unit

## Install and initialise LXD

The simplest way to get started with charmed etcd is to set up a local LXD cloud.
[LXD](https://documentation.ubuntu.com/lxd/en/latest/) is a system container and
virtual machine manager that comes pre-installed on Ubuntu. Juju interfaces with
LXD to control the containers on which charmed etcd runs.

Verify if your Ubuntu system already has LXD installed with the command `which lxd`.
If there is no output, then install LXD with

```text
sudo snap install lxd
```

After installation, `lxd init` is run to perform post-installation tasks. For this
tutorial, the default parameters are preferred and the network bridge should be set
to have no IPv6 addresses since Juju does not support IPv6 addresses with LXD:

```text
lxd init --auto
lxc network set lxdbr0 ipv6.address none
```

You can list all LXD containers by executing the command `lxc list`. At this point
in the tutorial, none should exist, so you'll only see this as output:

```text
+------+-------+------+------+------+-----------+
| NAME | STATE | IPV4 | IPV6 | TYPE | SNAPSHOTS |
+------+-------+------+------+------+-----------+
```

## Install and set up Juju

[Juju](https://juju.is/docs/juju) is an Operator Lifecycle Manager (OLM) for clouds,
bare metal, LXD or Kubernetes. We will be using it to deploy and manage charmed etcd. 

As with LXD, Juju is installed using a snap package:

```text
sudo snap install juju
```

Juju already has a built-in knowledge of LXD and how it works, so there is no
additional setup or configuration needed, however,  because Juju 3.x is a
[strictly confined snap](https://snapcraft.io/docs/classic-confinement), 
and is not allowed to create a `~/.local/share` directory, we need to create it
manually.

```text
mkdir -p ~/.local/share
```

To list the clouds available to Juju, run the following command:

```text
juju clouds
```

The output will look as follows:

```text
Clouds available on the client:
Cloud      Regions  Default    Type  Credentials  Source    Description
localhost  1        localhost  lxd   1            built-in  LXD Container Hypervisor
```

Notice that Juju already has a built-in knowledge of LXD and how it works,
so there is no need for additional setup. A controller will be used to deploy
and control charmed etcd. 

Run the following command to bootstrap a Juju controller named `dev-controller` on LXD:

```text
juju bootstrap localhost dev-controller
```

This bootstrapping process can take several minutes depending on your system
resources. The Juju controller exists within an LXD container. You can verify
this by entering the command `lxc list`.

This will output the following:

```text
+---------------+---------+-----------------------+------+-----------+-----------+
|     NAME      |  STATE  |         IPV4          | IPV6 |   TYPE    | SNAPSHOTS |
+---------------+---------+-----------------------+------+-----------+-----------+
| juju-<id>     | RUNNING | 10.86.196.118 (eth0)  |      | CONTAINER | 0         |
+---------------+---------+-----------------------+------+-----------+-----------+
```

where `<id>` is a unique combination of numbers and letters such as `9d7e4e-0`

Set up a unique model for this tutorial named `etcd`:

```text
juju add-model etcd
```

You can now view the model you created above by entering the [`juju status` command](https://juju.is/docs/juju/juju-status). You should see the following:

```text
juju status
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  17:26:15Z
```

## Deploy etcd

To deploy charmed etcd, all you need to do is run the following command:

```text
juju deploy charmed-etcd -n 3 --channel 3.6/edge
```

```{note}
The `-n` flag is optional and specifies the number of units to deploy. In this case, we are deploying three units of charmed etcd. 

We recommend deploying at least three units for high availability.
```

The command will fetch the charm from [Charmhub](https://charmhub.io/charmed-etcd?channel=3.6/edge)
and deploy 3 units to the LXD cloud. This process can take several minutes
depending on your machine. 

You can track the progress by running:

```text
juju status --watch 1s
``` 

When the application is ready, `juju status` will show something similar to the sample output below: 

```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  17:26:19Z

App           Version  Status  Scale  Charm         Channel   Rev  Exposed  Message
charmed-etcd           active      3  charmed-etcd  3.5/edge    1  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0   active    idle   12       10.86.196.210          
charmed-etcd/1   active    idle   13       10.86.196.224          
charmed-etcd/2*  active    idle   14       10.86.196.143          

Machine  State    Address        Inst id         Base          AZ  Message
12       started  10.86.196.210  juju-6b619f-12  ubuntu@22.04      Running
13       started  10.86.196.224  juju-6b619f-13  ubuntu@22.04      Running
14       started  10.86.196.143  juju-6b619f-14  ubuntu@22.04      Running
```

To exit the `juju status` screen, enter `Ctrl + C`.

(access-etcd)=
## Access etcd

You can access etcd with a command line client like `etcdctl` or via REST API.

To confirm that the API is reachable, we can use `curl` to make a request to one of the nodes. If it returns a JSON string with the etcd server version, it is healthy.

```{terminal}
:input: curl -L http://10.86.196.143:2379/version


{"etcdserver":"3.5.16","etcdcluster":"3.5.0"}
```

## Next steps

Now that your cluster is set up, you can check [how-to guides](../how-to/index.md) such as:
* [How to scale your cluster](../how-to/scale-horizontally.md)
* [How to manage TLS encryption](../how-to/tls/index.md)
* [How to manage passwords](../how-to/manage-passwords.md)