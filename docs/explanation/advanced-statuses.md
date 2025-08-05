# Advanced Statuses

Charmed etcd utilises Advanced Statuses to provide detailed, component-specific status information. This interface supports more complex and multiple statuses.

## Basics

Advanced Statuses are implemented in the home-brewed [Data Platform Helpers](https://pypi.org/project/data-platform-helpers/) library.

Each component of the charm recomputes its statuses on every `update-status` event, and will sort the statuses by order of importance.

They are firstly ordered like `ops` does: `Error > Blocked > Maintenance > Waiting > Active  > Unknown` and then by component priority if the status levels are equal.

If multiple important (`Blocked`, `Maintenance` or `Waiting`) statuses are reported, a special status is computed. This status has the priority of the most important status, and its message is the following:

```text
"<status message>. Run `status-detail`: X action required; Y additional statuses")
```

For example, the following `juju status` output shows an aggregated status for the charmed-etcd application:

```{terminal}
:input: juju status
Model  Controller  Cloud/Region         Version  SLA          Timestamp
test   lxd         localhost/localhost  3.6.8    unsupported  12:45:09Z

App                       Version  Status       Scale  Charm                     Channel  Rev  Exposed  Message
charmed-etcd                       maintenance      3  charmed-etcd                         0  no       Initializing etcd cluster.... Run `status-detail`: 0 action required; 3 additional statuses.
self-signed-certificates           active           1  self-signed-certificates  1/edge   325  no

Unit                         Workload     Agent      Machine  Public address  Ports  Message
charmed-etcd/0*              maintenance  executing  1        10.72.192.153          Initializing etcd cluster.... Run `status-detail`: 0 action required; 3 additional statuses.
charmed-etcd/1               maintenance  executing  2        10.72.192.61           Initializing etcd cluster.... Run `status-detail`: 0 action required; 3 additional statuses.
charmed-etcd/2               maintenance  executing  3        10.72.192.225          Initializing etcd cluster.... Run `status-detail`: 0 action required; 3 additional statuses.
self-signed-certificates/0*  active       executing  0        10.72.192.240

Machine  State    Address        Inst id        Base          AZ  Message
0        started  10.72.192.240  juju-3baff2-0  ubuntu@24.04      Running
1        started  10.72.192.153  juju-3baff2-1  ubuntu@24.04      Running
2        started  10.72.192.61   juju-3baff2-2  ubuntu@24.04      Running
3        started  10.72.192.225  juju-3baff2-3  ubuntu@24.04      Running
```

Due to their extended structure, advanced statuses contain more information than regular ones, providing developers with valuable hints for operators. They can specify an action to run to resolve a status and indicate which check led to the status being computed.

Statuses can be set as critical so that they override the regular flow and are displayed no matter what happens if they require immediate action.

## `status-detail` action

The `status-detail` action is a helper that provides extended access to the charm statuses.
Running this action will display all application and unit statuses. It includes an optional `recompute` argument that allows for status re-evaluation. When `recompute` is used, the system re-computes statuses for non-leader units and all statuses for leader units.

This action is particularly useful for debugging and understanding the charm's current state, as it provides a detailed view of all statuses, including those not visible in the standard status output.

Running the `status-detail` action produces output similar to the following:

```{terminal}
:input: juju run charmed-etcd/1 status-detail
Running operation 1 with 1 task
  - task 2 on unit-charmed-etcd-1

Waiting for task 2...
12:45:18 Stored statuses:
12:45:19                                  App Statuses
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┓
┃ Status      ┃ Component Name   ┃ Message                  ┃ Action ┃ Reason ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━┩
│ Maintenance │ cluster          │ Initializing etcd        │ N/A    │ N/A    │
│             │                  │ cluster...               │        │        │
│ Maintenance │ cluster          │ Waiting to join cluster  │ N/A    │ N/A    │
│ Maintenance │ tls              │ Enabling peer TLS...     │ N/A    │ N/A    │
│ Maintenance │ tls              │ Enabling client TLS...   │ N/A    │ N/A    │
│ Active      │ config           │                          │ N/A    │ N/A    │
│ Active      │ external_clients │                          │ N/A    │ N/A    │
│ Active      │ backup           │                          │ N/A    │ N/A    │
└─────────────┴──────────────────┴──────────────────────────┴────────┴────────┘
12:45:19                                  Unit Statuses
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┓
┃ Status      ┃ Component Name   ┃ Message                  ┃ Action ┃ Reason ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━┩
│ Maintenance │ cluster          │ Initializing etcd        │ N/A    │ N/A    │
│             │                  │ cluster...               │        │        │
│ Maintenance │ cluster          │ Waiting to join cluster  │ N/A    │ N/A    │
│ Maintenance │ tls              │ Enabling peer TLS...     │ N/A    │ N/A    │
│ Maintenance │ tls              │ Enabling client TLS...   │ N/A    │ N/A    │
│ Active      │ config           │                          │ N/A    │ N/A    │
│ Active      │ external_clients │                          │ N/A    │ N/A    │
│ Active      │ backup           │                          │ N/A    │ N/A    │
└─────────────┴──────────────────┴──────────────────────────┴────────┴────────┘

json-output:
  app: '[{"Status": "Maintenance", "Component Name": "cluster", "Message": "Initializing
    etcd cluster...", "Action": "N/A", "Reason": "N/A"}, {"Status": "Maintenance",
    "Component Name": "cluster", "Message": "Waiting to join cluster", "Action": "N/A",
    "Reason": "N/A"}, {"Status": "Maintenance", "Component Name": "tls", "Message":
    "Enabling peer TLS...", "Action": "N/A", "Reason": "N/A"}, {"Status": "Maintenance",
    "Component Name": "tls", "Message": "Enabling client TLS...", "Action": "N/A",
    "Reason": "N/A"}, {"Status": "Active", "Component Name": "config", "Message":
    "", "Action": "N/A", "Reason": "N/A"}, {"Status": "Active", "Component Name":
    "external_clients", "Message": "", "Action": "N/A", "Reason": "N/A"}, {"Status":
    "Active", "Component Name": "backup", "Message": "", "Action": "N/A", "Reason":
    "N/A"}]'
  unit: '[{"Status": "Maintenance", "Component Name": "cluster", "Message": "Initializing
    etcd cluster...", "Action": "N/A", "Reason": "N/A"}, {"Status": "Maintenance",
    "Component Name": "cluster", "Message": "Waiting to join cluster", "Action": "N/A",
    "Reason": "N/A"}, {"Status": "Maintenance", "Component Name": "tls", "Message":
    "Enabling peer TLS...", "Action": "N/A", "Reason": "N/A"}, {"Status": "Maintenance",
    "Component Name": "tls", "Message": "Enabling client TLS...", "Action": "N/A",
    "Reason": "N/A"}, {"Status": "Active", "Component Name": "config", "Message":
    "", "Action": "N/A", "Reason": "N/A"}, {"Status": "Active", "Component Name":
    "external_clients", "Message": "", "Action": "N/A", "Reason": "N/A"}, {"Status":
    "Active", "Component Name": "backup", "Message": "", "Action": "N/A", "Reason":
    "N/A"}]'
```

This output provides a comprehensive view of statuses, detailing component names, messages, actions, and reasons for each status. The `json-output` section at the end offers a structured format, which is ideal for parsing by automation tools and scripts.

## Developing with advanced statuses

With advanced statuses, the charm never sets statuses directly but goes through the [Data Platform Helpers](https://pypi.org/project/data-platform-helpers/) advanced statuses module.

While documentation is available in the library, keep these key points in mind:

* Add new statuses to the status collection in `src/statuses.py`.
* Ensure your manager inherits from the `ManagerStatusProtocol`.
* Implement the `get_statuses(scope, recompute)` method within your manager.
* Add your manager to the list of those that report statuses in `src/charm.py`.