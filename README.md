# Uncomplicated Vir{sh,tualization}

Wrapper around virsh and lvm to make a "cloud" easier to manage

# Assumption made by this software

* You have two KVM hosts with the same CPU (required for migrations)
* Each KVM can connect over ssh to its twin through the name *otherkvm*
* KVMs' firewalls are set to allow traffic required by qemu for the live migrations (*left as an exercise to the reader*)
* Guests (VMs) use only lvm backed disks, no qcow2/raw
* You have installed on the KVMs *pv*, *python3.6+*, *python3-jinja2*, *python3-libvirt*, *python3-paramiko*, and *zstd*

# Raison d'être

This script was initially written to ease guest migration between servers. I
was checking some stuff manually (which was therefore error-prone) so I decided
to automate it through a script. I don't really like the *virsh(1)* commands.
For instance it bothers me that to start it's "start" but to stop, it's not
"stop" but "shutdown", to hard stop is "destroy", "destroy is not clear for me,
and scary. Note that you can still use these verbs.

# Current features

Here's the current features

```
# uv --help
usage: uv [-h]
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
```

Most of them are pretty close to virsh but create. To use create, take an
existing guest definition and transform it as a jinja template. You can find
template00.xml.j2 in the repository as an example.
