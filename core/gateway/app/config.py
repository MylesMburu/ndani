"""Gateway settings, read from environment variables written by `ndani up`."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    inference_url: str = "http://inference:8000"
    embeddings_url: str = "http://embeddings:8001"
    database_url: str = ""
    model_id: str = "local-model"
    # sha256 hex digests of valid API keys. Raw keys never live on the server.
    api_key_hashes: list[str] = field(default_factory=list)
    audit_path: str = "/var/log/ndani/audit.jsonl"
    # Off by default: the audit log records *that* something was asked, not *what*.
    audit_log_content: bool = False
    # Tool/function calling is the main way a model can be tricked into reaching out.
    # Rejected unless explicitly enabled.
    allow_tools: bool = False
    rate_limit_per_minute: int = 60
    rag_enabled: bool = True
    rag_top_k: int = 4
    embedding_dim: int = 768
    request_timeout_s: float = 300.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            inference_url=os.environ.get("NDANI_INFERENCE_URL", cls.inference_url),
            embeddings_url=os.environ.get("NDANI_EMBEDDINGS_URL", cls.embeddings_url),
            database_url=os.environ.get("NDANI_DATABASE_URL", ""),
            model_id=os.environ.get("NDANI_MODEL_ID", cls.model_id),
            api_key_hashes=_list("NDANI_API_KEY_HASHES"),
            audit_path=os.environ.get("NDANI_AUDIT_PATH", cls.audit_path),
            audit_log_content=_bool("NDANI_AUDIT_LOG_CONTENT", False),
            allow_tools=_bool("NDANI_ALLOW_TOOLS", False),
            rate_limit_per_minute=int(os.environ.get("NDANI_RATE_LIMIT_PER_MINUTE", "60")),
            rag_enabled=_bool("NDANI_RAG_ENABLED", True),
            rag_top_k=int(os.environ.get("NDANI_RAG_TOP_K", "4")),
            embedding_dim=int(os.environ.get("NDANI_EMBEDDING_DIM", "768")),
            request_timeout_s=float(os.environ.get("NDANI_REQUEST_TIMEOUT_S", "300")),
        )
