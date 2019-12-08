# Uncomplicated Vir{sh,tualization}

Wrapper around virsh and lvm to make a "cloud" easier to manage

# Assumption made by this software

* You have two KVM hosts with the same CPU (required for migrations)
* Each KVM can connect over ssh to its twin through the name *otherkvm*
* KVMs' firewalls are set to allow traffic required by qemu for the live migrations (*left as an exercise to the reader*)
* Guests (VMs) use only lvm backed disks, no qcow2/raw
* You have installed on the KVMs *pv*, *python3.6+*, *python3-jinja2*, *python3-libvirt*, *python3-paramiko*, and *zstd*

# Current features

```
# ./uv.py --help
usage: uv.py [-h]
             {create,start,move,stop,shutdown,reboot,crash,destroy,list,delete}
             ...

positional arguments:
  {create,start,move,stop,shutdown,reboot,crash,destroy,list,delete}
                        Type of action you want to do
    create              Create a new guest
    start               Start an existing guest
    move                Move an existing guest
    stop (shutdown)     Stop cleanly an existing guest
    reboot              Reboot an existing guest
    crash (destroy)     Pull the plug on an existing guest
    list                List all existing guests
    delete              Delete an existing guest

optional arguments:
  -h, --help            show this help message and exit
```

## Create a guest

```
# ./uv.py create --help
usage: uv.py create [-h] --template TEMPLATE --cpu CPU --ram RAM --mac MAC
                    --vnc VNC
                    guest

positional arguments:
  guest                Name of the guest

optional arguments:
  -h, --help           show this help message and exit
  --template TEMPLATE  Which template to use for the definition
  --cpu CPU            How many CPU
  --ram RAM            How much RAM (in G)
  --mac MAC            Which mac address
  --vnc VNC            Which tcp port for VNC
```

## Start a guest

```
# ./uv.py start --help
usage: uv.py start [-h] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
```

## Stop a guest

```
# ./uv.py stop --help
usage: uv.py stop [-h] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
```

## Reboot a guest

```
# ./uv.py reboot --help
usage: uv.py reboot [-h] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
```

## Crash a guest

```
# ./uv.py crash --help
usage: uv.py crash [-h] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
```

## Move a guest

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

## List guests

```
# ./uv.py list --help
usage: uv.py list [-h] [--on | --off | --vnc]

optional arguments:
  -h, --help  show this help message and exit
  --on        List only guests powered on
  --off       List only guests powered off
  --vnc       Show VNC ports used by the guest
```

## Delete a guest

```
# ./uv.py delete --help
usage: uv.py delete [-h] [--yes] guest

positional arguments:
  guest       Name of the guest

optional arguments:
  -h, --help  show this help message and exit
  --yes       Don't ask for confirmation
```

