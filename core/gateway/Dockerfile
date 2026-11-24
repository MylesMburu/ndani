# Build context is the repo root (see core/compose.yaml) so the probe can be baked in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY core/gateway/requirements.txt .
RUN pip install -r requirements.txt

COPY core/gateway/app ./app
# The egress probe runs inside other containers' network namespaces during `ndani verify`.
COPY verify/probe.py ./probe.py

RUN useradd --system --uid 10001 ndani && mkdir -p /var/log/ndani && chown ndani /var/log/ndani
USER ndani

EXPOSE 8080
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
