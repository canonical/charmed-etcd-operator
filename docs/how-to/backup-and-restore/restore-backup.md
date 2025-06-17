# How To restore a backup

## Prerequisites
* A deployment of a charmed etcd application
  * There is no required amount of units; the charmed operator will initialize the cluster after restore with the currently deployed Juju units
* It is possible to restore any backup from etcd, not only from charmed etcd clusters
  * The backup file must have been created on an etcd >= v3.0
  * The backup file to restore can either be a snapshot taken with `etcdctl` or a copy of a regular etcd database file (`member/snap/db`) 
* Integration with an [object storage provider](configure-object-storage-provider.md), the backup file to be restored must be present in the configured object storage
* Make sure you have enough storage on disk to download the backup file from object storage. If this is not the case, add a volume that is big enough
	* See [How to manage persistent storage](../manage-persistent-storage.md)

## Apply cluster credentials
For security reasons, charm credentials are not stored inside backups. So, when you restore a backup file from an etcd
cluster that had authentication enabled, **you will need the admin user's password for the cluster the backup was taken from**.

Charmed etcd will enable authentication by default after restoring a backup. It will either use the credentials that are
already configured to the deployed charmed etcd application, or automatically generate an admin password if none is provided.
For further information, see [manage passwords](../manage-passwords.md).

To retrieve the Juju secret that includes the current admin password, run the following command:
```{terminal}
:input: juju config charmed-etcd system-users
secret:d184d2q96n7svmjrivqg
```

Now display the secret content:
```{terminal}
:input: juju show-secret --reveal secret:d184d2q96n7svmjrivqg
d184d2q96n7svmjrivqg:
  name: mysecret
  [...]
  content:
    root: securepassword
  [...]
```

To change the admin password of the `root` user, run the following command (using your correct admin password from the 
time the backup was taken):

```{terminal}
:input: juju update-secret mysecret root=changeme
```

If you restore a backup that was taken from the same cluster and the admin password has not changed since then, you do 
not need to apply the credentials.

## List backups
You can list your available backups by running the `list-backups` command:
```{terminal}
:input: $ juju run charmed-etcd/leader list-backups

Running operation 6 with 1 task
  - task 7 on unit-charmed-etcd-0

Waiting for task 7...
backups: |-
  backup-id             | backup-status
  -------------------------------------
  2025-06-16T16:42:45Z  | finished
  2025-06-06T10:08:39Z  | finished
```

## Restore a backup
To restore a backup from the previously returned list, run the restore command and pass the corresponding backup-id:
```{terminal}
:input: juju run charmed-etcd/leader restore backup-id="2025-06-16T16:42:45Z"
Running operation 5 with 1 task
  - task 6 on unit-charmed-etcd-0

Waiting for task 6...
07:16:53 Initiating restore process for backup-id 2025-06-16T16:42:45Z

success: restore initiated for 2025-06-16T16:42:45Z
```

Your restore will then be in progress. You can watch its progress by running the following command:
```{terminal}
:input: watch juju status --color
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  07:17:08Z

App            Version  Status       Scale  Charm          Channel   Rev  Exposed  Message
charmed-etcd            maintenance      3  charmed-etcd   3.6/edge   49  no       Database restore is in progress
s3-integrator           active           1  s3-integrator  1/stable  145  no       

Unit              Workload     Agent      Machine  Public address  Ports  Message
charmed-etcd/0*   maintenance  executing  1        10.188.28.54           Database restore is in progress
charmed-etcd/1    maintenance  executing  2        10.188.28.52           Database restore is in progress
charmed-etcd/2    maintenance  executing  3        10.188.28.143          Database restore is in progress
s3-integrator/0*  active       idle       0        10.188.28.139          
```

The charm operator will:
* download the backup file from object storage
* stop the etcd database on all units
* ```{caution}Charmed etcd has a safety mechanism to avoid data loss: It verifies the restore before deleting data.``` For more information, see [handle a failed restore](#handle-a-failed-restore)
* purge the current cluster data and restore the backup on all units
* start the etcd database on all units and initialize the new cluster
* enable authentication

Your backup has been restored.

## Handle a failed restore
In order to avoid data loss, charmed etcd will verify the restore of your backup. It will therefore restore the backup 
file at first only on one of the units (the Juju leader), while the data on all other units will remain as is.

Possible reasons for a failed restore could be (among others):
* the backup file can not be downloaded from object storage
* the backup file is corrupt and can not be restored
* the configured cluster credentials are not correct
* the health check fails after restoring the backup and restarting etcd on this unit

If the restore verification fails, you will see the following status for your charmed etcd application:
```{terminal}
:input: watch juju status --color
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  07:36:01Z

App            Version  Status   Scale  Charm          Channel   Rev  Exposed  Message
charmed-etcd            blocked      3  charmed-etcd   3.6/edge   49  no       Restore verification failed - etcd cluster still running, restore cancelled, check debug-log
s3-integrator           active       1  s3-integrator  1/stable  145  no       

Unit              Workload  Agent      Machine  Public address  Ports  Message
charmed-etcd/0*   blocked   executing  1        10.188.28.54           Restore verification failed - etcd cluster still running, restore cancelled, check debug-log
charmed-etcd/1    blocked   idle       2        10.188.28.52           Restore verification failed - etcd cluster still running, restore cancelled, check debug-log
charmed-etcd/2    blocked   idle       3        10.188.28.143          Restore verification failed - etcd cluster still running, restore cancelled, check debug-log
s3-integrator/0*  active    idle       0        10.188.28.139          
```

If this happens, **the charmed operator will not purge the data of your existing cluster.** Instead, the restore operation 
will be cancelled and your existing cluster will continue to run as is.

In this case, investigate why the restore verification fails by checking the log:
```{terminal}
:input juju debug-log
Error: etcdserver: authentication failed, invalid user ID or password
...
Error: unhealthy cluster
```

You can run another `restore` operation at any time.
