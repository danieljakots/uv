#!/usr/bin/env python3

# Copyright (c) 2019, 2020, 2021 Daniel Jakots
#
# Licensed under the MIT license. See the LICENSE file.

import argparse
import subprocess
import sys
import time

import libvirt

DD_BS = 4096
ZSTD_LEVEL = 6


def is_guest_running(qemu_conn, guest):
    dom = qemu_conn.lookupByName(guest)
    return dom.isActive()


def get_zvol_size(zvol):
    # remove /dev/zvol/
    zvol = zvol[10:]
    # zfs get -o value -Hp volsize zpool0/zvol/docker5
    cmd = [
        "zfs",
        "get",
        "-o",
        "value",
        "-Hp",
        "volsize",
        zvol
    ]

    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8"
    )
    if result.returncode != 0:
        return 0

    # in Bytes
    size = result.stdout.strip()
    return size


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
        for zvol in list_disks(qemu_conn, guest):
            size = get_zvol_size(zvol)
            disks[zvol] = size
        guests[guest]["disks"] = disks
        cpu, ram = list_cpu_ram(qemu_conn, guest)
        guests[guest]["cpu"] = str(cpu)
        guests[guest]["ram"] = str(ram)
    return guests


def undefine_guest(guest):
    print(f"Undefining {guest} on local")
    cmd = ["virsh", "undefine", guest]
    subprocess.run(cmd)


def parse_cli():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        help="Type of action you want to do", dest="verb", required=True
    )

    parser_start = subparsers.add_parser("start", help="Start an existing guest")
    parser_start.add_argument("guest", help="Name of the guest")

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
    print(
        "{:15} {:4} {:5} {:5} {:6} {}".format(guest, cpu, ram, vnc_port, status, disks)
    )


def main():
    qemu_conn = libvirt.open("qemu:///system")

    known_guests = inventary(qemu_conn)
    args = parse_cli()

    if args.verb == "start":
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


if __name__ == "__main__":
    main()
