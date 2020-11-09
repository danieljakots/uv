#!/usr/bin/env python3

# Copyright (c) 2019, 2020 Daniel Jakots
#
# Licensed under the MIT license. See the LICENSE file.

import argparse
# XXX remove os with subprocess.run
import os
import re
import socket
import subprocess
import sys
import time
import uuid

import jinja2
import libvirt
import paramiko

DD_BS = 4096
ZSTD_LEVEL = 6


def copy_disk_from_template(template, known_guests, guest):
    logical_volume_size = known_guests[template]["disks"][f"/dev/ubuntu-vg/{template}"]
    if check_logical_volume_on_local(f"/dev/ubuntu-vg/{guest}"):
        print(f"NOPE: logical volume for {guest} already exists")
        sys.exit(3)
    template = template.split(".")[0]
    if "/" in template:
        template = template.split("/")[-1]
    create_new_lv(guest, logical_volume_size)
    print(f"Copying data from {template}")
    lvcopy_cmd = [
        "dd",
        f"if=/dev/ubuntu-vg/{template}",
        f"of=/dev/ubuntu-vg/{guest}",
        f"bs={str(DD_BS)}",
    ]
    result = subprocess.run(
        lvcopy_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8"
    )
    if result.returncode != 0:
        print(f"{lvcopy_cmd} didn't work")
        sys.exit(3)


def create_new_lv(name, logical_volume_size):
    lvcreate_cmd = [
        "lvcreate",
        f"-L{logical_volume_size}B",
        f"-n{name}",
        "ubuntu-vg",
    ]
    print("Creating new LV")
    result = subprocess.run(
        lvcreate_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"{lvcreate_cmd} didn't work")
        sys.exit(3)


def create_guest_from_template(args, known_guests, disk_size):
    if disk_size == 0:
        copy_disk_from_template(args.template, known_guests, args.guest)
    else:
        create_new_lv(args.guest, disk_size)

    # Check mac address
    comp = re.compile("^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")
    if not comp.fullmatch(args.mac):
        print("Given mac address is invalid")
        sys.exit(3)
    guest_uuid = uuid.uuid4()
    # size is given in G and libvirt takes K
    if args.ram > 8:
        print("NOPE: ram is too big. Unit mismatch?")
        sys.exit(3)
    ram = int(args.ram * 1024 * 1024)
    new_guest = {
        "name": args.guest,
        "cpu": args.cpu,
        "ram": ram,
        "id": guest_uuid,
        "vnc": args.vnc,
        "disk": f"/dev/ubuntu-vg/{args.guest}",
        "mac": args.mac,
    }
    with open(args.template, "r") as f:
        template = f.read()
    jinja2_template = jinja2.Template(template)
    new_guest_definition = jinja2_template.render(new_guest=new_guest)
    with open(f"/etc/libvirt/qemu/{args.guest}.xml", "w") as f:
        f.write(new_guest_definition)
        f.write("\n")
    print("Definining new guest")
    os.system(f"virsh define /etc/libvirt/qemu/{args.guest}.xml")


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
        return 0

    # in Bytes
    size = local_result.stdout.strip()
    return size


