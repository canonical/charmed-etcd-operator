# Deploy etcd

To deploy charmed etcd, all you need to do is run the following command:

```shell
juju deploy charmed-etcd -n 3 --channel 3.5/edge
```

>**Note:** The `-n` flag is optional and specifies the number of units to
> deploy. In this case, we are deploying three units of charmed etcd. We
> recommend deploying at least three units for high availability.

The command will fetch the charm from [Charmhub](https://charmhub.io/charmed-etcd?channel=3.5/edge)
and deploy 3 units to the LXD cloud. This process can take several minutes
depending on your machine. 

You can track the progress by running:

```shell
juju status --watch 1s
```

> See also: [`juju status` command](https://juju.is/docs/juju/juju-status) 

When the application is ready, `juju status` will show something similar to the sample output below: 

```shell
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

## Access etcd

You can access etcd with a command line client like `etcdctl` or via REST API.

In this tutorial, we will use `curl` with the REST API. Get the IP of an etcd node
from the output of juju status (any of the nodes should work fine), and run the
following command to connect to the etcd cluster:

```shell
curl -L http://10.86.196.143:2379/version
{"etcdserver":"3.5.16","etcdcluster":"3.5.0"}
```