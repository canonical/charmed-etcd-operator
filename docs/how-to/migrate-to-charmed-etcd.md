# How To migrate to charmed etcd

This guide outlines the steps required to migrate an existing etcd to charmed etcd.

## Prerequisites

- a Juju VM controller with a model
- TLS Provider deployed to that model, in our case we use `self-signed-certificates` (see: [](tls/enable-tls.md))
- Object Storage Provider deployed to that model, in our case we use `s3-integrator` (see [](backup-and-restore/configure-object-storage-provider.md))
- your existing etcd cluster must be at least of version 3.0

## Migration Guide

Migrating to charmed etcd includes the following steps:
- create a backup of your existing cluster
- upload the backup to object storage
- deploy charmed etcd
- integrate charmed etcd with object storage provider
- integrate charmed etcd with TLS provider
- optional: apply cluster credentials
- restore the backup to charmed etcd
- switch your application over to charmed etcd

### Create a backup

Migrating your existing etcd cluster to charmed etcd happens via backup and restore. First, create a backup of your
existing etcd cluster. The following command is an example for a snap-based installation of [etcd](https://snapcraft.io/etcd)
in version `3.4.36`. It connects to etcd via `localhost` and creates a backup file called `etcd-backup.db` in the current
working directory:

```text
etcdctl snapshot save etcd-backup.db
```

This will prompt the following output (or similar):
```text
{"level":"info","ts":1754036152.734727,"caller":"snapshot/v3_snapshot.go:119","msg":"created temporary db file","path":"etcd-backup.db.part"}
{"level":"info","ts":"2025-08-01T08:15:52.736886Z","caller":"clientv3/maintenance.go:212","msg":"opened snapshot stream; downloading"}
{"level":"info","ts":1754036152.7369702,"caller":"snapshot/v3_snapshot.go:127","msg":"fetching snapshot","endpoint":"127.0.0.1:2379"}
{"level":"info","ts":"2025-08-01T08:15:52.738103Z","caller":"clientv3/maintenance.go:220","msg":"completed snapshot read; closing"}
{"level":"info","ts":1754036152.7385268,"caller":"snapshot/v3_snapshot.go:142","msg":"fetched snapshot","endpoint":"127.0.0.1:2379","size":"20 kB","took":0.003674558}
{"level":"info","ts":1754036152.738597,"caller":"snapshot/v3_snapshot.go:152","msg":"saved","path":"etcd-backup.db"}
Snapshot saved at etcd-backup.db
```

### Upload the backup to object storage

For restoring backup files, charmed etcd supports S3-compatible or Azure object storage. In our example, we upload the
backup file that we just created to a self-hosted MicroCeph (S3-compatible) by using the `S3cmd` tool.

If not yet installed, execute the following command to install `S3cmd`:

```text
sudo apt-get install s3cmd
```

Ensure the bucket for uploading your backup file exists (replace the placeholders with your credentials). In this case
we want to use a bucket called `etcd-backups-test-bucket`:

```text
s3cmd ls s3://etcd-backups-test-bucket/ --access_key=<your-access-key> --secret_key=<your-secret>
```

You should see your bucket listing all available directories:
```text
DIR  s3://etcd-backups-test-bucket/etcd-backups/
```

If the storage bucket you want to use does not exist yet, create a bucket `etcd-backups` with the following command 
(replace the placeholders with your credentials):

```text
s3cmd mb s3://etcd-backups-test-bucket/ --access_key=<your-access-key> --secret_key=<your-secret>
```

With the following command, you can upload the backup file `etcd-backup.db` to the just created bucket 
(replace placeholders with your credentials again):

```text
s3cmd put etcd-backup.db s3://etcd-backups-test-bucket/etcd-backups/ --access_key=<your-access-key> --secret_key=<your-secret>
```

Ensure your backup file was uploaded correctly by repeating the `s3cmd ls s3://etcd-backups-test-bucket/etcd-backups/` command:

```text
2025-08-01 08:24        20512  s3://etcd-backups-test-bucket/etcd-backups/etcd-backup.db
```

### Deploy charmed etcd

Now it's time to deploy charmed etcd. Run the following command to deploy a 3-unit cluster:

```text
juju deploy charmed-etcd --channel 3.6/edge -n 3
```

Wait for the deployment to become available by checking `watch juju status --color`:

```text
Model          Controller      Cloud/Region         Version  SLA          Timestamp
backend-store  dev-controller  localhost/localhost  3.6.5    unsupported  08:34:43Z

App                       Version  Status  Scale  Charm                     Channel   Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd              3.6/edge   68  no       
s3-integrator                      active      1  s3-integrator             1/stable  145  no       
self-signed-certificates           active      1  self-signed-certificates  1/stable  317  no       

Unit                         Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*              active    idle   0        10.143.229.222  2379/tcp  
charmed-etcd/1               active    idle   1        10.143.229.119  2379/tcp  
charmed-etcd/2               active    idle   2        10.143.229.81   2379/tcp  
s3-integrator/0*             active    idle   4        10.143.229.157            
self-signed-certificates/0*  active    idle   3        10.143.229.160
```

### Integrate with object storage provider

After charmed etcd has been deployed, integrate it with the deployed object storage provider to provide access 
the object storage. In our case, this happens with `s3-integrator` over the `s3-credentials` interface.

Run the following command:

```text
juju integrate s3-integrator charmed-etcd
```

```{caution}
Ensure s3-integrator is configured correctly. Please refer to [](backup-and-restore/configure-object-storage-provider.md) for more information.
```

Shortly after the relation between them should be established:

```text
Model          Controller      Cloud/Region         Version  SLA          Timestamp
backend-store  dev-controller  localhost/localhost  3.6.5    unsupported  08:38:22Z

App                       Version  Status  Scale  Charm                     Channel   Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd              3.6/edge   68  no       
s3-integrator                      active      1  s3-integrator             1/stable  145  no       
self-signed-certificates           active      1  self-signed-certificates  1/stable  317  no       

Unit                         Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*              active    idle   0        10.143.229.222  2379/tcp  
charmed-etcd/1               active    idle   1        10.143.229.119  2379/tcp  
charmed-etcd/2               active    idle   2        10.143.229.81   2379/tcp  
s3-integrator/0*             active    idle   4        10.143.229.157            
self-signed-certificates/0*  active    idle   3        10.143.229.160            

[...]

Integration provider               Requirer                           Interface            Type     Message
charmed-etcd:etcd-peers            charmed-etcd:etcd-peers            etcd_peers           peer     
charmed-etcd:restart               charmed-etcd:restart               rolling_op           peer     
s3-integrator:s3-credentials       charmed-etcd:s3-credentials        s3                   regular  
s3-integrator:s3-integrator-peers  s3-integrator:s3-integrator-peers  s3-integrator-peers  peer     
```

### Integrate with TLS provider

Because charmed etcd relies on mTLS for client authentication and authorisation, it is mandatory to set up client TLS in 
charmed etcd. To do so, integrate with the deployed TLS provider over the `tls-certificates` interface. In our case, 
this is with the `self-signed-certificates` operator.

Run the following command:

```text
juju integrate self-signed-certificates:certificates charmed-etcd:client-certificates
```

Charmed etcd will enable client TLS on all units with a rolling restart. After a few moments, the relation should be 
established and charmed etcd should be settled again:

```text
Model          Controller      Cloud/Region         Version  SLA          Timestamp
backend-store  dev-controller  localhost/localhost  3.6.5    unsupported  08:43:12Z

App                       Version  Status  Scale  Charm                     Channel   Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd              3.6/edge   68  no       
s3-integrator                      active      1  s3-integrator             1/stable  145  no       
self-signed-certificates           active      1  self-signed-certificates  1/stable  317  no       

Unit                         Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*              active    idle   0        10.143.229.222  2379/tcp  
charmed-etcd/1               active    idle   1        10.143.229.119  2379/tcp  
charmed-etcd/2               active    idle   2        10.143.229.81   2379/tcp  
s3-integrator/0*             active    idle   4        10.143.229.157            
self-signed-certificates/0*  active    idle   3        10.143.229.160            

[...]

Integration provider                   Requirer                           Interface            Type     Message
charmed-etcd:etcd-peers                charmed-etcd:etcd-peers            etcd_peers           peer     
charmed-etcd:restart                   charmed-etcd:restart               rolling_op           peer     
s3-integrator:s3-credentials           charmed-etcd:s3-credentials        s3                   regular  
s3-integrator:s3-integrator-peers      s3-integrator:s3-integrator-peers  s3-integrator-peers  peer     
self-signed-certificates:certificates  charmed-etcd:client-certificates   tls-certificates     regular  
```

Though it is optional, it is also recommended to enable peer TLS for encryption between the etcd cluster members.

Run the following command to enable peer TLS:

```text
juju integrate self-signed-certificates:certificates charmed-etcd:peer-certificates
```

Charmed etcd will enable peer TLS on all units with a rolling restart. After a few moments, the relation should be 
established and charmed etcd should be settled again:

```text
Model          Controller      Cloud/Region         Version  SLA          Timestamp
backend-store  dev-controller  localhost/localhost  3.6.5    unsupported  08:46:21Z

App                       Version  Status  Scale  Charm                     Channel   Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd              3.6/edge   68  no       
s3-integrator                      active      1  s3-integrator             1/stable  145  no       
self-signed-certificates           active      1  self-signed-certificates  1/stable  317  no       

Unit                         Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*              active    idle   0        10.143.229.222  2379/tcp  
charmed-etcd/1               active    idle   1        10.143.229.119  2379/tcp  
charmed-etcd/2               active    idle   2        10.143.229.81   2379/tcp  
s3-integrator/0*             active    idle   4        10.143.229.157            
self-signed-certificates/0*  active    idle   3        10.143.229.160            

[...]

Integration provider                   Requirer                           Interface            Type     Message
charmed-etcd:etcd-peers                charmed-etcd:etcd-peers            etcd_peers           peer     
charmed-etcd:restart                   charmed-etcd:restart               rolling_op           peer     
s3-integrator:s3-credentials           charmed-etcd:s3-credentials        s3                   regular  
s3-integrator:s3-integrator-peers      s3-integrator:s3-integrator-peers  s3-integrator-peers  peer     
self-signed-certificates:certificates  charmed-etcd:client-certificates   tls-certificates     regular  
self-signed-certificates:certificates  charmed-etcd:peer-certificates     tls-certificates     regular  
```

### Optional: apply cluster credentials to charmed etcd

If your previous etcd cluster did not have authentication enabled, this step can be skipped.

If your previous etcd cluster uses authentication, it is required to apply the password of the `admin` user to charmed etcd.
Otherwise, restoring the backup will fail.

First, create a Juju secret with the admin password. Run the following command to create a secret called `etcd-credentials`,
 replacing the placeholder with your correct admin password:

```text
juju add-secret etcd-credentials root=<your-admin-password>
```

Take a note on the prompted secret URI, as this will later be configured to charmed etcd:

```text
secret:d268gfktvobctdg3loe0
```

Now allow charmed etcd access to this secret by running `juju grant-secret etcd-credentials charmed-etcd`.

Next step is to configure the secret to charmed etcd. Run the following command, replacing the secret URI with the one 
you noted earlier:

```text
juju config charmed-etcd system-users=secret:<your-secret-URI>
```

After a few moments, charmed etcd will have updated the credentials internally. Check the status:

```text
Model          Controller      Cloud/Region         Version  SLA          Timestamp
backend-store  dev-controller  localhost/localhost  3.6.5    unsupported  09:29:50Z

App                       Version  Status  Scale  Charm                     Channel   Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd              3.6/edge   68  no       
s3-integrator                      active      1  s3-integrator             1/stable  145  no       
self-signed-certificates           active      1  self-signed-certificates  1/stable  317  no       

Unit                         Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*              active    idle   0        10.143.229.222  2379/tcp  
charmed-etcd/1               active    idle   1        10.143.229.119  2379/tcp  
charmed-etcd/2               active    idle   2        10.143.229.81   2379/tcp  
s3-integrator/0*             active    idle   4        10.143.229.157            
self-signed-certificates/0*  active    idle   3        10.143.229.160            
```

### Restore the backup to charmed etcd

After applying your cluster credentials (if needed), it is time to migrate your data to charmed etcd by restoring the 
previously taken backup.

First, list the available backups in the object storage with this command:

```text
juju run charmed-etcd/leader list-backups
```

It should list the backup file `etcd-backup.db` in the output:

```text
Running operation 3 with 1 task
  - task 4 on unit-charmed-etcd-0

Waiting for task 4...
backups: |-
  backup-id             | backup-status
  -------------------------------------
  etcd-backup.db        | finished
```

Now restore this backup to your charmed etcd cluster by running:

```text
juju run charmed-etcd/leader restore backup-id="etcd-backup.db"
```

The restore will be initiated on all units of the charmed etcd application:

```text
Running operation 5 with 1 task
  - task 6 on unit-charmed-etcd-0

Waiting for task 6...
09:34:41 Initiating restore process for backup-id etcd-backup.db

success: restore initiated for etcd-backup.db
```

You can follow the progress by running `watch juju status --color`:

```text
Model          Controller      Cloud/Region         Version  SLA          Timestamp
backend-store  dev-controller  localhost/localhost  3.6.5    unsupported  09:34:57Z

App                       Version  Status       Scale  Charm                     Channel   Rev  Exposed  Message
charmed-etcd                       maintenance      3  charmed-etcd              3.6/edge   68  no       Database restore is in progress
s3-integrator                      active           1  s3-integrator             1/stable  145  no       
self-signed-certificates           active           1  self-signed-certificates  1/stable  317  no       

Unit                         Workload     Agent      Machine  Public address  Ports     Message
charmed-etcd/0*              maintenance  executing  0        10.143.229.222  2379/tcp  Database restore is in progress
charmed-etcd/1               maintenance  executing  1        10.143.229.119  2379/tcp  Database restore is in progress
charmed-etcd/2               maintenance  executing  2        10.143.229.81   2379/tcp  Database restore is in progress
s3-integrator/0*             active       idle       4        10.143.229.157            
self-signed-certificates/0*  active       idle       3        10.143.229.160            
```

After the restore was completed, the status will be `active/idle` again:

```text
Model          Controller      Cloud/Region         Version  SLA          Timestamp
backend-store  dev-controller  localhost/localhost  3.6.5    unsupported  09:37:01Z

App                       Version  Status  Scale  Charm                     Channel   Rev  Exposed  Message
charmed-etcd                       active      3  charmed-etcd              3.6/edge   68  no       
s3-integrator                      active      1  s3-integrator             1/stable  145  no       
self-signed-certificates           active      1  self-signed-certificates  1/stable  317  no       

Unit                         Workload  Agent  Machine  Public address  Ports     Message
charmed-etcd/0*              active    idle   0        10.143.229.222  2379/tcp  
charmed-etcd/1               active    idle   1        10.143.229.119  2379/tcp  
charmed-etcd/2               active    idle   2        10.143.229.81   2379/tcp  
s3-integrator/0*             active    idle   4        10.143.229.157            
self-signed-certificates/0*  active    idle   3        10.143.229.160            
```

Congratulations: You have migrated your etcd data to charmed etcd!

### Switch your application over to charmed etcd

Now it's time to connect your application to charmed etcd. This happens over the `etcd_client` interface by integrating
the applications:

```text
juju integrate charmed-etcd <your-charm>
```

The interface requires you to provide a key prefix (the key space in the etcd database you will need access to) and a 
client certificate (for mTLS authentication). Please refer to [charm-relation-interfaces/etcd_client](https://github.com/canonical/charm-relation-interfaces/tree/main/interfaces/etcd_client/v0)
for detailed information.

For further information about how to enable your charm to integrate over the `etcd_client` interface, please refer to [](client-relations.md).