def make_logical_volume_on_remote(
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
            sys.exit(3)
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
        sys.exit(3)


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


def wait_for_guest_down(guest, qemu_conn):
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


def list_cpu_ram(qemu_conn, guest):
    xml = qemu_conn.lookupByName(guest).XMLDesc()
    cpu = 0
    for line in xml.split("\n"):
        if "vcpu placement" in line:
            cpu = line.split(">")[1].split("<")[0]

    ram = 0
    for line in xml.split("\n"):
        if "memory unit" in line:
            ram = int(int(line.split(">")[1].split("<")[0]) / 1024)

    return cpu, ram


def list_vnc_port(qemu_conn, guest):
    xml = qemu_conn.lookupByName(guest).XMLDesc()
    for line in xml.split("\n"):
        if "graphics type='vnc'" in line:
            return line.split("port='")[1][:4]


def inventary(qemu_conn):
    guests = {}
    for guest in list_guests(qemu_conn):
        guests[guest] = {}
        disks = {}
        for logical_volume in list_disks(qemu_conn, guest):
            size = check_logical_volume_on_local(logical_volume)
            disks[logical_volume] = size
        guests[guest]["disks"] = disks
        cpu, ram = list_cpu_ram(qemu_conn, guest)
        guests[guest]["cpu"] = str(cpu)
        guests[guest]["ram"] = str(ram)
    return guests


def offline_migration(qemu_conn, ssh_client, guest, logical_volumes_dict):
    # Shutdown the guest if it runs and wait until it's down
    print(f"Moving {guest}")
    guest_was_running = False
    if is_guest_running(qemu_conn, guest):
        guest_was_running = True
        print("Guest is running, shutthing it down")
        shutdown_guest(guest, qemu_conn)
        wait_for_guest_down(guest, qemu_conn)
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
    if guest_was_running:
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
    parser_create.add_argument(
        "--template",
        help="Which template to use for the definition",
        default=2,
        required=True,
    )
    parser_create.add_argument(
        "--cpu", help="How many CPU", type=int, default=2, required=True
    )
    parser_create.add_argument(
        "--ram", help="How much RAM (in G)", type=float, default=2, required=True
    )
    parser_create.add_argument("--mac", help="Which mac address", required=True)
    parser_create.add_argument(
        "--vnc", help="Which tcp port for VNC", type=int, required=True
    )
    group_create = parser_create.add_mutually_exclusive_group(required=True)
    group_create.add_argument("--disk-size", help="Size of the disk", type=int)
    group_create.add_argument(
        "--copy-disk",
        action="store_true",
        help="Add this option if you want to copy the disk from a template",
    )

    parser_start = subparsers.add_parser("start", help="Start an existing guest")
    parser_start.add_argument("guest", help="Name of the guest")

    parser_move = subparsers.add_parser("move", help="Move an existing guest")
    parser_move.add_argument("guest", help="Name of the guest")
    group_move = parser_move.add_mutually_exclusive_group(required=True)
    group_move.add_argument("--live", action="store_true")
    group_move.add_argument("--offline", action="store_true")
    parser_move.add_argument(
        "--disable-bell",
        action="store_true",
        help="By default it will send a bell to the term once the migration is done",
    )

    parser_stop = subparsers.add_parser(
        "stop", aliases=["shutdown"], help="Stop cleanly an existing guest"
    )
    parser_stop.add_argument("guest", help="Name of the guest")
    parser_stop.add_argument(
        "--no-wait", help="Don't block until the guest is down", action="store_false"
    )

    parser_reboot = subparsers.add_parser("reboot", help="Reboot an existing guest")
    parser_reboot.add_argument("guest", help="Name of the guest")

    parser_crash = subparsers.add_parser(
        "crash", aliases=["destroy"], help="Pull the plug on an existing guest"
    )
    parser_crash.add_argument("guest", help="Name of the guest")

    subparsers.add_parser("list", help="List all existing guests")

    parser_delete = subparsers.add_parser(
        "delete", aliases=["rm"], help="Delete an existing guest"
    )
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


def check_guest_exists_runs(qemu_conn, known_guests, guest, should_be_running):
    if not does_guest_exist(known_guests, guest):
        print(f"NOPE: guest {guest} not known")
        sys.exit(3)
    if is_guest_running(qemu_conn, guest) and not should_be_running:
        print(f"NOPE: {guest} is already running")
        sys.exit(3)
    elif not is_guest_running(qemu_conn, guest) and should_be_running:
        print(f"NOPE: {guest} is already stopped")
        sys.exit(3)


def print_guests(guest, vnc_port, cpu, ram, status, disks):
    print("{:15} {:4} {:5} {:5} {:6} {}".format(guest, cpu, ram, vnc_port, status, disks))


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
        for lv_name, lv_size in known_guests[args.guest]["disks"].items():
            print(f"Checking on remote {lv_name} (size {lv_size}B)")
            make_logical_volume_on_remote(ssh_client, lv_name, lv_size)

        if args.offline:
            offline_migration(
                qemu_conn, ssh_client, args.guest, known_guests[args.guest]["disks"]
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
        should_be_running = False
        check_guest_exists_runs(qemu_conn, known_guests, args.guest, should_be_running)
        start_guest(args.guest, qemu_conn)
    elif args.verb == "stop" or args.verb == "shutdown":
        should_be_running = True
        check_guest_exists_runs(qemu_conn, known_guests, args.guest, should_be_running)
        shutdown_guest(args.guest, qemu_conn)
        # action="store_false" so if it's true, the flag wasn't given
        if args.no_wait:
            wait_for_guest_down(args.guest, qemu_conn)
    elif args.verb == "reboot":
        should_be_running = True
        check_guest_exists_runs(qemu_conn, known_guests, args.guest, should_be_running)
        # It's a stop + start to ensure libvirt rereads the guest definition
        shutdown_guest(args.guest, qemu_conn)
        wait_for_guest_down(args.guest, qemu_conn)
        time.sleep(2)
        start_guest(args.guest, qemu_conn)
    elif args.verb == "crash" or args.verb == "destroy":
        should_be_running = True
        check_guest_exists_runs(qemu_conn, known_guests, args.guest, should_be_running)
        crash_guest(args.guest, qemu_conn)
    elif args.verb == "list":
        print_guests("GUEST", "VNC", "CPU", "RAM", "STATUS", "DISKS")
        for guest in known_guests.keys():
            running = is_guest_running(qemu_conn, guest)
            vnc_port = list_vnc_port(qemu_conn, guest)
            if running:
                status = "ON"
            else:
                status = "OFF"
            disks = {}
            for disk_name, disk_size in known_guests[guest]["disks"].items():
                # basename(1) equivalent
                disk_name = disk_name.rpartition("/")[-1]
                disk_size = f"{int(int(disk_size) / 1024 / 1024 / 1024)}G"
                disks[disk_name] = disk_size
            print_guests(
                guest,
                vnc_port,
                known_guests[guest]["cpu"],
                known_guests[guest]["ram"],
                status,
                disks,
            )
    elif args.verb == "delete" or args.verb == "rm":
        should_be_running = False
        check_guest_exists_runs(qemu_conn, known_guests, args.guest, should_be_running)
        if not args.yes:
            confirmation = input(
                f"Confirm you want to delete {args.guest} ('{args.guest}' to confirm)?\n"
            )
            if confirmation != args.guest:
                sys.exit(3)
        undefine_guest(args.guest)
    elif args.verb == "create":
        if args.copy_disk:
            disk = 0
        else:
            disk = args.disk_size * 1024 * 1024 * 1024
            if disk == 0:
                print("NOPE: 0 is not a valid disk size")
                sys.exit(3)
        create_guest_from_template(args, known_guests, disk)


if __name__ == "__main__":
    main()
