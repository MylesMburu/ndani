import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "cli"), str(ROOT / "core" / "gateway"), str(ROOT / "verify")]
