#!/usr/bin/env python3

# Copyright (c) 2019 Daniel Jakots
#
# Licensed under the MIT license. See the LICENSE file.


import os
import socket
import subprocess
import sys
import time

import libvirt
import paramiko

DD_BS = 4096
ZSTD_LEVEL = 6


def is_guest_running(qemu_conn, guest):
    dom = qemu_conn.lookupByName(guest)
    return dom.isActive()


def check_logical_volume_on_local(logical_volume):
    local_cmd = [
        "lvs",
        logical_volume,
        "-o",
        "LV_SIZE",
        "--noheadings",
        "--units",
        "B",
        "--nosuffix",
    ]
    local_result = subprocess.run(
        local_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8"
    )
    if local_result.returncode != 0:
        print(f"I can't find {logical_volume} on local")
        sys.exit(1)

    # in Bytes
    size = local_result.stdout.strip()
    return size


def check_logical_volume_on_remote(ssh_client, logical_volume_name, logical_volume_size):
    remote_cmd = [
        "ssh",
        "otherkvm",
        f"lvs {logical_volume_name} -o LV_SIZE --noheadings --units B --nosuffix",
    ]

    remote_result = subprocess.run(
        remote_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8"
    )
    if remote_result.returncode != 0:
        print(f"I can't find {logical_volume_name} on remote")
        lvcreate_cmd = f"lvcreate -L{logical_volume_size}B -n{logical_volume_name.split('/')[-1]} ubuntu-vg"
        print(lvcreate_cmd)
        copy_remote_answer = input("Should I run this command on remote? (yes)\n")
        if copy_remote_answer != "yes":
            sys.exit(1)
        else:
            stdin, stdout, stderr = ssh_client.exec_command(lvcreate_cmd)
            remote_result = subprocess.run(
                remote_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                encoding="utf-8",
            )
    local_size = logical_volume_size
    remote_size = remote_result.stdout.strip()
    if local_size != remote_size:
        print(f"{logical_volume_name} is not the same size on local vs remote:")
        print(f"{local_size}B on local VS {remote_size}B on remote")
        sys.exit(1)


def copy_logical_volume(logical_volume_name, logical_volume_size):
    print(f"Copying {logical_volume_name}")
    cmd = [
        "dd",
        "if=" + logical_volume_name,
        "bs=" + str(DD_BS),
        "|",
        f"pv --size {str(logical_volume_size).strip()}",
        "|",
        "zstd",
        f"-{ZSTD_LEVEL}",
        "|",
        "ssh",
        "otherkvm",
        f"'zstd -d | dd of={logical_volume_name} bs={str(DD_BS)}'",
    ]
    # subprocess.run does't seem to work :(
    print(" ".join(cmd))
    os.system(" ".join(cmd))


def copy_definition(guest, ssh_client):
    sftp_client = ssh_client.open_sftp()
    sftp_client.put(f"/root/{guest}.xml", f"/root/{guest}.xml")
    sftp_client.close()


def shutdown_guest(guest, qemu_conn):
    qemu_conn.lookupByName(guest).shutdown()
    print("Guest has been shat down")
    while is_guest_running(qemu_conn, guest):
        time.sleep(1)
    print("Guest is down")


def list_guests(qemu_conn):
    for guest in qemu_conn.listAllDomains():
        yield guest.name()


def list_disks(qemu_conn, guest):
    xml = qemu_conn.lookupByName(guest).XMLDesc()
    for line in xml.split("\n"):
        if "source dev" in line:
            device = line.split("'")[1]
            yield device


def inventary(qemu_conn):
    guests = {}
    for guest in list_guests(qemu_conn):
        disks = {}
        for logical_volume in list_disks(qemu_conn, guest):
            size = check_logical_volume_on_local(logical_volume)
            disks[logical_volume] = size
        guests[guest] = disks
    return guests


def what_to_move(known_guests):
    if len(sys.argv) == 1:
        guest = input("Which VM to move? ")
    else:
        guest = sys.argv[1]
    if guest not in known_guests.keys():
        print(f"NOPE: guest {guest} not known")
        print("Known guests are")
        print(known_guests.keys())
        sys.exit(1)
    return guest


def offline_migration(qemu_conn, ssh_client, guest, logical_volumes_dict):
    # Shutdown the guest if it runs and wait until it's down
    print(f"Moving {guest}")
    if not is_guest_running(qemu_conn, guest):
        print("Guest is not running, confirm to continue")
        input()
    else:
        print("Guest is running, shutthing it down")
        shutdown_guest(guest, qemu_conn)
    qemu_conn.close()

    # Get the disk(s) on the other host
    for logical_volume_name, logical_volume_size in logical_volumes_dict.items():
        copy_logical_volume(logical_volume_name, logical_volume_size)


def main():
    qemu_conn = libvirt.open("qemu:///system")

    # Try to connect, to check if the otherkvm is reachable
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(hostname="otherkvm", username="root", timeout=3)
    except (paramiko.ssh_exception.NoValidConnectionsError, socket.timeout):
        print("NOPE, I can't ssh into the other kvm")
        sys.exit(3)

    # Find out which guests run on the kvm and get the user choice
    known_guests = inventary(qemu_conn)
    guest = what_to_move(known_guests)

    # Check all the lv exist on remote
    for logical_volume_name, logical_volume_size in known_guests[guest].items():
        print(f"Checking {logical_volume_name} (size {logical_volume_size}B)")
        check_logical_volume_on_remote(ssh_client, logical_volume_name, logical_volume_size)

    offline_migration(qemu_conn, ssh_client, guest, known_guests[guest])

    # Dump the guest definition and ship it to the other kvm
    cmd = ["virsh", "dumpxml", guest]
    with open(f"/root/{guest}.xml", "w") as f:
        subprocess.run(cmd, stdout=f)
    copy_definition(guest, ssh_client)

    print(f"Defining {guest} on remote")
    ssh_client.exec_command(f"virsh define /root/{guest}.xml")

    # It might be too fast otherwise
    time.sleep(1)
    print(f"Starting {guest} on remote")
    ssh_client.exec_command(f"virsh start {guest}")

    print(f"Undefining {guest} on local")
    cmd = ["virsh", "undefine", guest]
    subprocess.run(cmd)


if __name__ == "__main__":
    main()