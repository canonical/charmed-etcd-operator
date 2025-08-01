# How to migrate an etcd cluster
This guide will show how to restore a backup that was made from a different etcd cluster (i.e. cluster migration via restore).

You can migrate different types of etcd clusters to charmed etcd:
* standalone etcd instances
* non-charmed etcd clusters
* other charmed etcd clusters

## Prerequisites
Restoring a backup from a previous cluster to charmed etcd requires:
* A charmed etcd application deployment with your desired amount of units
* An etcd backup file from the previous cluster with min. version 3.0 of etcd
* Integration with an [](configure-object-storage-provider.md) and all configuration set
* The backup file from the previous cluster must be present in the configured object storage
* Make sure you have enough storage on disk to download the backup file from object storage. If this is not the case, add a volume that is big enough
	* See [](../manage-persistent-storage.md)

## Apply cluster credentials
```{caution}
Make sure to have to correct admin password configured to your charmed etcd application. Please see [](restore-backup.md/#apply-cluster-credentials) for more information.
```

## Create a backup of the previous cluster
If you have not done already, create a backup of the etcd database or cluster you want to migrate to your charmed etcd application. For example, 
if you have an instance of etcd running on your local machine, run this command to create the backup file:
```text
etcdctl snapshot save standalone-etcd.db
```

This will create a backup file with the name `standalone-etcd.db`, which you can then upload to your object storage 
provider. For example, use the tool `s3cmd` to upload the backup file to an existing S3 storage bucket:

```text
s3cmd put standalone-etcd.db s3://<your-bucket>/etcd/ --access_key=<your-access-key> --secret_key=<your-secret-key>
```

## Restore backup to charmed etcd
You can list your available backups by running `juju run charmed-etcd/leader list-backups`:
```text
Running operation 11 with 1 task
  - task 12 on unit-charmed-etcd-0

Waiting for task 12...
backups: |-
  backup-id             | backup-status
  -------------------------------------
  standalone-etcd.db    | finished
  2025-06-16T16:42:45Z  | finished
  2025-06-06T10:08:39Z  | finished
```

The backup-id `standalone-etcd.db` is the backup file you uploaded earlier, it can now be used for restoring on your
charmed etcd application executing `juju run charmed-etcd/leader restore backup-id="standalone-etcd.db"`:
```text
Running operation 13 with 1 task
  - task 14 on unit-charmed-etcd-0

Waiting for task 14...
09:57:36 Initiating restore process for backup-id standalone-etcd.db

success: restore initiated for standalone-etcd.db
```

The cluster migration will now be executed by restoring the backup file to your charmed etcd application. You can watch 
the progress by running `juju status`:
```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  09:59:09Z

App            Version  Status  		Scale   Charm          Channel   Rev  Exposed  Message
charmed-etcd            maintenance 	3		charmed-etcd   3.6/edge   49  no       Database restore is in progress
s3-integrator           active      	1  		s3-integrator  1/stable  145  no       

Unit              Workload     Agent      Machine  Public address  Ports  Message
charmed-etcd/0*   maintenance  executing  1        10.188.28.54           Database restore is in progress
charmed-etcd/1    maintenance  executing  2        10.188.28.52           Database restore is in progress
charmed-etcd/2    maintenance  executing  3        10.188.28.143          Database restore is in progress
s3-integrator/0*  active       idle       0        10.188.28.139               
```

Once the restore is completed and all units are `active/idle` again, the cluster migration is finished.

You can now relate your client applications to charmed etcd. For more information, see [How to integrate etcd with an application](../client-relations.md).