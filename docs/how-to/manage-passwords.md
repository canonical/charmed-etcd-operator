# How to manage passwords

In the [Access etcd](#access-etcd) section of the tutorial, we didn't need to include a password in the HTTP request. However, in order to read or write data in etcd, we need to authenticate ourselves.

Typically, this can be done using a username and TLS certificate. For the sake of this guide, we will also use charmed {spellexception}`etcd's` internal admin user `root`. This user is only for  internal use, and it is created automatically by charmed etcd.

We will go through setting a user-defined password for the admin user and configuring it to charmed etcd. 

First, create a secret in `Juju` containing your password. You will get the `secret` ID as a response.

```{terminal}
:input: juju add-secret mysecret root=changeme

secret:ctbirhuutr9sr8mgrmpg
```

Make note of the string following `secret:`.

Grant the secret to charmed etcd:

```{terminal}
:input: juju grant-secret mysecret charmed-etcd
```

Configure the secret's URI as `system-users` credentials to charmed etcd:

```{terminal}
:input: juju config charmed-etcd system-users=secret:ctbirhuutr9sr8mgrmpg
```
