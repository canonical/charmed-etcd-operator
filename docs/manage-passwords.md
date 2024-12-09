# Manage Passwords
When we accessed etcd earlier in this tutorial, we didn't need to include a
password in the HTTP request. But in order to read or write data in etcd, we
need to authenticate ourselves.

Typically, this can be done using a username and TLS certificate. But for now
we can also use Charmed etcd's internal admin user. This user is only for 
internal use, and it is created automatically by Charmed etcd.

We will go through setting a user-defined password for the admin user and 
configuring it to Charmed etcd. 

First, create a secret in `Juju` containing your password:

```shell
juju add-secret mysecret admin-password=changeme
```

You will get the `secret-id` as response. Make note of this, as we will need to
configure it to Charmed etcd soon:
```shell
secret:ctbirhuutr9sr8mgrmpg
```

Now we grant our secret to Charmed etcd:
```shell
juju grant-secret mysecret charmed-etcd
```

As final step, we configure the secret to Charmed etcd, using the previously noted
`secret-id`:
```shell
juju config charmed-etcd admin-password=secret:ctbirhuutr9sr8mgrmpg
```
