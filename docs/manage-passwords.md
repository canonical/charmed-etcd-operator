# Manage Passwords
When we accessed etcd earlier in this tutorial, we didn't need to include a
password in the HTTP request. But in order to read or write data in etcd, we
need to authenticate ourselves.

Typically, this can be done using a username and TLS certificate. But for now
we can also use charmed etcd's internal admin user `root`. This user is only for 
internal use, and it is created automatically by charmed etcd.

We will go through setting a user-defined password for the admin user and 
configuring it to charmed etcd. 

First, create a secret in `Juju` containing your password:

```shell
juju add-secret mysecret root=changeme
```

You will get the `secret-id` as response. Make note of this, as we will need to
configure it to charmed etcd soon:
```shell
secret:ctbirhuutr9sr8mgrmpg
```

Now we grant our secret to charmed etcd:
```shell
juju grant-secret mysecret charmed-etcd
```

As final step, we configure the secret to charmed etcd, using the previously noted
`secret-id`:
```shell
juju config charmed-etcd system-users=secret:ctbirhuutr9sr8mgrmpg
```
