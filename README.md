# Uncomplicated Vir{sh,tualization}

Wrapper around virsh and lvm to make a "cloud" easier to manage

# Assumption made by this software

* You have two KVM hosts with the same CPU feature
* Each KVM can connect to its twin over ssh through the name *otherkvm*
* KVMs' firewalls are set to allow traffic required by qemu for the live migrations (*left as an exercise to the reader*)
* Guests (VMs) use only lvm backed disks, no qcow2/raw
* You have installed on the KVMs *python3.6+*, *python3-libvirt*, *python3-paramiko*, and zstd

# Current support

```
# ./uv.py --help
usage: uv.py [-h] {move,start,stop,shutdown,crash,destroy} ...

positional arguments:
  {create,move,delete,start,stop,shutdown,crash,destroy}
                        Type of action you want to do
    move                Move an existing guest
    start               Start an existing guest
    stop (shutdown)     Stop cleanly an existing guest
    crash (destroy)     Pull the plug on an existing guest

optional arguments:
  -h, --help            show this help message and exit
```

```
# ./uv.py start --help
usage: uv.py start [-h] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
```

```
# ./uv.py stop --help
usage: uv.py stop [-h] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
```

```
# ./uv.py crash --help
usage: uv.py crash [-h] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
```

```
# ./uv.py move --help
usage: uv.py move [-h] (--live | --offline) [--disable-bell] guest

positional arguments:
  guest           Name of the guest

optional arguments:
  -h, --help      show this help message and exit
  --live
  --offline
  --disable-bell  By default it will send a bell to the term once the
                  migration is done
```

# Planned support

Guest creation
```
usage: uv.py create [-h] [--cpu CPU] guest
```

Guest deletion

```
usage: uv.py [-h] {create,move,delete} ...
```
