# How to tune in production

Charmed etcd includes default configuration settings that work well in most deployment settings. In some cases though,
it might be required to adjust the configuration. This guide will show how to tune charmed etcd to match specific requirements.

## Network latency

In networks with high latency or across regions, the default configuration can trigger unnecessary leader elections and disrupt the cluster. 
On the other hand, in deployments where fast failover matters, a longer election timeout can delay recovery when a leader fails.

Charmed etcd lets you adjust the settings for `heartbeat-interval` and `election-timeout`, in order to:
- Tolerate slower networks without constant leader re-elections
- Reduce overhead in resource-constrained environments by lowering heartbeat frequency
- Speed up failover when low latency is critical

In the following example, we will set `heartbeat-interval` to 300 milliseconds and `election-timeout` to 3 seconds.

You can either configure these settings at deploy time by adding the configuration to your deploy-command, for example:

```text
juju deploy charmed-etcd --channel 3.6/edge -n 3 --config heartbeat-interval=300 --config election-timeout=3000
```

Or you can set them on your existing charmed etcd application by running `juju config <application-name>...`, for example:

```text
juju config charmed-etcd heartbeat-interval=300 election-timeout=3000 
```

Charmed etcd will validate the configuration values and apply them to all units in the deployment (with a rolling restart
in case of an existing deployment). 

For more information about the allowed values, you can inspect the description of these settings by running `juju config charmed-etcd:`

```text
[...]
charm: charmed-etcd
settings: 
  election-timeout: 
    default: 1000
    description: How long, in milliseconds, a follower node will go without hearing
      a heartbeat before attempting to become leader itself. The value should be at
      least 10x the value of <heartbeat-interval>, and must not exceed 50000.
    source: user
    type: int
    value: 3000
  heartbeat-interval: 
    default: 100
    description: The frequency, in milliseconds, with which the leader will notify
      followers that it is still the leader. For best practices, the parameter should
      be set around round-trip time between members. The value must be between 10
      and 5000 at most.
    source: user
    type: int
    value: 300
[...]
```

If the configuration values are invalid, charmed etcd will display a status message in `juju status`:

```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  11:23:13Z

App           Version  Status   Scale  Charm         Channel   Rev  Exposed  Message
charmed-etcd           blocked      3  charmed-etcd  3.6/edge   66  no       Invalid values set for the config options: 'election-timeout', 'heartbeat-interval'

Unit             Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*  blocked   idle   9        10.136.16.209   2379/tcp  Invalid values set for the config options: 'election-timeout', 'heartbeat-interval'
charmed-etcd/1   blocked   idle   10       10.136.16.121   2379/tcp  Invalid values set for the config options: 'election-timeout', 'heartbeat-interval'
charmed-etcd/2   blocked   idle   11       10.136.16.41    2379/tcp  Invalid values set for the config options: 'election-timeout', 'heartbeat-interval'

Machine  State    Address        Inst id         Base          AZ  Message
9        started  10.136.16.209  juju-335dd8-9   ubuntu@24.04      Running
10       started  10.136.16.121  juju-335dd8-10  ubuntu@24.04      Running
11       started  10.136.16.41   juju-335dd8-11  ubuntu@24.04      Running
```

Please refer to the [official etcd docs](https://etcd.io/docs/v3.6/tuning/#time-parameters) for more information on how
to decide the correct adjustments to the parameters for your specific requirements.

## Certificate SANs configuration

In X.509 TLS certificates, a `Subject Alternative Name` (SAN) allows a certificate subject to be associated with the 
service name and domain name components of a DNS record. If charmed etcd is deployed in an environment where the DNS 
resolution on the client side does not correspond to the DNS resolution on the server side, it might be required to add 
custom SANs to the TLS certificates used for client- and/or peer-communication.

See more about TLS in charmed etcd: [TLS encryption](tls/index.md)

To add different IP addresses or hostnames to the SANs of charmed etcd's TLS certificates, configure the `certificate-extra-sans`
option. It is possible to add a comma-separated list of multiple values, as long as each of them is a valid IP address 
or hostname:

```text
juju config charmed-etcd certificate-extra-sans="10.241.9.34, etcd-production-cluster.mycompany.com"
```

If the configured sans are valid and not yet included in charmed etcd's certificates, it will automatically refresh
those.

```{caution}
Wildcards (`*`) are not allowed as part of the `certificate-extra-sans` configuration.
```

If it is required to include the unit number of each unit, this can be done by using the `{unit}` placeholder. For example,
a configuration of `certificate-extra-sans="etcd{unit}.my-external-domain.com"` would result in `etcd0.my-external-domain.com`
as an additional SAN in the TLS certificates for unit `charmed-etcd/0`.
