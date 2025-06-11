# How to integrate etcd with an application

[Relations](https://documentation.ubuntu.com/juju/latest/reference/relation/index.html) are connections between two applications with compatible endpoints. These connections simplify creating and managing users, passwords, and other shared data.

This guide will walk you through integrating your charm with etcd via the `etcd_client` interface or the [`data-integrator`](https://charmhub.io/data-integrator) charm.

```{caution}
charmed etcd only accepts client relations when TLS is enabled.
```

## Integrate a different charm with etcd

To integrate a different charm with etcd, you need to add the `etcd_client` interface to your charm. This interface allows your charm to communicate with etcd.

First, add the `etcd_client` interface to your charm in the `metadata.yaml` file. This file is located in the root directory of your charm.

```yaml
requires:
  etcd:
    interface: etcd_client
```

The `requires` section of the `metadata.yaml` file defines the relations that your charm can establish with other charms. In this case, we are defining a relation with the `etcd_client` interface.

Next, you need to implement the `etcd_client` interface in your charm's code. This is typically done in the `charm.py` file, which contains the main logic of your charm. We provide helpers as part of the [`data_interfaces`](https://charmhub.io/data-platform-libs/libraries/data_interfaces) library to make this easier.

To install the `data_interfaces` library, navigate to your charm's root directory and run the following command:

```{terminal}
:input: charmcraft fetch-lib charms.data_platform_libs.v0.data_interfaces
```

This command will download the `data_interfaces` library and make it available for use in your charm.
Next, import the `EtcdRequires` class from the `data_interfaces` library in your `charm.py` file:

```python
from charms.data_platform_libs.v0.data_interfaces import EtcdRequires
```

Then, create an instance of the `EtcdRequires` class in your charm's `__init__` method. The `EtcdRequires` class takes the following parameters:

- `charm`: The charm instance.
- `relation_name`: The name of the relation. This should match the name you defined in the `metadata.yaml` file.
- `prefix`: The range of keys that your charm will use in etcd.
- `mtls_cert`: The client certificate for mutual TLS authentication.

```python
class MyCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.etcd = EtcdRequires(
            self,
            relation_name="etcd",
            prefix="/my-charm/",
            mtls_cert=self.model.config["mtls_cert"],
        )
```

Finally, define a callback function to handle the `etcd-ready` event. This function will be called when the relation is established and the authentication information is available. You can use this information to connect to etcd and perform any necessary operations.

```python
class MyCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.etcd = EtcdRequires(
            self,
            relation_name="etcd",
            prefix="/my-charm/",
            mtls_cert=self.model.config["mtls_cert"],
        )
        self.etcd.on.etcd_ready(self._on_etcd_ready)

    def _on_etcd_ready(self, event):
        pass
```

The `etcd_ready` event provides the following attributes:

- `username`: The username of the user created in etcd. This matches the common name of the client certificate.
- `endpoints`: The endpoints of the etcd cluster. This is a comma-separated list of IP addresses and ports (`ip:port`).
- `uris`: The URIs of the etcd cluster. This is a comma-separated list of URIs (`scheme://ip:port`).
- `version`: The version of etcd that is being used.
- `tls`: Whether TLS is enabled. This is `True` if TLS is enabled and `False` otherwise.
- `tls_ca`: The CA certificate used to sign the server certificate.

The `EtcdRequires` class also emit:

- `endpoints_changed` event when the endpoints of etcd change.

To establish a relation between your charm and etcd, use the `juju integrate` command:

```{terminal}
:input: juju integrate charmed-etcd <your-charm>
```

To remove the relation, you can use the `juju remove-relation` command:

```{terminal}
:input: juju remove-relation <your-charm> charmed-etcd
```

If at any point in the charm code you need to access any field you can do the following:

```python
credentials = {
    "prefix": self.etcd.fetch_my_relation_field(relation.id, "prefix"),
    "username": self.etcd.fetch_relation_field(self.etcd_relation.id, "username"),
    "endpoints": self.etcd.fetch_relation_field(self.etcd_relation.id, "endpoints"),
    "uris": self.etcd.fetch_relation_field(self.etcd_relation.id, "uris"),
    "version": self.etcd.fetch_relation_field(self.etcd_relation.id, "version"),
    "tls": self.etcd.fetch_relation_field(self.etcd_relation.id, "tls"),
    "tls-ca": self.etcd.fetch_relation_field(self.etcd_relation.id, "tls-ca"),
}
```

## Integrate an application outside of Juju with etcd using data-integrator

[`data-integrator`](https://charmhub.io/data-integrator) is a bare-bones charm that allows for central management of database users.  

The `data-integrator` charm can be used to integrate an application outside of Juju with etcd. This is done by creating a relation between the `data-integrator` charm and the etcd charm.

First, deploy the `data-integrator` charm:

```{terminal}
:input: juju deploy data-integrator
```

Next, configure the `data-integrator` charm with the `prefix-name` and `mtls-cert` options. The `prefix-name` option specifies the prefix for the keys that will be used in etcd, and the `mtls-cert` option specifies the client certificate for mutual TLS authentication.

```{terminal}
:input: juju config data-integrator prefix-name=/my-charm/ mtls-cert="-----BEGIN CERTIFICATE-----...--------END CERTIFICATE-----"
```

Then, create a relation between the `data-integrator` charm and the etcd charm:

```{terminal}
:input: juju integrate data-integrator charmed-etcd
```

To fetch the authentication information, you can use the `get-credentials` action provided by the `data-integrator` charm:

```{terminal}
:input: juju run data-integrator/leader get-credentials
:scroll:
Running operation 1 with 1 task
  - task 2 on unit-data-integrator-0

Waiting for task 2...
etcd:
  endpoints: 10.0.59.172:2379,10.0.59.201:2379,10.0.59.209:2379
  uris: https://10.0.59.172:2379,https://10.0.59.201:2379,https://10.0.59.209:2379
  prefix: /my-charm/
  tls-ca: |-
    -----BEGIN CERTIFICATE-----
    ...
    -----END CERTIFICATE-----
  username: test
  version: 3.5.21
ok: "True"
```

To remove the relation, you can use the `juju remove-relation` command:

```{terminal}
:input: juju remove-relation data-integrator charmed-etcd
```
