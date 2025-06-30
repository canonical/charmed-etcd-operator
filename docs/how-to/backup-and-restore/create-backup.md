# How to create a backup

## Prerequisites
* A deployment of a charmed etcd application
  * There is no minimum amount of units
  * You can even create a backup if the etcd workload is currently not running
* Integration with a storage provider, see [](configure-object-storage-provider.md)
* Make sure you have enough storage on disk to create the backup file of your database. If this is not the case, add a volume that is big enough
  * See [](../manage-persistent-storage.md)

## Save current cluster credentials
For security reasons, charm credentials are not stored inside backups. So, if you plan to restore to a backup at any 
point in the future, **you will need the admin user's password for your existing cluster**.

To retrieve the Juju secret that includes the current admin password, run the following command:
```text
juju config charmed-etcd system-users
```

You will receive the secret URI as confirmation: 
```text
secret:d184d2q96n7svmjrivqg
```

Now display the secret content using this URI:
```text
juju show-secret --reveal secret:d184d2q96n7svmjrivqg
```

The secret content includes the admin password:
```text
d184d2q96n7svmjrivqg:
  [...]
  content:
    root: changeme
  [...]
```

If you did not configure an admin password to charmed etcd yet, you can do this by following the steps in [](../manage-passwords.md).

## Create backup
Once you have an etcd cluster with configurations set for object storage, you can create your first backup:
```text
juju run charmed-etcd/leader create-backup
```

The output will return the id of the backup you just created:
```text
Running operation 4 with 1 task
  - task 5 on unit-charmed-etcd-0

Waiting for task 5...
16:42:46 Initiating backup process ...

backup-id: "2025-06-16T16:42:45Z"
```

You will also find a confirmation message in the log:
```text
unit-charmed-etcd-0: 16:42:48 INFO unit.charmed-etcd/0.juju-log Backup uploaded to backups/2025-06-16T16:42:45Z
```

## List backups
You can list your available backups by running `juju run charmed-etcd/leader list-backups`:
```text
Running operation 6 with 1 task
  - task 7 on unit-charmed-etcd-0

Waiting for task 7...
backups: |-
  backup-id             | backup-status
  -------------------------------------
  2025-06-16T16:42:45Z  | finished
  2025-06-06T10:08:39Z  | finished
```