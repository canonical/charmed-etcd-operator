# Managing Private Keys in TLS

You can manage private keys used by the charm to generate the certificate signing requests (CSR) by storing the private key in a [juju secret](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/secret/) and then referencing the secret in the [charm configuration](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-applications/#configure-an-application).

## Store the Private Key in a Juju Secret
To store the private key in a juju secret, run the following command:

```shell
juju add-secret tls-peer-private-key private-key=$(base64 -w0 private-key.key)
secret:cuni0uh34trs5tihuf9g
```
You will get a secret ID as output. You can use this secret ID to reference the secret in the charm configuration.

Once the secret is stored, you can grant the secret to the application using the following command:

```shell
juju grant charmed-etcd tls-peer-private-key
```

## Reference the Secret in the Charm Configuration

To reference the secret in the charm configuration, run:

```shell
# For example, to set the private key for the peer-to-peer communication:
juju config charmed-etcd tls-peer-private-key=secret:cuni0uh34trs5tihuf9g
```

Once the configuration is set, the charm will use the private key stored in the secret to generate new certificate signing requests (CSR) to aquire new certificates from the TLS provider.

Setting the private key for the client-to-server communication is similar to the peer-to-peer communication. You can set the private key for the client-to-server communication by running:

```shell
juju add-secret tls-client-private-key private-key=$(base64 -w0 private-key.key)
juju grant-secret tls-client-private-key charmed-etcd
juju config charmed-etcd tls-client-private-key=<SECRET_ID>
```

