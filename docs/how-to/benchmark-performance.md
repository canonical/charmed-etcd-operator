# How to benchmark charmed etcd

The [etcd benchmark tool](https://github.com/etcd-io/etcd/tree/main/tools/benchmark) is the upstream utility for measuring the performance of an etcd cluster. The [`etcd-benchmark`](https://charmhub.io/etcd-benchmark) charm packages this tool as a machine charm so that you can run benchmarks against a charmed-etcd deployment from within Juju.

This guide shows how to deploy the benchmark charm alongside a running charmed-etcd cluster, kick off a benchmark run, and read back the results.

## Prerequisites

- A Juju controller bootstrapped on a machine cloud (for example, LXD). See the [tutorial](/tutorial/index.md) if you do not yet have one.
- A deployed charmed-etcd application. The examples below assume an application named `charmed-etcd` running in a model named `etcd`:

  ```text
  juju add-model etcd
  juju deploy charmed-etcd -n 3 --channel 3.6/stable
  ```

  Adjust the number of units and channel to match your deployment.

## Deploy the etcd-benchmark charm

Deploy the `etcd-benchmark` charm into the same model as your charmed-etcd cluster, from Charmhub:

```text
juju deploy etcd-benchmark --channel 3.6/edge
```

```{attention}
The etcd-benchmark charm is designed to run as a **single unit** and benchmarks must be initiated from the leader unit. Scaling the benchmark application to multiple units is not supported. Benchmark actions (`run`, `stop`, `list-tests`, `get-summary`) will only succeed on the leader.
```

## Enable TLS on the cluster

As described in the [charmed-etcd docs](https://canonical-charmed-etcd.readthedocs-hosted.com/), TLS certificates are required in order to integrate charmed-etcd with client applications.
Deploy a TLS provider and integrate it with `charmed-etcd` and `etcd-benchmark`. The [`self-signed-certificates`](https://charmhub.io/self-signed-certificates) charm is suitable for testing:

```text
juju deploy self-signed-certificates --channel edge

juju integrate charmed-etcd:client-certificates self-signed-certificates
juju integrate etcd-benchmark self-signed-certificates
```

Watch the model settle with `juju status --watch 3s --integrations` and wait until all applications are `active` and all agents `idle`.

## Integrate the benchmark charm with etcd

Once TLS is in place, relate the benchmark charm to charmed-etcd:

```text
juju integrate etcd-benchmark charmed-etcd
```

When the integration is established, the benchmark charm has everything it needs (endpoints, credentials, and CA certificate) to reach the cluster.

## Run a benchmark

Start a benchmark run from the leader unit:

```text
juju run etcd-benchmark/leader run
```

By default, the benchmark runs indefinitely and reports results periodically, until you stop it explicitly:

```text
juju run etcd-benchmark/leader stop
```

### Bound a run

If you wish to bound the run so that it terminates on its own, the charm offers two independent limits, configured with `juju config`:

- `duration` — the run length in seconds.
- `total-transactions` — the number of transactions after which the run stops.

If both are set, the run terminates as soon as either condition is met. The following example runs the benchmark for 20 minutes or 100,000 transactions, whichever comes first:

```text
juju config etcd-benchmark duration=1200 total-transactions=100000
```

The `report-interval` option controls how often (in seconds) interim results are emitted; it defaults to 10. To receive updates every 30 seconds instead:

```text
juju config etcd-benchmark report-interval=30
```

### Adjust the workload

A handful of additional options let you shape the workload being driven against the cluster, for example `rate` (requests per second) and `rw-ratio` (the read-to-write ratio). Set them with `juju config` before starting the run:

```text
juju config etcd-benchmark rate=200 rw-ratio=2
```

For the full list of options and their defaults, inspect the charm configuration directly:

```text
juju config etcd-benchmark
```

## Read the results

Each run is assigned a test ID. To list every run — in progress or completed — together with its status, use the `list-tests` action on the leader:

```text
juju run etcd-benchmark/leader list-tests
```

To view the summary of a specific run, pass its test ID to `get-summary`:

```text
juju run etcd-benchmark/leader get-summary --string-args test-id=<test-id>
```

Benchmark output is also written to the Juju debug log, which is useful for following a run in real time:

```text
juju debug-log --replay
```

## Visualize benchmark metrics in Grafana

The benchmark charm provides the `cos-agent` interface and exposes its metrics in a Prometheus-friendly format. To surface these metrics in a Grafana dashboard, deploy `grafana-agent` in the same model and integrate it with the benchmark charm:

```text
juju deploy grafana-agent --base ubuntu@24.04
juju integrate grafana-agent etcd-benchmark
```

Then connect `grafana-agent` to a Canonical Observability Stack (COS) deployment. The steps for deploying COS, offering its endpoints, and consuming the offers from the machine model are described in [How to enable monitoring](/how-to/enable-monitoring.md) — follow that guide, then integrate the agent with the COS offers:

```text
juju integrate grafana-agent prometheus-receive-remote-write
juju integrate grafana-agent grafana-dashboards
```

Once the model settles, benchmark metrics are available in Grafana alongside the etcd cluster metrics.

## Clean up

To remove the benchmark charm and its integrations once you are done measuring, first remove the relations to etcd and the TLS provider, then remove the application:

```text
juju remove-relation etcd-benchmark charmed-etcd
juju remove-relation etcd-benchmark self-signed-certificates
juju remove-application etcd-benchmark
```

## Further reading

- [`etcd-benchmark` charm on Charmhub](https://charmhub.io/etcd-benchmark)
- [etcd-benchmark source code on GitHub](https://github.com/canonical/charmed-etcd-benchmark-operator)
- [How to tune etcd settings](/how-to/tune-settings.md). Adjust cluster parameters that may influence benchmark results.
