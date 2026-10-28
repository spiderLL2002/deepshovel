#!/usr/bin/env python3
"""Start this project's tunnel, bypassing fake-IP DNS when a local TUN is active."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import urllib.parse
import urllib.request

REGIONS = ("region1.v2.argotunnel.com", "region2.v2.argotunnel.com")
# Clash Verge's generated configuration on this host uses 28.0.0.1/8.
# Check only the known Tunnel discovery hosts; 28/8 is not generally private.
FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("28.0.0.0/8"),
)


def has_fake_dns() -> bool:
    return any(
        ipaddress.ip_address(item[4][0]) in network
        for host in REGIONS
        for item in socket.getaddrinfo(host, 7844, socket.AF_INET)
        for network in FAKE_IP_NETWORKS
    )


def edge_addresses(host: str) -> list[str]:
    query = urllib.parse.urlencode({"name": host, "type": "A"})
    request = urllib.request.Request(
        "https://cloudflare-dns.com/dns-query?" + query,
        headers={"Accept": "application/dns-json", "User-Agent": "DeepShovel/1.0"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    addresses = []
    for answer in payload.get("Answer", []):
        if answer.get("type") != 1:
            continue
        address = ipaddress.ip_address(answer["data"])
        if address.version == 4 and address.is_global:
            addresses.append(str(address))
    if not addresses:
        raise ValueError("Cloudflare DNS returned no public IPv4 tunnel endpoints")
    return addresses[:2]


def physical_address() -> str:
    route = subprocess.check_output(["/sbin/route", "-n", "get", "default"], text=True, timeout=5)
    interface = next(line.split(":", 1)[1].strip() for line in route.splitlines() if "interface:" in line)
    if not interface.startswith("en"):
        raise ValueError("Default route is not a physical network interface")
    address = subprocess.check_output(["/usr/sbin/ipconfig", "getifaddr", interface], text=True, timeout=5).strip()
    if ipaddress.ip_address(address).version != 4:
        raise ValueError("Physical interface has no IPv4 address")
    return address


def reachable_physical_address(edges: list[str]) -> str | None:
    try:
        bind = physical_address()
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return None
    for address in edges[:2]:
        try:
            with socket.create_connection((address, 7844), timeout=2, source_address=(bind, 0)):
                return bind
        except OSError:
            continue
    return None


def command(binary: str, config: str) -> list[str]:
    args = [binary, "tunnel", "--config", config]
    try:
        if has_fake_dns():
            # Explicit endpoints prevent cloudflared from dialing a synthetic IP.
            edges = [address for host in REGIONS for address in edge_addresses(host)]
            args += ["--protocol", "http2"]
            # Some networks need the current VPN/TUN route. Only retain the
            # physical-interface bypass when that route can reach an edge.
            bind = reachable_physical_address(edges)
            if bind is not None:
                args += ["--edge-bind-address", bind]
            for address in edges:
                args += ["--edge", address + ":7844"]
            route = "the physical interface" if bind else "the current system route"
            print(f"DeepShovel tunnel: fake-IP DNS detected; using resolved Cloudflare endpoints on {route}.", flush=True)
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError) as error:
        print(f"DeepShovel tunnel: direct endpoint discovery unavailable ({type(error).__name__}); using standard discovery.", flush=True)
    return args + ["run"]


if __name__ == "__main__":
    args = command(sys.argv[1], sys.argv[2])
    os.execv(args[0], args)
