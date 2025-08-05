# Charmed etcd Advanced Statuses

Charmed etcd utilises Advanced Statuses to provide detailed, component-specific status information. This interface supports more complex and multiple statuses, though it introduces additional complexity.

## Advanced Statuses: basics

Advanced Statuses are implemented in the home-brewed [Data Platform Helpers](https://pypi.org/project/data-platform-helpers/) library.

Each component of the charm recomputes its statuses on every `status-update` event, and will sort the statuses by order of importance.

They are firstly ordered like `ops` does: `Error > Blocked > Maintenance > Waiting > Active  > Unknown` and then by component priority if the status levels are equal.

If multiple important (`Blocked`, `Maintenance` or `Waiting`) statuses are reported, a special status is computed. This status has the priority of the most important status, and its message is the following:

```text
"<status message>. Run `status-detail`: X action required; Y additional statuses")
```

Due to their extended structure, advanced statuses contain more information than regular ones, providing developers with valuable hints for operators. They can specify an action to run to resolve a status and indicate which check led to the status being computed.

Statuses can be set as critical so that they override the regular flow and are displayed no matter what happens if they require immediate action.

## Advanced Statuses: `status-detail` action

The `status-detail` action is a helper that provides extended access to the charm statuses.
Running this action will display all application and unit statuses. It includes an optional `recompute` argument that allows for status re-evaluation. When `recompute` is used, the system re-computes statuses for non-leader units and all statuses for leader units.

```text
App:
+------------+----------------+---------+---------+--------+
| Status     | Component Name | Message | Action  | Reason |
+------------+----------------+---------+---------+--------+
| Blocked    | <component 1>  | <...>   | <...>   | <...>  |
| Blocked    | <component 2>  | <...>   | <...>   | <...>  |
| Maintence  | <component 3>  | <...>   | <...>   | <...>  |
| Waiting    | <component 1>  | <...>   | <...>   | <...>  |
+------------+----------------+---------+---------+--------+


Unit:
+------------+----------------+---------+---------+--------+
| Status     | Component Name | Message | Action  | Reason |
+------------+----------------+---------+---------+--------+
| Blocked    | <component 1>  | <...>   | <...>   | <...>  |
| Blocked    | <component 2>  | <...>   | <...>   | <...>  |
| Maintenance| <component 3>  | <...>   | <...>   | <...>  |
| Waiting    | <component 1>  | <...>   | <...>   | <...>  |
| Active     | <component 4>  | <...>   | <...>   | <...>  |
+------------+----------------+---------+---------+--------+

json-output: ...
```

The `json-output` is provided to have parsable statuses for automation.

### For developers: Developing with advanced statuses

With advanced statuses, the charm never sets statuses directly but goes through the [Data Platform Helpers](https://pypi.org/project/data-platform-helpers/) advanced statuses module.

While documentation is available in the library, keep these key points in mind:

* Add new statuses to the status collection in `src/statuses.py`.
* Ensure your manager inherits from the `ManagerStatusProtocol`.
* Implement the `get_statuses(scope, recompute)` method within your manager.
* Add your manager to the list of those that report statuses in `src/charm.py`.