# How to scale horizontally

Adding and removing nodes from an etcd deployment is done by scaling [Juju units](https://juju.is/docs/juju/unit). 
 
## Add a node
You can add additional nodes to your deployed etcd application with the following command:

`juju add-unit charmed-etcd -n 1`

Where `-n 1` specifies the number of units to add. 

In this case, we are adding one unit to the etcd application. You can add more units by changing the number after -n.

You can now watch the new units join the cluster with: `juju status --watch 1s`. 
It usually takes a few minutes for the new nodes to be added to the cluster formation. 
You’ll know that all nodes are ready when `juju status --watch 1s` reports:

```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  09:53:59Z

App           Version  Status  Scale  Charm         Channel   Rev  Exposed  Message
charmed-etcd           active      4  charmed-etcd  3.5/edge   18  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0   active    idle   0        10.105.253.90          
charmed-etcd/1*  active    idle   1        10.105.253.210         
charmed-etcd/2   active    idle   2        10.105.253.47          
charmed-etcd/3   active    idle   3        10.105.253.27          

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.105.253.90   juju-fed980-0  ubuntu@22.04      Running
1        started  10.105.253.210  juju-fed980-1  ubuntu@22.04      Running
2        started  10.105.253.47   juju-fed980-2  ubuntu@22.04      Running
3        started  10.105.253.27   juju-fed980-3  ubuntu@22.04      Running
```

The `Charmed Etcd` operator has added the node to your etcd cluster correctly.
If you want to verify that it is now a cluster member, you can also use an etcd 
client to check.

We recommend the tool `etcdctl`, which can be installed, e.g. as a [package in 
Ubuntu](https://packages.ubuntu.com/search?keywords=etcd-client&searchon=names&suite=all&section=all), running the following commands:
```shell
$ sudo apt-get update
$ sudo apt-get install etcd-client
```

Once it has been installed, use `etcdctl` to verify the cluster formation. Run 
the following command, using one of the public addresses of your cluster:
```shell
$ etcdctl member list --endpoints=10.105.253.210:2379 -w=table
+------------------+---------+---------------+----------------------------+----------------------------+------------+
|        ID        | STATUS  |     NAME      |         PEER ADDRS         |        CLIENT ADDRS        | IS LEARNER |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
| 234c6a8da3b70eec | started | charmed-etcd2 |  http://10.105.253.47:2380 |  http://10.105.253.47:2379 |      false |
| 4f461443df8969ec | started | charmed-etcd1 | http://10.105.253.210:2380 | http://10.105.253.210:2379 |      false |
| 5effe145a89f5e0d | started | charmed-etcd3 |  http://10.105.253.27:2380 |  http://10.105.253.27:2379 |      false |
| 9f845651adefaf4e | started | charmed-etcd0 |  http://10.105.253.90:2380 |  http://10.105.253.90:2379 |      false |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
```

## Remove a node
> Warning: It is **highly recommended** to always have a cluster size greater 
> than two in production. It is unsafe to remove a member from a two member 
> cluster. If there is a failure during the removal process, the cluster might
> not be able to process requests anymore.

Removing a unit from the Juju application scales down your etcd cluster by one 
node. Before we scale down the nodes we no longer need, list all the units with 
juju status. Here you will see four units / nodes: 
- charmed-etcd/0
- charmed-etcd/1
- charmed-etcd/2
- charmed-etcd/3

To remove the unit charmed-etcd/3 run:

```shell
juju remove-unit charmed-etcd/3
```
You’ll know that the node was successfully removed when `juju status --watch 1s` reports:
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  10:38:03Z

App           Version  Status  Scale  Charm         Channel   Rev  Exposed  Message
charmed-etcd           active      3  charmed-etcd  3.5/edge   18  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0   active    idle   0        10.105.253.90          
charmed-etcd/1*  active    idle   1        10.105.253.210         
charmed-etcd/2   active    idle   2        10.105.253.47          

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.105.253.90   juju-fed980-0  ubuntu@22.04      Running
1        started  10.105.253.210  juju-fed980-1  ubuntu@22.04      Running
2        started  10.105.253.47   juju-fed980-2  ubuntu@22.04      Running
```

Run the same `etcdctl` command as earlier to verify the removed node is no longer a cluster member:
```shell
$ etcdctl member list --endpoints=10.105.253.210:2379 -w=table
+------------------+---------+---------------+----------------------------+----------------------------+------------+
|        ID        | STATUS  |     NAME      |         PEER ADDRS         |        CLIENT ADDRS        | IS LEARNER |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
| 234c6a8da3b70eec | started | charmed-etcd2 |  http://10.105.253.47:2380 |  http://10.105.253.47:2379 |      false |
| 4f461443df8969ec | started | charmed-etcd1 | http://10.105.253.210:2380 | http://10.105.253.210:2379 |      false |
| 9f845651adefaf4e | started | charmed-etcd0 |  http://10.105.253.90:2380 |  http://10.105.253.90:2379 |      false |
+------------------+---------+---------------+----------------------------+----------------------------+------------+
```