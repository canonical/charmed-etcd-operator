# Tutorial

This hands-on tutorial guides you through deploying, configuring, and operating Charmed etcd using Juju.

To keep this tutorial accessible, you will deploy the machine charm for etcd on LXD as a local cloud inside a virtual machine. The operations covered here also apply to production deployments on bare metal and public clouds.

## Prerequisites

This tutorial is most beneficial if:

* You have some experience using a Linux command line.
* You are familiar with [Juju](https://documentation.ubuntu.com/juju/latest/).
* You understand basic etcd concepts, such as key-value pairs, clustering, and Raft consensus.

### Minimum system requirements

* A Linux operating system that supports snaps
* At least 4 GB of RAM
* At least 4 CPUs
* At least 50 GB of storage
* Virtualization support
* `amd64` or `arm64` architecture

---

## Set up the environment

First, you will set up a virtual machine using [Multipass](https://multipass.run/) with [LXD](https://documentation.ubuntu.com/lxd/latest/) and [Juju](https://documentation.ubuntu.com/juju/3.6/). This approach isolates the deployment from your host system.

### Create a Multipass VM

Multipass launches Ubuntu virtual machines with automated configuration.

Install Multipass with snap:

```shell
sudo snap install multipass
```

Launch a virtual machine named `etcd` using Ubuntu 24.04 LTS (Noble Numbat) and the [`cloud-init-charm-dev.yaml`](https://raw.githubusercontent.com/canonical/multipass/refs/heads/main/data/cloud-init-yaml/cloud-init-charm-dev.yaml) configuration:

```shell
multipass launch 24.04 \
  --name etcd \
  --cpus 4 \
  --memory 8G \
  --disk 50G \
  --timeout 1800 \
  --cloud-init https://raw.githubusercontent.com/canonical/multipass/refs/heads/main/data/cloud-init-yaml/cloud-init-charm-dev.yaml
```

This step may take several minutes while cloud-init installs software packages.

After the virtual machine starts, open a shell inside it:

```shell
multipass shell etcd
```

The cloud-init configuration installs both LXD and Juju inside the virtual machine.

### Set up Juju

Bootstrap a Juju controller on the local LXD cloud:

```shell
juju bootstrap localhost dev-controller
```

A controller can host multiple models. Create a model named `tutorial` for this tutorial:

```shell
juju add-model tutorial
```

Confirm that the model is ready by checking the status:

```shell
juju status
```

You will see output similar to this:

```text
Model     Controller      Cloud/Region         Version  SLA          Timestamp
tutorial  dev-controller  localhost/localhost  3.6.28   unsupported  14:13:03+04:00

Model "admin/tutorial" is empty.
```

(deploy-etcd)=
## Deploy etcd

Deploy a single unit of Charmed etcd:

```shell
juju deploy charmed-etcd --channel 3.6/stable
```

````{note}
If you deploy on an ARM64 architecture, specify the architecture constraint:

```shell
juju deploy charmed-etcd --channel 3.6/stable --constraints arch=arm64
```
````

Juju fetches the charm from Charmhub and provisions an LXD container.

Track the deployment progress:

```shell
juju status --watch 1s
```

You can also view deployment events in another terminal using `juju debug-log`.

When the deployment finishes, `juju status` reports:

```text
Model     Controller      Cloud/Region         Version  SLA          Timestamp
tutorial  dev-controller  localhost/localhost  3.6.28   unsupported  14:20:29+04:00

App           Version  Status  Scale  Charm         Channel     Rev  Exposed  Message
charmed-etcd  3.6.13   active      1  charmed-etcd  3.6/stable  182  no

Unit             Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*  active    idle   0        10.109.130.153  2379/tcp

Machine  State    Address         Inst id        Base          AZ    Message
0        started  10.109.130.153  juju-bf477e-0  ubuntu@24.04  etcd  Running
```

Press `Ctrl+C` to exit the watch view.

(access-etcd)=
## Access etcd

Charmed etcd enables authentication by default. It generates an administrative internal user named `root` and stores its password in a Juju secret.

```{caution}
Do not use the `root` administrative user directly for external client applications in production. In production environments, integrate applications using client relations or manage dedicated credentials.
```

### Retrieve credentials

To connect to etcd, you need the public address of the unit and the password for the `root` user.

Find the unit address in the `Public address` column of `juju status`.

Retrieve the generated `root` password by inspecting the peer relation secret:

```shell
juju show-secret etcd-peers.charmed-etcd.app --reveal
```

The output displays the secret content:

```text
m4ia6m2nhd7si4ru16ng:
  revision: 1
  checksum: ee7db1843a2b3df54921601688bb64737ec4bf2bb315e19cf8401e4d94bad503
  owner: charmed-etcd
  label: etcd-peers.charmed-etcd.app
  created: 2026-09-11T10:16:26Z
  updated: 2026-09-11T10:16:26Z
  content:
    root-password: AqXgscaWxTiVWihlpGXdKRnQsA4S26eP
```

Copy the value of `root-password`.

### Connect with etcdctl

Install the `charmed-etcd` snap inside your virtual machine to provide `etcdctl`, and set an alias:

```shell
sudo snap install charmed-etcd --channel 3.6/stable
sudo snap alias charmed-etcd.etcdctl etcdctl
```

Confirm that the cluster endpoint is healthy by querying it with `etcdctl`. Replace `<unit-ip>` and `<root-password>` with your actual values:

```shell
etcdctl endpoint health --endpoints=http://<unit-ip>:2379 --user=root --password=<root-password>
```

The command returns a confirmation:

```text
http://10.109.130.153:2379 is healthy: successfully committed proposal: took = 322.624µs
```

You can now write and read data in the database. Write a key named `mykey`:

```shell
etcdctl put --endpoints=http://<unit-ip>:2379 --user=root --password=<root-password> mykey "HelloWorld"
```

The server confirms the write:

```text
OK
```

Read the value back from the database:

```shell
etcdctl get --endpoints=http://<unit-ip>:2379 --user=root --password=<root-password> mykey
```

The output returns the key and its stored value:

```text
mykey
HelloWorld
```

You can also run commands directly on the unit. Connect with SSH and use the snap wrapper:

```shell
juju ssh charmed-etcd/0 "charmed-etcd.etcdctl --endpoints=http://<unit-ip>:2379 --user=root --password=<root-password> get mykey"
```

## Scale your deployment

Etcd uses the Raft consensus algorithm to replicate state across nodes. Raft requires a strict majority of nodes (a quorum) to agree on proposals before committing them. An odd number of cluster members is recommended. A cluster of three nodes tolerates one node failure, while a cluster of five nodes tolerates two node failures.

```{caution}
This tutorial hosts all units on the same local machine for demonstration purposes. In production, distribute units across distinct physical machines or availability zones to maintain fault tolerance.
```

### Add units

Scale the deployment up to a three-node cluster:

```shell
juju add-unit charmed-etcd -n 2
```

Monitor the new units as they join the cluster:

```shell
juju status --watch 1s
```

After several minutes, all three units show `active` and `idle`:

```text
Model     Controller      Cloud/Region         Version  SLA          Timestamp
tutorial  dev-controller  localhost/localhost  3.6.28   unsupported  14:29:33+04:00

App           Version  Status  Scale  Charm         Channel     Rev  Exposed  Message
charmed-etcd  3.6.13   active      3  charmed-etcd  3.6/stable  182  no

Unit             Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*  active    idle   0        10.109.130.153  2379/tcp
charmed-etcd/1   active    idle   1        10.109.130.61   2379/tcp
charmed-etcd/2   active    idle   2        10.109.130.72   2379/tcp

Machine  State    Address         Inst id        Base          AZ    Message
0        started  10.109.130.153  juju-bf477e-0  ubuntu@24.04  etcd  Running
1        started  10.109.130.61   juju-bf477e-1  ubuntu@24.04  etcd  Running
2        started  10.109.130.72   juju-bf477e-2  ubuntu@24.04  etcd  Running
```

Verify that the new members joined the Raft cluster using `etcdctl`:

```shell
etcdctl member list --endpoints=http://<unit-ip>:2379 --user=root --password=<root-password> -w table
```

The command returns the three active cluster members:

```text
+------------------+---------+---------------+----------------------------+----------------------------+------------+
|        ID        | STATUS  |     NAME      |         PEER ADDRS         |        CLIENT ADDRS        | IS LEARNER |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
| 544861235643a6c1 | started | charmed-etcd0 | http://10.109.130.153:2380 | http://10.109.130.153:2379 |      false |
| 9c687c3e7cfc1234 | started | charmed-etcd2 |  http://10.109.130.72:2380 |  http://10.109.130.72:2379 |      false |
| 9fb1c1182e825d27 | started | charmed-etcd1 |  http://10.109.130.61:2380 |  http://10.109.130.61:2379 |      false |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
```

Data replicates across all members. Verify that `mykey` is available from the second unit:

```shell
etcdctl get --endpoints=http://<another-unit-ip>:2379 --user=root --password=<root-password> mykey
```

The output confirms the value is present:

```text
mykey
HelloWorld
```

### Remove a unit

Removing a unit removes that member from the cluster.

```{caution}
Avoid reducing a cluster below three members in production. A two-node cluster requires both nodes for quorum, meaning any single failure causes quorum loss.
```

Remove unit `charmed-etcd/2`:

```shell
juju remove-unit charmed-etcd/2
```

Wait until the unit leaves the deployment:

```shell
juju status --watch 1s
```

Check the member list again to confirm the member was removed:

```shell
etcdctl member list --endpoints=http://<unit-ip>:2379 --user=root --password=<root-password> -w table
```

The output shows only the two remaining members:

```text
+------------------+---------+---------------+----------------------------+----------------------------+------------+
|        ID        | STATUS  |     NAME      |         PEER ADDRS         |        CLIENT ADDRS        | IS LEARNER |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
| 544861235643a6c1 | started | charmed-etcd0 | http://10.109.130.153:2380 | http://10.109.130.153:2379 |      false |
| 9fb1c1182e825d27 | started | charmed-etcd1 |  http://10.109.130.61:2380 |  http://10.109.130.61:2379 |      false |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
```

## Enable encryption with TLS

Transport Layer Security (TLS) encrypts network communications and verifies server identity. In Charmed etcd, TLS can secure peer-to-peer traffic between nodes, client-to-server traffic, or both.

TLS certificates are provided through Juju relations with certificate authority charms. In this tutorial, you will use the [self-signed-certificates](https://charmhub.io/self-signed-certificates) charm.

```{caution}
Self-signed certificates are intended for testing and development. Use an official certificate provider such as Vault or Let's Encrypt in production environments.
```

### Deploy the certificate provider

Deploy the `self-signed-certificates` charm:

```shell
juju deploy self-signed-certificates --channel 1/edge --config ca-common-name="Tutorial CA"
```

````{note}
If you deploy on an ARM64 architecture, specify the architecture constraint:

```shell
juju deploy self-signed-certificates --channel 1/edge --config ca-common-name="Tutorial CA" --constraints arch=arm64
```
````

Wait for the certificate charm to reach `active` and `idle`:

```shell
juju status --watch 1s
```

```text
Model     Controller      Cloud/Region         Version  SLA          Timestamp
tutorial  dev-controller  localhost/localhost  3.6.28   unsupported  14:37:23+04:00

App                       Version  Status  Scale  Charm                     Channel     Rev  Exposed  Message
charmed-etcd              3.6.13   active      2  charmed-etcd              3.6/stable  182  no
self-signed-certificates           active      1  self-signed-certificates  1/edge      681  no

Unit                         Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*              active    idle   0        10.109.130.153  2379/tcp
charmed-etcd/1               active    idle   1        10.109.130.61   2379/tcp
self-signed-certificates/0*  active    idle   3        10.109.130.103

Machine  State    Address         Inst id        Base          AZ    Message
0        started  10.109.130.153  juju-bf477e-0  ubuntu@24.04  etcd  Running
1        started  10.109.130.61   juju-bf477e-1  ubuntu@24.04  etcd  Running
3        started  10.109.130.103  juju-bf477e-3  ubuntu@24.04  etcd  Running
```

### Enable TLS on etcd

Charmed etcd provides two separate TLS endpoints:

* `peer-certificates` secures replication traffic between etcd cluster members.
* `client-certificates` secures traffic between client applications and etcd.

Integrate both endpoints with the certificate provider:

```shell
juju integrate self-signed-certificates:certificates charmed-etcd:peer-certificates
juju integrate self-signed-certificates:certificates charmed-etcd:client-certificates
```

The operator generates keys, requests certificates, and restarts the workload to enable TLS. Monitor the transition:

```shell
juju status --watch 1s
```

After the status returns to `active` and `idle`, verify the TLS certificate on the client port using `openssl`:

```shell
openssl s_client -connect <unit-ip>:2379 </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates
```

The output shows that the certificate was issued by your tutorial authority:

```text
issuer=CN = Tutorial CA, x500UniqueIdentifier = 835b3cf7-7b75-4f4f-80c7-6274e59c2c67
notBefore=Sep 11 10:37:43 2026 GMT
notAfter=Dec 10 10:37:43 2026 GMT
```

### Disable TLS

To remove TLS encryption and return to unencrypted communication, remove both relations:

```shell
juju remove-relation self-signed-certificates:certificates charmed-etcd:client-certificates
juju remove-relation self-signed-certificates:certificates charmed-etcd:peer-certificates
```

The units revert to unencrypted communication once the status settles back to `active`.

## Clean up your environment

If you want to keep the deployment for further testing, exit the virtual machine with `exit` and stop it:

```shell
multipass stop etcd-vm
```

When you are ready to remove the tutorial environment completely and reclaim disk space, delete and purge the virtual machine:

```shell
multipass delete --purge etcd-vm
```

```{warning}
Purging the virtual machine permanently deletes all data inside it, including the etcd database and Juju configuration.
```

## Next steps

Now that you know how to deploy and operate Charmed etcd, explore the how-to guides for more advanced tasks:

* [Scale your cluster](../how-to/scale-horizontally.md)
* [Manage TLS encryption](../how-to/tls/index.md)
* [Manage passwords](../how-to/manage-passwords.md)
* [Integrate with applications](../how-to/client-relations.md)
* [Configure backups and restore data](../how-to/backup-and-restore/index.md)
* [Enable monitoring with the Canonical Observability Stack](../how-to/enable-monitoring.md)

