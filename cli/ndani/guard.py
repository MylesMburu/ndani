"""Host egress guard.

Two layers, both default-deny, both logging with the prefix NDANI-EGRESS so
`ndani verify` can show evidence that blocked attempts actually happened:

1. Host OUTPUT (nftables, table `inet ndani_guard`): the host itself can only
   reach loopback, replies to inbound connections, its own Docker bridges and an
   explicit allowlist (e.g. VPC endpoint CIDRs).
2. Container FORWARD (iptables chain NDANI-EGRESS hooked into DOCKER-USER):
   any NEW connection that starts on an Ndani bridge and leaves it is logged
   and dropped. The gateway can answer users; it can never dial out.

Docker manages FORWARD through iptables, so layer 2 uses iptables (which is
iptables-nft on modern distros) to sit in the chain Docker guarantees to honour.
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from dataclasses import dataclass

BRIDGES = ("ndani-edge", "ndani-sealed")
NFT_TABLE = "ndani_guard"
CHAIN = "NDANI-EGRESS"
LOG_PREFIX_HOST = "NDANI-EGRESS host "
LOG_PREFIX_CONTAINER = "NDANI-EGRESS ctr "
AWS_METADATA = "169.254.169.254"
AWS_TIME = "169.254.169.123"


@dataclass
class GuardPolicy:
    allow_cidrs: list[str]
    allow_ports: list[int]
    allow_dns_servers: list[str]
    allow_aws_metadata: bool = False
    allow_aws_time_sync: bool = False

    @classmethod
    def from_config(cls, guard: dict) -> "GuardPolicy":
        return cls(
            allow_cidrs=list(guard.get("allow_cidrs", [])),
            allow_ports=[int(p) for p in guard.get("allow_ports", [443])],
            allow_dns_servers=list(guard.get("allow_dns_servers", [])),
            allow_aws_metadata=bool(guard.get("allow_aws_metadata", False)),
            allow_aws_time_sync=bool(guard.get("allow_aws_time_sync", False)),
        )


def _family(cidr: str) -> str:
    return "ip6" if ipaddress.ip_network(cidr, strict=False).version == 6 else "ip"


def nft_script(policy: GuardPolicy) -> str:
    ports = ", ".join(str(p) for p in sorted(set(policy.allow_ports))) or "443"
    lines = [
        f"table inet {NFT_TABLE}",
        f"delete table inet {NFT_TABLE}",
        f"table inet {NFT_TABLE} {{",
        "  chain output {",
        "    type filter hook output priority filter; policy drop;",
        '    oifname "lo" accept',
        "    ct state established,related accept",
        "    # host -> its own containers (health checks, `ndani verify`)",
        '    oifname { "docker0", ' + ", ".join(f'"{b}"' for b in BRIDGES) + " } accept",
        "    # IPv6 neighbour discovery, otherwise v6 networking breaks locally",
        "    icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit } accept",
    ]
    if policy.allow_aws_metadata:
        lines.append(f"    ip daddr {AWS_METADATA} tcp dport 80 meta skuid 0 accept  # SSM agent (root only)")
    if policy.allow_aws_time_sync:
        lines.append(f"    ip daddr {AWS_TIME} udp dport 123 accept")
    for server in policy.allow_dns_servers:
        fam = _family(server)
        lines.append(f"    {fam} daddr {server} udp dport 53 accept")
        lines.append(f"    {fam} daddr {server} tcp dport 53 accept")
    for cidr in policy.allow_cidrs:
        lines.append(f"    {_family(cidr)} daddr {cidr} tcp dport {{ {ports} }} accept")
    lines += [
        f'    limit rate 10/second log prefix "{LOG_PREFIX_HOST}" level warn',
        "    counter drop",
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def iptables_commands(binary: str = "iptables") -> list[list[str]]:
    """Commands to (re)build the container egress chain idempotently."""
    cmds: list[list[str]] = [
        [binary, "-N", CHAIN],            # may fail if it exists; caller ignores that
        [binary, "-F", CHAIN],
        [binary, "-A", CHAIN, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN"],
    ]
    for bridge in BRIDGES:
        # traffic staying on the same bridge (container <-> container) is allowed
        cmds.append([binary, "-A", CHAIN, "-i", bridge, "-o", bridge, "-j", "RETURN"])
    cmds += [
        [binary, "-A", CHAIN, "-m", "limit", "--limit", "10/second", "-j", "LOG",
         "--log-prefix", LOG_PREFIX_CONTAINER, "--log-level", "4"],
        [binary, "-A", CHAIN, "-j", "DROP"],
    ]
    for bridge in BRIDGES:
        cmds.append([binary, "-I", "DOCKER-USER", "-i", bridge, "-j", CHAIN])
    return cmds


def _run(cmd: list[str], check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, text=True, capture_output=True, check=check)


def _hook_exists(binary: str, bridge: str) -> bool:
    return _run([binary, "-C", "DOCKER-USER", "-i", bridge, "-j", CHAIN], check=False).returncode == 0


def apply(policy: GuardPolicy, dry_run: bool = False) -> list[str]:
    actions: list[str] = []
    script = nft_script(policy)
    if dry_run:
        actions.append("nft -f - <<EOF\n" + script + "EOF")
    else:
        if not shutil.which("nft"):
            raise RuntimeError("nft not found: install nftables")
        _run(["nft", "-f", "-"], stdin=script)
        actions.append(f"nftables table inet {NFT_TABLE} loaded")

    for binary in ("iptables", "ip6tables"):
        if not dry_run and not shutil.which(binary):
            continue
        for cmd in iptables_commands(binary):
            if dry_run:
                actions.append(" ".join(cmd))
                continue
            if cmd[1] == "-I" and _hook_exists(binary, cmd[4]):
                continue
            if cmd[1] == "-I" and _run([binary, "-L", "DOCKER-USER", "-n"], check=False).returncode != 0:
                raise RuntimeError("DOCKER-USER chain missing: is Docker running?")
            _run(cmd, check=cmd[1] != "-N")
        if not dry_run:
            actions.append(f"{binary} chain {CHAIN} installed on {', '.join(BRIDGES)}")
    return actions


def remove() -> list[str]:
    actions = []
    if shutil.which("nft") and _run(["nft", "delete", "table", "inet", NFT_TABLE], check=False).returncode == 0:
        actions.append(f"removed nftables table {NFT_TABLE}")
    for binary in ("iptables", "ip6tables"):
        if not shutil.which(binary):
            continue
        for bridge in BRIDGES:
            while _hook_exists(binary, bridge):
                _run([binary, "-D", "DOCKER-USER", "-i", bridge, "-j", CHAIN], check=False)
        _run([binary, "-F", CHAIN], check=False)
        if _run([binary, "-X", CHAIN], check=False).returncode == 0:
            actions.append(f"removed {binary} chain {CHAIN}")
    return actions


def status() -> dict:
    info = {"nft_table": False, "container_chain": False, "blocked_log_lines": None}
    if shutil.which("nft"):
        info["nft_table"] = _run(["nft", "list", "table", "inet", NFT_TABLE], check=False).returncode == 0
    if shutil.which("iptables"):
        info["container_chain"] = all(_hook_exists("iptables", b) for b in BRIDGES)
    lines = blocked_log_lines()
    info["blocked_log_lines"] = len(lines) if lines is not None else None
    return info


def drop_counters() -> dict[str, int]:
    """Packets dropped so far by each layer. Readable even when kernel logs are not."""
    import re
    counts = {"host": 0, "containers": 0}
    if shutil.which("nft"):
        out = _run(["nft", "list", "table", "inet", NFT_TABLE], check=False).stdout
        m = re.search(r"counter packets (\d+) bytes \d+ drop", out)
        counts["host"] = int(m.group(1)) if m else 0
    for binary in ("iptables", "ip6tables"):
        if not shutil.which(binary):
            continue
        out = _run([binary, "-L", CHAIN, "-v", "-x", "-n"], check=False).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[2] == "DROP":
                counts["containers"] += int(parts[0])
    return counts


def blocked_log_lines(since: str | None = None) -> list[str] | None:
    """Kernel log lines written by the guard. None if no log source is readable."""
    if shutil.which("journalctl"):
        cmd = ["journalctl", "-k", "--no-pager", "-o", "short-iso"]
        if since:
            cmd += ["--since", since]
        proc = _run(cmd, check=False)
        if proc.returncode == 0:
            return [l for l in proc.stdout.splitlines() if "NDANI-EGRESS" in l]
    proc = _run(["dmesg"], check=False) if shutil.which("dmesg") else None
    if proc and proc.returncode == 0:
        return [l for l in proc.stdout.splitlines() if "NDANI-EGRESS" in l]
    return None
