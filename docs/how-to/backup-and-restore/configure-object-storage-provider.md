# How to configure an object storage provider
A backup for charmed etcd can be stored on any S3- or Azure-compatible storage. This includes:
- AWS S3
- Ceph with RadosGateway (see: [MicroCeph documentation](https://canonical-microceph.readthedocs-hosted.com/en/latest/tutorial/get-started/))
- Google Cloud Storage
- Azure Blob Storage

## Prerequisites
For configuration, you will need:
* the S3 or Azure account credentials
* the connection parameters

It is not required to set up a storage bucket or container in advance. This will be handled by the charmed operator: 
If it does not already exist, the charmed operator will create the storage bucket/container with the configured name
and the default permissions of your account.

```{caution}
Make sure to set up secure permissions on your storage account and bucket/container to avoid leaking sensitive information.
```

## Deploy integrator charm

The configuration and credentials management for the object storage are handled by an intermediate integrator charm. It 
is providing these information to charmed etcd. Depending on the type of object storage, you can use one of:
* `s3-integrator`
* `azure-storage-integrator`

### S3 storage
To deploy the `s3-integrator`, run:
```text
juju deploy s3-integrator
```

Once it is running, you should see an error message with `juju status`:
```text
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.5    unsupported  16:01:25Z

App            Version  Status   Scale  Charm          Channel   Rev  Exposed  Message
charmed-etcd            active       1  charmed-etcd   3.6/edge   49  no       
s3-integrator           blocked      1  s3-integrator  1/stable  145  no       Missing parameters: ['access-key', 'secret-key']

Unit              Workload  Agent  Machine  Public address  Ports  Message
charmed-etcd/0*   active    idle   2        10.198.26.225          
s3-integrator/0*  blocked   idle   1        10.198.26.200          Missing parameters: ['access-key', 'secret-key']
```

Now you need to configure the credentials for your S3 storage. Replace the placeholders and run the following command:
```text
juju run s3-integrator/leader sync-s3-credentials access-key=<access-key> secret-key=<secret-key> 
```

Finally, configure the connection parameters of your S3 storage to `s3-integrator`, like in the example shown below:
```text
juju config s3-integrator \
    endpoint="https://s3.us-west-2.amazonaws.com" \
    bucket="etcd" \
    path="/backups" \
    region="us-west-2"
```

For the full list of supported parameters, see [s3-integrator documentation](https://charmhub.io/s3-integrator/configurations).

### Azure storage
To deploy the `azure-storage-integrator`, run:
```text
juju deploy azure-storage-integrator --channel latest/edge
```

The `azure-storage-integrator` is currently only supported in the `latest/edge` channel.

Once it is running, configure the connection parameters of your Azure storage to `azure-storage-integrator`:
```text
juju config azure-storage-integrator storage-account=<Azure_storage_account> container=<Azure_storage_container>
```

The full list of parameters is available in the [documentation](https://charmhub.io/azure-storage-integrator/configurations).

To provide the credentials, create a secret, allow the `azure-storage-integrator` to read it and add it as a configuration:
```text
juju add-secret mysecret secret-key=<Azure_storage_key>
secret:d183rs296n7svmjrivp0

juju grant-secret mysecret azure-storage-integrator

juju config azure-storage-integrator credentials=secret:d183rs296n7svmjrivp0 
```

## Integrate with charmed-etcd
To provide the configuration and credentials of your object storage to charmed etcd, create a relation between the 
integrator charm and etcd:
```text
juju integrator <storage-integrator-charm> charmed-etcd
```

You can see a confirmation message in the log with `juju debug-log`:
```text
unit-charmed-etcd-0: 16:10:52 INFO unit.charmed-etcd/0.juju-log s3-credentials:3: Using existing bucket etcd
```

## Change storage configuration
If you want to change a connection parameter of your object storage, run the following command:
```text
juju config <storage-integrator-charm> <option>=<value>
```