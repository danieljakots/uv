#!/usr/bin/env python3

# Copyright (c) 2019 Daniel Jakots
#
# Licensed under the MIT license. See the LICENSE file.

import argparse
# XXX remove os with subprocess.run
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


def check_logical_volume_on_remote(
    ssh_client, logical_volume_name, logical_volume_size
):
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
        lvcreate_cmd = (
            f"lvcreate -L{logical_volume_size}B "
            + f"-n{logical_volume_name.split('/')[-1]} ubuntu-vg"
        )
        print(lvcreate_cmd)
        copy_remote_answer = input(
            "Should I run this command on remote? ('yes' to confirm)\n"
        )
        if copy_remote_answer != "yes":
            sys.exit(1)
        else:
            stdin, stdout, stderr = ssh_client.exec_command(lvcreate_cmd)
            # Recheck
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
    # XXX split the loop in another function and --no-wait option to stop command
    while is_guest_running(qemu_conn, guest):
        time.sleep(1)
    print("Guest is down")


def start_guest(guest, qemu_conn):
    qemu_conn.lookupByName(guest).create()
    print("Guest has been started")


def crash_guest(guest, qemu_conn):
    qemu_conn.lookupByName(guest).destroy()
    print("Guest has been detroyed")


def list_guests(qemu_conn):
    for guest in qemu_conn.listAllDomains():
        yield guest.name()


def list_disks(qemu_conn, guest):
    xml = qemu_conn.lookupByName(guest).XMLDesc()
    for line in xml.split("\n"):
        if "source dev" in line:
            device = line.split("'")[1]
            yield device


def list_vnc_port(qemu_conn, guest):
    xml = qemu_conn.lookupByName(guest).XMLDesc()
    for line in xml.split("\n"):
        if "graphics type='vnc'" in line:
            return line.split("port='")[1][:4]


def inventary(qemu_conn):
    guests = {}
    for guest in list_guests(qemu_conn):
        disks = {}
        for logical_volume in list_disks(qemu_conn, guest):
            size = check_logical_volume_on_local(logical_volume)
            disks[logical_volume] = size
        guests[guest] = disks
    return guests


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

    undefine_guest(guest)


def undefine_guest(guest):
    print(f"Undefining {guest} on local")
    cmd = ["virsh", "undefine", guest]
    subprocess.run(cmd)


def live_migration(guest):
    # persistent will define the guest on remote
    # undefinesource will undefine the guest on local
    cmd = [
        "virsh",
        "migrate",
        "--verbose",
        "--live",
        "--copy-storage-all",
        "--persistent",
        "--undefinesource",
        guest,
        "qemu+ssh://otherkvm/system",
    ]
    # Using that instead of subprocess.run to have the progress in real time
    print("Running ", " ".join(cmd))
    os.system(" ".join(cmd))


def parse_cli():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        help="Type of action you want to do", dest="verb", required=True
    )

    parser_create = subparsers.add_parser("create", help="Create a new guest")
    parser_create.add_argument("guest", help="Name of the guest")
    parser_create.add_argument("--cpu", help="How many CPU", type=int, default=2)

    parser_start = subparsers.add_parser("start", help="Start an existing guest")
    parser_start.add_argument("guest", help="Name of the guest")

    parser_move = subparsers.add_parser("move", help="Move an existing guest")
    parser_move.add_argument("guest", help="Name of the guest")
    group = parser_move.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true")
    group.add_argument("--offline", action="store_true")
    parser_move.add_argument(
        "--disable-bell",
        action="store_true",
        help="By default it will send a bell to the term once the migration is done",
    )

    parser_stop = subparsers.add_parser(
        "stop", aliases=["shutdown"], help="Stop cleanly an existing guest"
    )
    parser_stop.add_argument("guest", help="Name of the guest")

    parser_reboot = subparsers.add_parser("reboot", help="Reboot an existing guest")
    parser_reboot.add_argument("guest", help="Name of the guest")

    parser_crash = subparsers.add_parser(
        "crash", aliases=["destroy"], help="Pull the plug on an existing guest"
    )
    parser_crash.add_argument("guest", help="Name of the guest")

    parser_list = subparsers.add_parser("list", help="List all existing guests")
    group = parser_list.add_mutually_exclusive_group()
    group.add_argument("--on", action="store_true", help="List only guests powered on")
    group.add_argument(
        "--off", action="store_true", help="List only guests powered off"
    )

    group.add_argument(
        "--vnc", action="store_true", help="Show VNC ports used by the guest"
    )

    parser_delete = subparsers.add_parser("delete", help="Delete an existing guest")
    parser_delete.add_argument("guest", help="Name of the guest")
    parser_delete.add_argument(
        "--yes", help="Don't ask for confirmation", action="store_true"
    )

    return parser.parse_args()


