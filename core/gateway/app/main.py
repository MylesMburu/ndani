"""Ndani gateway: the only container reachable from outside the sealed network.

OpenAI-compatible, so any existing SDK works by changing its base URL:

    client = OpenAI(base_url="https://<host>:8443/v1", api_key="ndk_...")
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings
from .rag import RagStore, build_context_message
from .security import AuditLog, RateLimiter, check_key


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    audit = AuditLog(settings.audit_path, settings.audit_log_content)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # trust_env=False: ignore any HTTP(S)_PROXY in the environment. The gateway
        # only ever talks to services on the sealed network.
        client = httpx.AsyncClient(timeout=settings.request_timeout_s, transport=transport, trust_env=False)
        state["client"] = client
        state["rag"] = (
            RagStore(settings.database_url, settings.embeddings_url, settings.embedding_dim, client)
            if settings.rag_enabled and settings.database_url
            else None
        )
        yield
        await client.aclose()

    app = FastAPI(title="ndani gateway", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    def error(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {"message": message, "type": kind}})

    def authenticate(request: Request) -> str | None:
        header = request.headers.get("authorization", "")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else None
        return check_key(raw, settings.api_key_hashes)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        started = time.perf_counter()
        key_id = authenticate(request)
        if key_id is None:
            audit.record(key_id=None, endpoint=request.url.path, status=401,
                         latency_ms=(time.perf_counter() - started) * 1000)
            return error(401, "Missing or invalid API key", "authentication_error")
        if not limiter.allow(key_id):
            audit.record(key_id=key_id, endpoint=request.url.path, status=429,
                         latency_ms=(time.perf_counter() - started) * 1000)
            return error(429, "Rate limit exceeded", "rate_limit_error")
        request.state.key_id = key_id
        request.state.started = started
        return await call_next(request)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models():
        now = int(time.time())
        return {"object": "list", "data": [{"id": settings.model_id, "object": "model", "created": now, "owned_by": "ndani"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        key_id = request.state.key_id

        if not settings.allow_tools and (body.get("tools") or body.get("functions")):
            audit.record(key_id=key_id, endpoint="/v1/chat/completions", status=400,
                         latency_ms=_ms(request), payload=body, extra={"blocked": "tools"})
            return error(400, "Tool calling is disabled on this server (NDANI_ALLOW_TOOLS=false)")

        use_rag = bool(body.pop("ndani_rag", False))
        sources: list[str] = []
        if use_rag:
            rag: RagStore | None = state.get("rag")
            if rag is None:
                return error(400, "RAG is not enabled on this server")
            question = _last_user_text(body.get("messages", []))
            chunks = await rag.search(question, settings.rag_top_k) if question else []
            if chunks:
                body["messages"] = [build_context_message(chunks), *body.get("messages", [])]
                sources = sorted({c.source for c in chunks})

        body["model"] = settings.model_id  # one model per server; ignore client's choice
        client: httpx.AsyncClient = state["client"]
        url = f"{settings.inference_url.rstrip('/')}/v1/chat/completions"

        if body.get("stream"):
            upstream_req = client.build_request("POST", url, json=body)
            upstream = await client.send(upstream_req, stream=True)

            async def relay():
                try:
                    async for piece in upstream.aiter_raw():
                        yield piece
                finally:
                    await upstream.aclose()
                    audit.record(key_id=key_id, endpoint="/v1/chat/completions", status=upstream.status_code,
                                 latency_ms=_ms(request), payload=body,
                                 extra={"stream": True, "rag_sources": sources})

            return StreamingResponse(relay(), status_code=upstream.status_code,
                                     media_type=upstream.headers.get("content-type", "text/event-stream"))

        upstream = await client.post(url, json=body)
        data = upstream.json()
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        audit.record(key_id=key_id, endpoint="/v1/chat/completions", status=upstream.status_code,
                     latency_ms=_ms(request), payload=body,
                     extra={"usage": usage, "rag_sources": sources})
        if sources and isinstance(data, dict):
            data["ndani_sources"] = sources
        return JSONResponse(status_code=upstream.status_code, content=data)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        body = await request.json()
        client: httpx.AsyncClient = state["client"]
        upstream = await client.post(f"{settings.embeddings_url.rstrip('/')}/v1/embeddings", json=body)
        audit.record(key_id=request.state.key_id, endpoint="/v1/embeddings",
                     status=upstream.status_code, latency_ms=_ms(request), payload=body)
        return JSONResponse(status_code=upstream.status_code, content=upstream.json())

    @app.post("/v1/rag/documents")
    async def ingest(request: Request):
        rag: RagStore | None = state.get("rag")
        if rag is None:
            return error(400, "RAG is not enabled on this server")
        body = await request.json()
        source, text = body.get("source"), body.get("text")
        if not source or not text:
            return error(400, "Both 'source' and 'text' are required")
        count = await rag.ingest(source, text)
        audit.record(key_id=request.state.key_id, endpoint="/v1/rag/documents", status=200,
                     latency_ms=_ms(request), payload={"source": source},
                     extra={"chunks": count, "chars": len(text)})
        return {"source": source, "chunks": count}

    @app.post("/v1/rag/search")
    async def search(request: Request):
        rag: RagStore | None = state.get("rag")
        if rag is None:
            return error(400, "RAG is not enabled on this server")
        body = await request.json()
        results = await rag.search(body.get("query", ""), int(body.get("top_k", settings.rag_top_k)))
        audit.record(key_id=request.state.key_id, endpoint="/v1/rag/search", status=200,
                     latency_ms=_ms(request), payload=body)
        return {"results": [c.__dict__ for c in results]}

    return app


def _ms(request: Request) -> float:
    return (time.perf_counter() - request.state.started) * 1000


def _last_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""

