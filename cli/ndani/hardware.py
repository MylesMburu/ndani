"""Pick a profile from what the machine actually has."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Hardware:
    cpus: int
    ram_gb: float
    gpus: list[tuple[str, float]]  # (name, vram_gb)

    @property
    def total_vram_gb(self) -> float:
        return sum(v for _, v in self.gpus)

    @property
    def max_single_vram_gb(self) -> float:
        return max((v for _, v in self.gpus), default=0.0)


def detect() -> Hardware:
    return Hardware(cpus=os.cpu_count() or 1, ram_gb=_ram_gb(), gpus=_nvidia_gpus())


def _ram_gb() -> float:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0


def _nvidia_gpus() -> list[tuple[str, float]]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return parse_nvidia_smi(out)


def parse_nvidia_smi(output: str) -> list[tuple[str, float]]:
    gpus = []
    for line in output.strip().splitlines():
        if "," not in line:
            continue
        name, mib = line.rsplit(",", 1)
        try:
            gpus.append((name.strip(), int(mib.strip()) / 1024))
        except ValueError:
            continue
    return gpus


def recommend_profile(hw: Hardware) -> str:
    vram = hw.max_single_vram_gb
    if vram >= 44:
        return "gpu-large"
    if vram >= 22:
        return "gpu-small"
    if hw.ram_gb >= 15:
        return "cpu-medium"
    return "cpu-small"