def does_guest_exist(known_guests, guest):
    return guest in known_guests.keys()


def ssh_init():
    # Try to connect, to check if the otherkvm is reachable
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(hostname="otherkvm", username="root", timeout=3)
    except (paramiko.ssh_exception.NoValidConnectionsError, socket.timeout):
        print("NOPE, I can't ssh into the other kvm")
        sys.exit(3)

    return ssh_client


def main():
    qemu_conn = libvirt.open("qemu:///system")

    known_guests = inventary(qemu_conn)
    args = parse_cli()

    if args.verb == "move":
        time_begin = time.time()
        ssh_client = ssh_init()

        if not does_guest_exist(known_guests, args.guest):
            print(f"NOPE: guest {args.guest} not known")
            sys.exit(3)
        # Check all the lv exist on remote
        for lv_name, lv_size in known_guests[args.guest].items():
            print(f"Checking on remote {lv_name} (size {lv_size}B)")
            check_logical_volume_on_remote(ssh_client, lv_name, lv_size)

        if args.offline:
            offline_migration(
                qemu_conn, ssh_client, args.guest, known_guests[args.guest]
            )
        elif args.live:
            live_migration(args.guest)
        if not args.disable_bell:
            # print a bell to notify the migration is done
            print("\a")
        time_end = time.time()
        total_time = int(time_end - time_begin)
        print(f"Migration took {str(total_time)}s")
    elif args.verb == "start":
        if not does_guest_exist(known_guests, args.guest):
            print(f"NOPE: guest {args.guest} not known")
            sys.exit(3)
        if is_guest_running(qemu_conn, args.guest):
            print(f"NOPE: {args.guest} is already running")
            sys.exit(3)
        start_guest(args.guest, qemu_conn)
    elif args.verb == "stop" or args.verb == "shutdown":
        if not does_guest_exist(known_guests, args.guest):
            print(f"NOPE: guest {args.guest} not known")
            sys.exit(3)
        if not is_guest_running(qemu_conn, args.guest):
            print(f"NOPE: {args.guest} is already stopped")
            sys.exit(3)
        shutdown_guest(args.guest, qemu_conn)
    elif args.verb == "reboot":
        if not does_guest_exist(known_guests, args.guest):
            print(f"NOPE: guest {args.guest} not known")
            sys.exit(3)
        if not is_guest_running(qemu_conn, args.guest):
            print(f"NOPE: {args.guest} is already stopped")
            sys.exit(3)
        # It's a stop + start to ensure libvirt rereads the guest definition
        shutdown_guest(args.guest, qemu_conn)
        time.sleep(2)
        start_guest(args.guest, qemu_conn)
    elif args.verb == "crash" or args.verb == "destroy":
        if not does_guest_exist(known_guests, args.guest):
            print(f"NOPE: guest {args.guest} not known")
            sys.exit(3)
        if not is_guest_running(qemu_conn, args.guest):
            print(f"NOPE: {args.guest} is already stopped")
            sys.exit(3)
        crash_guest(args.guest, qemu_conn)
    elif args.verb == "list":
        for guest in known_guests.keys():
            running = is_guest_running(qemu_conn, guest)
            if args.vnc:
                vnc_port = list_vnc_port(qemu_conn, guest)
                print("{:30} {}".format(guest, vnc_port))
            elif running and not args.off:
                print("{:30}  ON".format(guest))
            elif not running and not args.on:
                print("{:30}  OFF".format(guest))
    elif args.verb == "delete":
        if not does_guest_exist(known_guests, args.guest):
            print(f"NOPE: guest {args.guest} not known")
            sys.exit(3)
        if is_guest_running(qemu_conn, args.guest):
            print("Guest is still running, please shut it down first")
            sys.exit(3)

        if not args.yes:
            confirmation = input(f"Confirm you want to delete {args.guest} ('yes' to confirm)?\n")
            if confirmation != "yes":
                sys.exit(3)
        undefine_guest(guest)
    elif args.verb == "create":
        print("Unsupported actions for now")


if __name__ == "__main__":
    main()
