"""ndani.toml loading, profile merging and the files `ndani up` writes for Docker."""
from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "core" / "profiles"
COMPOSE_FILE = REPO_ROOT / "core" / "compose.yaml"
STATE_DIR = Path(os.environ.get("NDANI_STATE_DIR", REPO_ROOT / ".ndani"))
CONFIG_PATH = Path(os.environ.get("NDANI_CONFIG", REPO_ROOT / "ndani.toml"))
GATEWAY_IMAGE = "ndani/gateway:0.1.0"


class ConfigError(Exception):
    pass


def list_profiles() -> dict[str, dict]:
    return {p.stem: tomllib.loads(p.read_text()) for p in sorted(PROFILES_DIR.glob("*.toml"))}


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.toml"
    if not path.exists():
        raise ConfigError(f"Unknown profile '{name}'. Available: {', '.join(list_profiles())}")
    return tomllib.loads(path.read_text())


@dataclass
class Config:
    profile: str
    bundle_dir: Path
    bind_addr: str = "127.0.0.1"
    port: int = 8443
    audit_dir: Path = field(default_factory=lambda: STATE_DIR / "audit")
    model: dict = field(default_factory=dict)        # profile values + [model] overrides
    embeddings: dict = field(default_factory=dict)
    gateway: dict = field(default_factory=dict)
    guard: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)

    @property
    def engine(self) -> str:
        return self.model["engine"]

    @property
    def compose_profile(self) -> str:
        return self.model["compose_profile"]

    @property
    def inference_container(self) -> str:
        return f"ndani-inference-{self.compose_profile}"

    def image_refs(self) -> list[str]:
        refs = [GATEWAY_IMAGE,
                f"ghcr.io/ggml-org/llama.cpp:{self.images.get('llamacpp', 'server')}",
                f"pgvector/pgvector:{self.images.get('pgvector', 'pg16')}"]
        if self.engine == "vllm":
            refs.append(f"vllm/vllm-openai:{self.images.get('vllm', 'latest')}")
        return refs


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"No config at {path}. Run `ndani init` first.")
    raw = tomllib.loads(path.read_text())
    server = raw.get("server", {})
    if "profile" not in server or "bundle_dir" not in server:
        raise ConfigError("[server] needs both 'profile' and 'bundle_dir'")
    model = {**load_profile(server["profile"]), **raw.get("model", {})}
    embeddings = {"file": "nomic-embed-text-v1.5.Q8_0.gguf", "dim": 768, "pooling": "mean",
                  **raw.get("embeddings", {})}
    gateway = {"allow_tools": False, "audit_log_content": False, "rate_limit_per_minute": 60,
               "rag_enabled": True, "rag_top_k": 4, **raw.get("gateway", {})}
    guard = {"enabled": True, "allow_cidrs": [], "allow_ports": [443], "allow_dns_servers": [],
             "allow_aws_metadata": False, "allow_aws_time_sync": False, **raw.get("guard", {})}
    if server.get("bind_addr") == "0.0.0.0":
        print("warning: gateway bound to 0.0.0.0, so it is reachable on every interface")
    return Config(
        profile=server["profile"],
        bundle_dir=Path(server["bundle_dir"]).expanduser(),
        bind_addr=server.get("bind_addr", "127.0.0.1"),
        port=int(server.get("port", 8443)),
        audit_dir=Path(server.get("audit_dir", STATE_DIR / "audit")).expanduser(),
        model=model, embeddings=embeddings, gateway=gateway, guard=guard,
        images=raw.get("images", {}),
    )


