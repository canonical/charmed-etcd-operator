# Disable encryption with TLS

To disable encryption with TLS, remove the relation between `charmed-etcd` and the TLS provider on the endpoint specific to the peer-to-peer or client-to-server communication.

To follow this guide, you need to have a running `charmed-etcd` cluster with TLS enabled. If you haven't deployed `charmed-etcd` with TLS enabled, you can follow the guide [here](./enable-tls.md).

```shell
Model  Controller  Cloud/Region         Version  SLA          Timestamp
test   dev         localhost/localhost  3.6.1    unsupported  10:24:14Z

App                       Version  Status  Scale  Charm                     Channel      Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd                             0  no
self-signed-certificates           active      1  self-signed-certificates  latest/edge  238  no

Unit                         Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0*              active    idle   1        10.73.32.122
charmed-etcd/1               active    idle   2        10.73.32.131
charmed-etcd/2               active    idle   3        10.73.32.193
self-signed-certificates/0*  active    idle   0        10.73.32.179

Machine  State    Address       Inst id        Base          AZ  Message
0        started  10.73.32.179  juju-5620b5-0  ubuntu@22.04      Running
1        started  10.73.32.122  juju-5620b5-1  ubuntu@24.04      Running
2        started  10.73.32.131  juju-5620b5-2  ubuntu@24.04      Running
3        started  10.73.32.193  juju-5620b5-3  ubuntu@24.04      Running

Integration provider                   Requirer                          Interface         Type     Message
charmed-etcd:etcd-peers                charmed-etcd:etcd-peers           etcd_peers        peer
charmed-etcd:restart                   charmed-etcd:restart              rolling_op        peer
self-signed-certificates:certificates  charmed-etcd:client-certificates  tls-certificates  regular
self-signed-certificates:certificates  charmed-etcd:peer-certificates    tls-certificates  regular
```

You can disable peer-to-peer encryption alone, client-to-server encryption alone, or both at the same time.

## Disable peer-to-peer encryption in transit

To disable peer-to-peer communication, run:

```shell
juju remove-relation self-signed-certificates charmed-etcd:peer-certificates
```

You can check the status of the relation using `juju status --watch 1s --relations`.

```shell
Model  Controller  Cloud/Region         Version  SLA          Timestamp
test   dev         localhost/localhost  3.6.1    unsupported  10:24:54Z

App                       Version  Status  Scale  Charm                     Channel      Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd                             0  no
self-signed-certificates           active      1  self-signed-certificates  latest/edge  238  no

Unit                         Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0*              active    idle   1        10.73.32.122
charmed-etcd/1               active    idle   2        10.73.32.131
charmed-etcd/2               active    idle   3        10.73.32.193
self-signed-certificates/0*  active    idle   0        10.73.32.179

Machine  State    Address       Inst id        Base          AZ  Message
0        started  10.73.32.179  juju-5620b5-0  ubuntu@22.04      Running
1        started  10.73.32.122  juju-5620b5-1  ubuntu@24.04      Running
2        started  10.73.32.131  juju-5620b5-2  ubuntu@24.04      Running
3        started  10.73.32.193  juju-5620b5-3  ubuntu@24.04      Running

Integration provider                   Requirer                          Interface         Type     Message
charmed-etcd:etcd-peers                charmed-etcd:etcd-peers           etcd_peers        peer
charmed-etcd:restart                   charmed-etcd:restart              rolling_op        peer
self-signed-certificates:certificates  charmed-etcd:client-certificates  tls-certificates  regular
```

Notice that the relation between `self-signed-certificates` and `charmed-etcd` for the peer-to-peer communication has been removed.

## Disable client-to-server encryption in transit and mutual authentication

To disable the client-to-server communication, run:

```shell
juju remove-relation self-signed-certificates charmed-etcd:client-certificates
```

You can wait for the relation to be removed by checking the status using `juju status --watch 1s --relations`.

```shell
Model  Controller  Cloud/Region         Version  SLA          Timestamp
test   dev         localhost/localhost  3.6.1    unsupported  10:25:45Z

App                       Version  Status  Scale  Charm                     Channel      Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd                             0  no
self-signed-certificates           active      1  self-signed-certificates  latest/edge  238  no

Unit                         Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0*              active    idle   1        10.73.32.122
charmed-etcd/1               active    idle   2        10.73.32.131
charmed-etcd/2               active    idle   3        10.73.32.193
self-signed-certificates/0*  active    idle   0        10.73.32.179

Machine  State    Address       Inst id        Base          AZ  Message
0        started  10.73.32.179  juju-5620b5-0  ubuntu@22.04      Running
1        started  10.73.32.122  juju-5620b5-1  ubuntu@24.04      Running
2        started  10.73.32.131  juju-5620b5-2  ubuntu@24.04      Running
3        started  10.73.32.193  juju-5620b5-3  ubuntu@24.04      Running

Integration provider     Requirer                 Interface   Type  Message
charmed-etcd:etcd-peers  charmed-etcd:etcd-peers  etcd_peers  peer
charmed-etcd:restart     charmed-etcd:restart     rolling_op  peer
```

Notice that the relation between `self-signed-certificates` and `charmed-etcd` for the client-to-server communication has been removed.

You have successfully disabled encryption with TLS for the `charmed-etcd` cluster.
You can verify that the cluster is running without encryption by checking checking the member list using the `etcdctl` command.

```shell
etcdctl member list --endpoints http://10.73.32.122:2379 -w table
+------------------+---------+---------------+--------------------------+--------------------------+------------+
|        ID        | STATUS  |     NAME      |        PEER ADDRS        |       CLIENT ADDRS       | IS LEARNER |
+------------------+---------+---------------+--------------------------+--------------------------+------------+
| 68327020b9432fc8 | started | charmed-etcd2 | http://10.73.32.193:2380 | http://10.73.32.193:2379 |      false |
| c5aec105e79a433b | started | charmed-etcd1 | http://10.73.32.131:2380 | http://10.73.32.131:2379 |      false |
| c74cb15a5aeade42 | started | charmed-etcd0 | http://10.73.32.122:2380 | http://10.73.32.122:2379 |      false |
+------------------+---------+---------------+--------------------------+--------------------------+------------+
```

Notice that the cluster is running without encryption. Both the `PEER ADDRS` and `CLIENT ADDRS` are using the HTTP protocol.

## Disable both peer-to-peer and client-to-server encryption at the same time

You can disable both peer-to-peer and client-to-server communication at the same time by removing both relations.

```shell
juju remove-relation self-signed-certificates charmed-etcd:peer-certificates && juju remove-relation self-signed-certificates charmed-etcd:client-certificates
```

## Rotate the TLS certificates

There are two scenarios that may trigger the rotation of TLS certificates:

1. The certificate has expired/is about to expire: In this case, Charmed etcd will automatically request a new certificate.
2. You want to rotate the certificate: In this case, you can manually request a new certificate.

To rotate the TLS certificates manually, all you have to do is remove the relation between the `charmed-etcd` and the TLS provider and then add the relation back. The charm will generate new certificates. 