# How to manage passwords

In the [Access etcd](#access-etcd) section of the tutorial, we didn't need to include a password in the HTTP request. However, in order to read or write data in etcd, we need to authenticate ourselves.

Typically, this can be done using a username and TLS certificate. For the sake of this guide, we will also use charmed {spellexception}`etcd's` internal admin user `root`. This user is only for  internal use, and it is created automatically by charmed etcd.

We will go through setting a user-defined password for the admin user and configuring it to charmed etcd. 

## Configure a user-provided password

First, create a secret in `Juju` containing your password:

```text
juju add-secret mysecret root=changeme
```

You will get the `secret` ID as a response:

```text
secret:ctbirhuutr9sr8mgrmpg
```

Make note of the string following `secret:`.

Grant the secret to charmed etcd:

```text
juju grant-secret mysecret charmed-etcd
```

Configure the secret's URI as `system-users` credentials to charmed etcd:

```text
juju config charmed-etcd system-users=secret:ctbirhuutr9sr8mgrmpg
```

Charmed etcd will now apply the new password to its `root` user. You can check the progress by running `juju status`. 
After a few moments, the deployment will settle:

```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.8    unsupported  10:07:25Z

App           Version  Status  Scale  Charm         Channel   Rev  Exposed  Message
charmed-etcd  3.6.1    active      3  charmed-etcd  3.6/edge   91  no

Unit             Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0   active    idle   0        10.248.160.82   2379/tcp      
charmed-etcd/1*  active    idle   1        10.248.160.120  2379/tcp      
charmed-etcd/2   active    idle   2        10.248.160.178  2379/tcp      

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.248.160.82   juju-48de5e-0  ubuntu@24.04      Running
1        started  10.248.160.120  juju-48de5e-1  ubuntu@24.04      Running
2        started  10.248.160.178  juju-48de5e-2  ubuntu@24.04      Running
```

Now you can use the password to access charmed etcd. Select the IP address for one of the units and check the current health of the cluster with this command:

```text
etcdctl endpoint health --cluster --endpoints=<your-IP-address>:2379 --user=root --password=changeme
```

You should receive an output similar to this:

```text
ttp://10.248.160.120:2379 is healthy: successfully committed proposal: took = 10.284464ms 
http://10.248.160.82:2379 is healthy: successfully committed proposal: took = 14.305912ms
http://10.248.160.178:2379 is healthy: successfully committed proposal: took = 2.462523ms
```

## Update the password

To update your user-configured password, simply update the value of the secret. Here's an example:

```text
juju update-secret mysecret root=moresecurepassword
```

After running this command, charmed etcd will immediately update the password. After the deployment has settled again,
you can no longer use the old password to access etcd. Instead, you will receive an error similar to this:

```text
authentication failed, invalid user ID or password
```

Instead, use your updated password:

```text
etcdctl endpoint health --cluster --endpoints=<your-IP-address>:2379 --user=root --password=moresecurepassword
```

The output should look like this again:

```text
http://10.248.160.120:2379 is healthy: successfully committed proposal: took = 10.048781ms
http://10.248.160.178:2379 is healthy: successfully committed proposal: took = 14.506541ms
http://10.248.160.82:2379 is healthy: successfully committed proposal: took = 1.717395ms
```
