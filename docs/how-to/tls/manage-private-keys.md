# How to manage private keys

You can manage private keys used by the charm to generate the certificate signing requests (CSR) by storing the private key in a [juju secret](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/secret/) and then referencing the secret in the [charm configuration](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-applications/#configure-an-application).

## Store the private key in a Juju secret

To store the private key in a juju secret, run the following command:

```{terminal}
:scroll:
:input: juju add-secret tls-peer-private-key private-key=$(base64 -w0 private-key.key)

secret:cuni0uh34trs5tihuf9g
```
You can use the secret ID from the output to reference the secret in the charm configuration.

Now that the secret is stored, you can grant the secret to the application using the following command:

```{terminal}
:scroll:
:input: juju grant-secret tls-peer-private-key charmed-etcd
```

## Reference the secret in the charm configuration

For example, to set the private key for the peer-to-peer communication, run

```{terminal}
:scroll:
:input: juju config charmed-etcd tls-peer-private-key=secret:cuni0uh34trs5tihuf9g
```

Once the configuration is set, the charm will use the private key stored in the secret to generate new certificate signing requests (CSR) to acquire new certificates from the TLS provider.

Setting the private key for the client-to-server communication is similar to the peer-to-peer communication. You can set the private key for the client-to-server communication by running:

```{terminal}
:scroll:

:input: juju add-secret tls-client-private-key private-key=$(base64 -w0 private-key.key)
:input: juju grant-secret tls-client-private-key charmed-etcd
:input: juju config charmed-etcd tls-client-private-key=<SECRET_ID>
```

