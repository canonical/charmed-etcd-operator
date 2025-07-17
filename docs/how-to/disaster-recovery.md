# How to perform disaster recovery

An etcd cluster automatically recovers from temporary failures of cluster members. If cluster members are lost permanently,
etcd is able to withstand up to `(N-1)/2` lost members.

```{caution}
If the cluster permanently loses more than `(N-1)/2` members then it loses quorum and fails. 
Once quorum is lost, the cluster cannot reach consensus and therefore cannot perform write operations anymore.
```

This condition is called majority failure.

## Detect majority failure

Charmed etcd checks regularly if the cluster is in healthy state. If it detects majority failure, it will update the 
status of the deployed charmed etcd application. You can observe this with `juju status`:

```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  13:22:15Z

App   Version  Status   Scale  Charm         Channel  Rev  Exposed  Message
etcd           blocked      3  charmed-etcd  3.6/edge  52  no       Cluster failure - majority of cluster members lost. Run action `rebuild-cluster` to recover.

Unit      Workload  Agent  Machine  Public address  Ports  Message
etcd/0*   blocked   idle   0        10.136.16.45           Cluster failure - majority of cluster members lost. Run action `rebuild-cluster` to recover.
etcd/1    unknown   lost   1        10.136.16.204          agent lost, see 'juju show-status-log etcd/1'
etcd/2    unknown   lost   2        10.136.16.118          agent lost, see 'juju show-status-log etcd/2'

Machine  State    Address        Inst id         Base          AZ  Message
0        started  10.136.16.45   juju-53b717-0   ubuntu@24.04      Running
1        down     10.136.16.204  juju-53b717-1   ubuntu@24.04      Running
2        down     10.136.16.118  juju-53b717-2   ubuntu@24.04      Running
```

The failed state is also printed in the log:

```text
unit-etcd-0: 13:23:23 WARNING unit.etcd/0.juju-log Cluster failed - no raft leader
unit-etcd-0: 13:23:23 ERROR unit.etcd/0.juju-log Cluster failure - majority of cluster members lost. Run action `rebuild-cluster` to recover.
```

## Recover from majority failure

Charmed etcd provides a mechanism to recover from majority failure. It can be executed by running the action `rebuild-cluster`. 

This will:
- stop the cluster
- keep existing data
- re-initialise the cluster members 
- form a new cluster with the remaining, functional units and the existing data

```{caution}
Before executing the action `rebuild-cluster`, make sure to remove the failed Juju units.
```

All units that are permanently lost, for example because their machine crashed, need to be removed first. In our example, 
these are the units `etcd/1` and `etcd/2`. If they can not be reached anymore by the Juju controller, they might need to
be removed with the `--force` option.

Remove them by running:

```
juju remove-unit etcd/1 etcd/2 --force --no-wait
```

```{caution}
This is a potentially dangerous command: `--force` will remove a unit and its machine without a clean shutdown. It should
only be executed if no other option exists.
```

After removing all faulty units, `juju status` will still show that the cluster is in the state of majority failure:

```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  13:46:47Z

App   Version  Status   Scale  Charm         Channel  Rev  Exposed  Message
etcd           blocked      1  charmed-etcd  3.6/edge  52  no       Cluster failure - majority of cluster members lost. Run action `rebuild-cluster` to recover.

Unit      Workload  Agent  Machine  Public address  Ports  Message
etcd/0*   blocked   idle   0        10.136.16.45           Cluster failure - majority of cluster members lost. Run action `rebuild-cluster` to recover.

Machine  State    Address       Inst id         Base          AZ  Message
0        started  10.136.16.45  juju-53b717-0   ubuntu@24.04      Running
```

Now it's time to recover by running the command: `juju run etcd/leader rebuild-cluster`. This will give you the output:

```text
Running operation 48 with 1 task
  - task 49 on unit-etcd-0

Waiting for task 49...
result: cluster rebuild in progress
```

You can observe the recovery process with `juju status` again:

```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  13:49:12Z

App   Version  Status   Scale  Charm         Channel  Rev  Exposed  Message
etcd           blocked      1  charmed-etcd  3.6/edge  52  no       Rebuilding with new cluster configuration...

Unit      Workload  Agent       Machine  Public address  Ports  Message
etcd/0*   blocked   executing   0        10.136.16.45           Rebuilding with new cluster configuration...

Machine  State    Address       Inst id         Base          AZ  Message
0        started  10.136.16.45  juju-53b717-0   ubuntu@24.04      Running
```

Shortly after, when the process is finished, your charmed etcd application has been recovered and is functional again:

```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  13:51:46Z

App   Version  Status  Scale  Charm         Channel  Rev  Exposed  Message
etcd           active      1  charmed-etcd  3.6/edge  52  no       

Unit      Workload  Agent  Machine  Public address  Ports  Message
etcd/0*   active    idle   0        10.136.16.45           

Machine  State    Address       Inst id         Base          AZ  Message
0        started  10.136.16.45  juju-53b717-0   ubuntu@24.04      Running
```