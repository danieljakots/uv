# Uncomplicated Vir{sh,tualization}

Wrapper around virsh and lvm to make a "cloud" easier to manage

# Assumption made by this software

* You have two KVM hosts with the same CPU feature
* Each KVM can connect to its twin over ssh through the name *otherkvm*
* KVMs' firewalls are set to allow traffic required by qemu for the live migrations (*left as an exercise to the reader*)
* Guests (VMs) use only lvm backed disks, no qcow2/raw
* You have installed on the KVMs *python3.6+*, *python3-libvirt*, *python3-paramiko*, and zstd

# Current support

It supports both *live* and *offline* migration

```
# usage: uv.py move [-h] (--live | --offline) guest
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
