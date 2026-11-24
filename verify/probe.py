"""Egress probe. Standard library only, so it runs anywhere Python 3.8+ runs.

`ndani verify` runs it inside each container's network namespace:

    docker run --rm --network container:ndani-inference-gpu ndani/gateway:0.1.0 \
        python probe.py --json --expect-reachable embeddings:8001

Every "escape" probe is expected to FAIL. "Control" probes (things that must
work, like the gateway reaching the model) are expected to SUCCEED, which
proves the escape probes failed because of the guard, not a broken network.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import ssl
import struct
import time

TIMEOUT = float(os.environ.get("NDANI_PROBE_TIMEOUT", "3"))

ESCAPE_PROBES = [
    # (id, description, kind, target)
    ("dns_system", "Resolve a public hostname via the system resolver", "resolve", "example.com"),
    ("dns_direct_udp", "Raw DNS query to a public resolver (DNS tunnelling path)", "dns_udp", "8.8.8.8"),
    ("tcp_ip_443", "TCP to a public IP on 443, bypassing DNS", "tcp", "1.1.1.1:443"),
    ("tcp_ip_80", "TCP to a public IP on 80", "tcp", "1.0.0.1:80"),
    ("https_hf", "HTTPS to huggingface.co (model phone-home path)", "https", "huggingface.co:443"),
    ("cloud_metadata", "Cloud instance metadata service (credential theft path)", "tcp", "169.254.169.254:80"),
    ("tcp_ipv6", "TCP to a public IPv6 address", "tcp6", "[2606:4700:4700::1111]:443"),
]


def _split(target: str) -> tuple[str, int]:
    if target.startswith("["):
        host, port = target[1:].split("]:")
    else:
        host, port = target.rsplit(":", 1)
    return host, int(port)


def try_resolve(host: str) -> tuple[bool, str]:
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
        return True, f"resolved to {', '.join(sorted(addrs))}"
    except socket.gaierror as exc:
        return False, f"resolution failed ({exc.strerror or exc})"


def try_tcp(target: str, family: int = socket.AF_INET) -> tuple[bool, str]:
    host, port = _split(target)
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect((host, port))
        return True, "connected"
    except (socket.timeout, TimeoutError):
        return False, "timed out (dropped)"
    except OSError as exc:
        return False, f"{exc.strerror or exc}"


def try_https(target: str) -> tuple[bool, str]:
    host, port = _split(target)
    try:
        raw = socket.create_connection((host, port), timeout=TIMEOUT)
    except socket.gaierror as exc:
        return False, f"resolution failed ({exc.strerror or exc})"
    except (socket.timeout, TimeoutError):
        return False, "timed out (dropped)"
    except OSError as exc:
        return False, f"{exc.strerror or exc}"
    try:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            first = tls.recv(64).decode(errors="replace").splitlines()[:1]
        return True, f"got response: {first[0] if first else '(empty)'}"
    except OSError as exc:
        return True, f"TCP connected, TLS failed ({exc}) - still an escape"


def try_dns_udp(server: str) -> tuple[bool, str]:
    query_id = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in "example.com".split(".")) + b"\x00"
    packet = header + qname + struct.pack(">HH", 1, 1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendto(packet, (server, 53))
            data, _ = sock.recvfrom(512)
        if len(data) >= 2 and struct.unpack(">H", data[:2])[0] == query_id:
            return True, "got a DNS answer"
        return False, "unexpected reply"
    except (socket.timeout, TimeoutError):
        return False, "no answer (dropped)"
    except OSError as exc:
        return False, f"{exc.strerror or exc}"


def run_probe(kind: str, target: str) -> tuple[bool, str]:
    if kind == "resolve":
        return try_resolve(target)
    if kind == "tcp":
        return try_tcp(target)
    if kind == "tcp6":
        if not socket.has_ipv6:
            return False, "no IPv6 support"
        return try_tcp(target, socket.AF_INET6)
    if kind == "https":
        return try_https(target)
    if kind == "dns_udp":
        return try_dns_udp(target)
    raise ValueError(kind)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--expect-reachable", action="append", default=[],
                        help="host:port that MUST be reachable (control probe)")
    parser.add_argument("--skip", action="append", default=[], help="escape probe id to skip")
    args = parser.parse_args(argv)

    results = []
    for probe_id, description, kind, target in ESCAPE_PROBES:
        if probe_id in args.skip:
            continue
        started = time.monotonic()
        reached, detail = run_probe(kind, target)
        results.append({"id": probe_id, "type": "escape", "description": description,
                        "target": target, "reached": reached, "passed": not reached,
                        "detail": detail, "ms": round((time.monotonic() - started) * 1000)})
    for target in args.expect_reachable:
        started = time.monotonic()
        reached, detail = try_tcp(target)
        results.append({"id": f"control:{target}", "type": "control",
                        "description": f"Must reach {target} (proves the network works)",
                        "target": target, "reached": reached, "passed": reached,
                        "detail": detail, "ms": round((time.monotonic() - started) * 1000)})

    ok = all(r["passed"] for r in results)
    if args.json:
        print(json.dumps({"hostname": socket.gethostname(), "ok": ok, "results": results}))
    else:
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"[{mark}] {r['id']:<28} {r['detail']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
