# Manage persistent storage
Like many other databases, etcd stores its state on disk. In a default deployment (as described in [deploy-etcd](./deploy-etcd.md)),
the filesystem attached to charmed etcd will be removed when charmed etcd is removed. The content of the etcd database
would then be lost.

To decouple the lifecycle of the etcd database from the lifecycle of its deployment, charmed etcd provides a feature 
called persistent storage. It allows to keep storage volumes around, even after a deployed etcd application or unit 
has been removed.

The use cases can be broken down into two groups:
- reusing storage from previous units but within the same etcd cluster/database (see: [Same cluster scenario](#same-cluster-scenario))
or
- reusing storage from another etcd cluster/database (see: [Different cluster scenario](#different-cluster-scenario))

Charmed etcd uses two different storage volumes:
- `data` containing the raw data files (the actual database) written and managed by etcd
- `logs` for logfiles written by etcd

The following document will explain how to use this feature in charmed etcd.

## Prerequisites
The storage volumes that can be attached to charmed etcd depend on the storage providers available in the cloud where
charmed etcd gets deployed. Please refer to the documentation about [Juju storage](https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/storage)
for more information on possible providers.

In our case, as we deploy charmed etcd in a local LXD cloud, we use the `lxd` storage provider to create a storage pool
for etcd:
```shell
juju create-storage-pool etcd-storage lxd volume-type=standard
```

Make sure the storage pool has been created:
```shell
juju storage-pools
```

Example output:
```shell
Name          Provider  Attributes
etcd-storage  lxd       volume-type=standard
loop          loop      
lxd           lxd       
lxd-btrfs     lxd       driver=btrfs lxd-pool=juju-btrfs
lxd-zfs       lxd       driver=zfs lxd-pool=juju-zfs zfs.pool_name=juju-lxd
rootfs        rootfs    
tmpfs         tmpfs
```     

Details about how to manage storage pools with Juju can be found [in this guide](https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-storage-pools/#manage-storage-pools).

## Deploy with persistent storage
The decision about using the persistent storage feature of charmed etcd has to be made at deploy time. In order to create
persistent storage volumes, add the `--storage` option and define your storage parameters.

This command will deploy a charmed etcd application with three units, create a storage volume of 8 GB for each of them 
and attach the volume as the `data` volume mount:
```shell
juju deploy charmed-etcd -n 3 --storage data=etcd-storage,8G,1
```

You can track the progress by executing:
```shell
juju status --storage --watch=1s
```

When the application is ready, `juju status --storage` will show something similar to the sample output below: 
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:00:45Z

App           Version  Status  Scale  Charm         Channel  Rev  Exposed  Message
charmed-etcd           active      3  charmed-etcd             0  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0*  active    idle   0        10.105.253.32          
charmed-etcd/1   active    idle   1        10.105.253.52          
charmed-etcd/2   active    idle   2        10.105.253.148         

Machine  State    Address         Inst id        Base          AZ  Message
0        started  10.105.253.32   juju-84b57b-0  ubuntu@24.04      Running
1        started  10.105.253.52   juju-84b57b-1  ubuntu@24.04      Running
2        started  10.105.253.148  juju-84b57b-2  ubuntu@24.04      Running

Storage Unit    Storage ID  Type        Pool          Mountpoint                                  Size     Status    Message
charmed-etcd/0  data/0      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/0  logs/1      filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
charmed-etcd/1  data/2      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/1  logs/3      filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
charmed-etcd/2  data/4      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/2  logs/5      filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
```

As you can see, volumes from the `etcd-storage` pool have been attached as the `data` volume mount, whereas for the 
`logs` volume mount, non-persistent storage from `rootfs` has been used. The `logs` storage will be removed when the 
respective unit gets removed, while the `data` storage will persist.

## Same cluster scenario
In this scenario, we want to reuse storage from previous units but within the same etcd cluster/database. This could
be useful when you want to scale down your etcd database temporarily without completely removing it.

To scale down, run the `juju remove-unit` command:

```shell
juju remove-unit charmed-etcd/0 charmed-etcd/1 charmed-etcd/2
```

Example output:
```shell
WARNING This command will perform the following actions:
will remove unit charmed-etcd/0
- will remove storage logs/1
- will detach storage data/0
will remove unit charmed-etcd/1
- will remove storage logs/3
- will detach storage data/2
will remove unit charmed-etcd/2
- will remove storage logs/5
- will detach storage data/4

Continue [y/N]? y
```

As you can see, the `logs` storage will be removed while the `data` storage will only be detached.

You can check this after removing the units by running the `juju status --storage` command:
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:02:34Z

App           Version  Status   Scale  Charm         Channel  Rev  Exposed  Message
charmed-etcd           unknown      0  charmed-etcd             0  no       

Storage Unit  Storage ID  Type        Pool          Mountpoint  Size     Status    Message
              data/0      filesystem  etcd-storage              8.0 GiB  detached  
              data/2      filesystem  etcd-storage              8.0 GiB  detached  
              data/4      filesystem  etcd-storage              8.0 GiB  detached  
```

When you want to scale up your etcd database again, attach the desired `data` volume to the new unit of charmed etcd:
```shell
juju add-unit charmed-etcd --attach-storage=data/0
```

If you did not remove all previous units of charmed etcd at the same time, make sure to attach the volume with most 
recent data (meaning: of the unit you removed last).

You can track the progress by running `juju status --storage --watch=3s`:
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:06:22Z

App           Version  Status  Scale  Charm         Channel  Rev  Exposed  Message
charmed-etcd           active      1  charmed-etcd             0  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/3*  active    idle   3        10.105.253.200         

Machine  State    Address         Inst id        Base          AZ  Message
3        started  10.105.253.200  juju-84b57b-3  ubuntu@24.04      Running

Storage Unit    Storage ID  Type        Pool          Mountpoint                                  Size     Status    Message
                data/2      filesystem  etcd-storage                                              8.0 GiB  detached  
                data/4      filesystem  etcd-storage                                              8.0 GiB  detached  
charmed-etcd/3  data/0      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/3  logs/6      filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
```

As you can see, the new unit `charmed-etcd/3` has been deployed with the existing `data/0` volume and the new volume
`logs/6` attached to it.

In order to scale up to three units again, add execute two more `juju add-unit` commands with the other storage volumes:
```shell
juju add-unit charmed-etcd --attach-storage=data/2
juju add-unit charmed-etcd --attach-storage=data/4
```

When the application is ready, you will see all three `data` volumes attached again:
```shell
juju status --storage --watch=3s
```

Example output:
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:09:33Z

App           Version  Status  Scale  Charm         Channel  Rev  Exposed  Message
charmed-etcd           active      3  charmed-etcd             0  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/3*  active    idle   3        10.105.253.200         
charmed-etcd/4   active    idle   4        10.105.253.33          
charmed-etcd/5   active    idle   5        10.105.253.6           

Machine  State    Address         Inst id        Base          AZ  Message
3        started  10.105.253.200  juju-84b57b-3  ubuntu@24.04      Running
4        started  10.105.253.33   juju-84b57b-4  ubuntu@24.04      Running
5        started  10.105.253.6    juju-84b57b-5  ubuntu@24.04      Running

Storage Unit    Storage ID  Type        Pool          Mountpoint                                  Size     Status    Message
charmed-etcd/3  data/0      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/3  logs/6      filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
charmed-etcd/4  data/2      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/4  logs/7      filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
charmed-etcd/5  data/4      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/5  logs/8      filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
```

## Different cluster scenario
In this scenario, we want to reuse existing storage from another etcd cluster/database.

### Safe removal
>**Attention - Before you remove your etcd cluster:**
> - Safe the credentials for the admin-user
> - or configure a user-defined password

By default, charmed etcd enables authentication. That means you can not access an existing etcd database without 
providing credentials. This is also valid for the admin-user charmed etcd uses to operate the cluster.

At any time before removing your existing etcd cluster, you can provide a user-defined password for the admin user and 
configure it to charmed etcd. Please refer to [manage-passwords](./manage-passwords.md) or the following steps:

Create a juju secret with your desired password and make note of the secret's URI:
```shell
juju add-secret mysecret root=changeme
```

Example output:
```shell
secret:cuvh9ggv7vbc46jefvjg
```

Allow the charmed etcd application to access this secret:
```shell
juju grant-secret mysecret charmed-etcd
```

Configure the secret's URI as `system-users` credentials to charmed etcd:
```shell
juju config charmed-etcd system-users=secret:cuvh9ggv7vbc46jefvjg
```

Now it is safe to remove your existing charmed etcd application:
```shell
juju remove-application charmed-etcd
```

Example output:
```shell
WARNING This command will perform the following actions:
will remove application charmed-etcd
- will remove unit charmed-etcd/3
- will remove unit charmed-etcd/4
- will remove unit charmed-etcd/5
- will remove storage logs/6
- will remove storage logs/7
- will remove storage logs/8
- will detach storage data/0
- will detach storage data/2
- will detach storage data/4

Continue [y/N]? y
```

You can check the status after removal by running the `juju status --storage` command:
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:11:11Z

Storage Unit  Storage ID  Type        Pool          Mountpoint  Size     Status    Message
              data/0      filesystem  etcd-storage              8.0 GiB  detached  
              data/2      filesystem  etcd-storage              8.0 GiB  detached  
              data/4      filesystem  etcd-storage              8.0 GiB  detached  

Model "admin/etcd" is empty.
```

### Deploy new cluster with existing storage
After you removed your charmed etcd application, deploy a new one attaching the `data` volume with most recent data and 
configure the secret URI containing the admin user's password:
```shell
juju deploy charmed-etcd etcd --attach-storage=data/0 --config system-users=secret:cuvh9ggv7vbc46jefvjg
```

To be able to read the password from the secret, grant secret access to the new charmed etcd application again:
```shell
juju grant-secret mysecret charmed-etcd
```

Watch the progress of your deployment again with `juju status --storage --watch=3s`:
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:25:01Z

App           Version  Status  Scale  Charm         Channel  Rev  Exposed  Message
charmed-etcd           active      1  charmed-etcd             3  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/8*  active    idle   8        10.105.253.225         

Machine  State    Address         Inst id        Base          AZ  Message
8        started  10.105.253.225  juju-84b57b-8  ubuntu@24.04      Running

Storage Unit    Storage ID  Type        Pool          Mountpoint                                  Size     Status    Message
                data/2      filesystem  etcd-storage                                              8.0 GiB  detached  
                data/4      filesystem  etcd-storage                                              8.0 GiB  detached  
charmed-etcd/8  data/0      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/8  logs/12     filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
```

Once the application is in active status, scale up attaching the other volumes:
```shell
juju add-unit charmed-etcd --attach-storage=data/2
juju add-unit charmed-etcd --attach-storage=data/4
```

When the deployment is complete, your new charmed etcd application is available with previously used data:
```shell
juju status --storage --watch=3s
```

Example output:
```shell
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:29:48Z

App           Version  Status  Scale  Charm         Channel  Rev  Exposed  Message
charmed-etcd           active      3  charmed-etcd             3  no       

Unit             Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/8*  active    idle   8        10.105.253.225         
charmed-etcd/9   active    idle   9        10.105.253.154         
charmed-etcd/10  active    idle   10       10.105.253.220         

Machine  State    Address         Inst id         Base          AZ  Message
8        started  10.105.253.225  juju-84b57b-8   ubuntu@24.04      Running
9        started  10.105.253.154  juju-84b57b-9   ubuntu@24.04      Running
10       started  10.105.253.220  juju-84b57b-10  ubuntu@24.04      Running

Storage Unit     Storage ID  Type        Pool          Mountpoint                                  Size     Status    Message
charmed-etcd/10  data/4      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/10  logs/14     filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
charmed-etcd/8   data/0      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/8   logs/12     filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached  
charmed-etcd/9   data/2      filesystem  etcd-storage  /var/snap/charmed-etcd/common/var/lib/etcd  8.0 GiB  attached  
charmed-etcd/9   logs/13     filesystem  rootfs        /var/snap/charmed-etcd/common/var/log/etcd  76 GiB   attached
```

## Remove persistent storage
To remove all remaining volumes and their data, use the `--destroy-storage` parameter:
```shell
juju remove-application charmed-etcd --destroy-storage
```

Example output:
```shell
WARNING This command will perform the following actions:
will remove application charmed-etcd
- will remove unit charmed-etcd/8
- will remove unit charmed-etcd/9
- will remove unit charmed-etcd/10
- will remove storage logs/12
- will remove storage data/10
- will remove storage logs/13
- will remove storage data/2
- will remove storage logs/14
- will remove storage data/4

Continue [y/N]? y
```

To ensure that the storage volumes were removed, run the `juju storage` command. The output will contain the message: 
```shell
No storage to display.
```

To clean up the secret with the admin user credentials, obtain the secret ID:
```shell
juju secrets
```

Example output
```shell
ID                    Name      Owner    Rotation  Revision  Last updated
cuvh9ggv7vbc46jefvjg  mysecret  <model>  never            1  14 minutes ago  
```

Then, run the command `juju remove-secret <secret_id>`:
```shell
juju remove-secret cuvh9ggv7vbc46jefvjg
```