def render_config(profile: str, bundle_dir: str, guard_cidrs: list[str] | None = None) -> str:
    cidrs = ", ".join(f'"{c}"' for c in (guard_cidrs or []))
    return f'''# Ndani server config. Profile values can be overridden under [model].
[server]
profile = "{profile}"
bundle_dir = "{bundle_dir}"
bind_addr = "127.0.0.1"     # private IP or VPN interface in production, never 0.0.0.0 by accident
port = 8443

[model]
# model_id = "..."          # override the profile's model
# model_file = "..."        # GGUF file name inside <bundle>/llm (cpu engine)

[embeddings]
file = "nomic-embed-text-v1.5.Q8_0.gguf"   # GGUF inside <bundle>/embed
dim = 768
pooling = "mean"

[gateway]
allow_tools = false         # tool calling is the main exfiltration path; keep off unless sandboxed
audit_log_content = false   # log hashes of prompts, not prompts
rate_limit_per_minute = 60
rag_enabled = true

[guard]
enabled = true
allow_cidrs = [{cidrs}]     # host-level outbound allowlist (e.g. VPC CIDR for SSM/KMS endpoints)
allow_ports = [443]
allow_dns_servers = []      # e.g. ["169.254.169.253"] on AWS if SSM endpoints need DNS
allow_aws_metadata = false  # host only (root) - needed by the SSM agent on EC2
allow_aws_time_sync = false # 169.254.169.123 NTP

[images]                    # pin these to tags or digests you have tested
vllm = "latest"
llamacpp = "server"
pgvector = "pg16"
'''


def secrets_path() -> Path:
    return STATE_DIR / "secrets.env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def write_env_file(path: Path, values: dict[str, str], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}={v}\n" for k, v in values.items())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    os.chmod(path, mode)


def new_db_password() -> str:
    return secrets.token_urlsafe(24)


def compose_env(cfg: Config, db_password: str) -> dict[str, str]:
    """Variables Docker Compose interpolates into compose.yaml."""
    m = cfg.model
    env = {
        "NDANI_BIND_ADDR": cfg.bind_addr,
        "NDANI_PORT": str(cfg.port),
        "NDANI_BUNDLE_DIR": str(cfg.bundle_dir),
        "NDANI_AUDIT_DIR": str(cfg.audit_dir),
        "NDANI_MODEL_ID": m["model_id"],
        "NDANI_CONTEXT_LENGTH": str(m.get("context_length", 4096)),
        "NDANI_EMBED_FILE": cfg.embeddings["file"],
        "NDANI_EMBED_POOLING": cfg.embeddings.get("pooling", "mean"),
        "NDANI_DB_PASSWORD": db_password,
        "NDANI_VLLM_TAG": cfg.images.get("vllm", "latest"),
        "NDANI_LLAMACPP_TAG": cfg.images.get("llamacpp", "server"),
        "NDANI_PGVECTOR_TAG": cfg.images.get("pgvector", "pg16"),
        "NDANI_THREADS": str(m.get("threads", max((os.cpu_count() or 4) - 2, 1))),
    }
    if cfg.engine == "vllm":
        env.update({
            "NDANI_GPU_MEMORY_UTILIZATION": str(m.get("gpu_memory_utilization", 0.9)),
            "NDANI_TENSOR_PARALLEL": str(m.get("tensor_parallel", 1)),
            "NDANI_VLLM_EXTRA_ARGS": m.get("vllm_extra_args", ""),
        })
    else:
        if "model_file" not in m:
            raise ConfigError("cpu engine needs model_file (GGUF name inside <bundle>/llm)")
        env["NDANI_MODEL_FILE"] = m["model_file"]
    return env


def gateway_env(cfg: Config, db_password: str, key_hashes: str) -> dict[str, str]:
    """Environment for the gateway container only. Other containers never see these."""
    g = cfg.gateway
    return {
        "NDANI_INFERENCE_URL": "http://inference:8000",
        "NDANI_EMBEDDINGS_URL": "http://embeddings:8001",
        "NDANI_DATABASE_URL": f"postgresql://ndani:{db_password}@pgvector:5432/ndani",
        "NDANI_MODEL_ID": cfg.model["model_id"],
        "NDANI_API_KEY_HASHES": key_hashes,
        "NDANI_AUDIT_LOG_CONTENT": str(g["audit_log_content"]).lower(),
        "NDANI_ALLOW_TOOLS": str(g["allow_tools"]).lower(),
        "NDANI_RATE_LIMIT_PER_MINUTE": str(g["rate_limit_per_minute"]),
        "NDANI_RAG_ENABLED": str(g["rag_enabled"]).lower(),
        "NDANI_RAG_TOP_K": str(g.get("rag_top_k", 4)),
        "NDANI_EMBEDDING_DIM": str(cfg.embeddings["dim"]),
    }
