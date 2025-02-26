# Manage persistent storage

>>>
storage:
  data:
    type: filesystem
    location: /var/snap/charmed-etcd/common/var/lib/etcd
    description: storage for etcd data
    minimum-size: 1G
  logs:
    type: filesystem
    location: /var/snap/charmed-etcd/common/var/log/etcd
    description: storage for logfiles of etcd server
    minimum-size: 1G
<<<


## Prerequisites
https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-storage/
https://canonical-juju.readthedocs-hosted.com/en/latest/user/howto/manage-storage-pools/#manage-storage-pools
juju create-storage-pool etcd-storage lxd volume-type=standard
juju deploy charmed-etcd -n 3 --storage data=etcd-storage,8G,1

$ juju status --storage
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


## Same Cluster Scenario

$ juju remove-unit charmed-etcd/0 charmed-etcd/1 charmed-etcd/2
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

#############################################################################################

$ juju status --storage
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:02:34Z

App           Version  Status   Scale  Charm         Channel  Rev  Exposed  Message
charmed-etcd           unknown      0  charmed-etcd             0  no       

Storage Unit  Storage ID  Type        Pool          Mountpoint  Size     Status    Message
              data/0      filesystem  etcd-storage              8.0 GiB  detached  
              data/2      filesystem  etcd-storage              8.0 GiB  detached  
              data/4      filesystem  etcd-storage              8.0 GiB  detached  

#############################################################################################

$ juju add-unit charmed-etcd --attach-storage=data/0

$ juju status --storage --watch=3s
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

$ juju add-unit charmed-etcd --attach-storage=data/2
$ juju add-unit charmed-etcd --attach-storage=data/4
$ juju status --storage --watch=3s
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


## Different Cluster Scenario

### Safe scale-down
BEFORE YOU REMOVE YOUR ETCD CLUSTER!!!!
set a user-defined password for the admin user and configure it to charmed etcd.

$ juju add-secret mysecret root=changeme
secret:cuvh9ggv7vbc46jefvjg
$ juju grant-secret mysecret charmed-etcd
$ juju config charmed-etcd system-users=secret:cuvh9ggv7vbc46jefvjg


$ juju remove-application charmed-etcd
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


################################################################################


$ juju status --storage
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:11:11Z

Storage Unit  Storage ID  Type        Pool          Mountpoint  Size     Status    Message
              data/0      filesystem  etcd-storage              8.0 GiB  detached  
              data/2      filesystem  etcd-storage              8.0 GiB  detached  
              data/4      filesystem  etcd-storage              8.0 GiB  detached  

Model "admin/etcd" is empty.



### Deploy new cluster with existing storage

$ juju deploy charmed-etcd --attach-storage=data/0 --config system-users=secret:cuvh9ggv7vbc46jefvjg
$ juju grant-secret mysecret charmed-etcd
$ juju status --storage --watch=3s
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




$ juju add-unit charmed-etcd --attach-storage=data/2
$ juju add-unit charmed-etcd --attach-storage=data/4
$ juju status --storage --watch=3s



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

## Remove persistent storage

$ juju remove-application charmed-etcd --destroy-storage
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


$ juju status --storage
Model  Controller      Cloud/Region         Version  SLA          Timestamp
etcd   dev-controller  localhost/localhost  3.6.0    unsupported  13:32:30Z

Model "admin/etcd" is empty.



$ juju storage
No storage to display.


$ juju secrets
ID                    Name      Owner    Rotation  Revision  Last updated
cuvh9ggv7vbc46jefvjg  mysecret  <model>  never            1  14 minutes ago  
$ juju remove-secret cuvh9ggv7vbc46jefvjg
