#!/usr/bin/env bash
# Isolation e2e test: same Docker networks as production, stand-in services, real guard.
# Needs root, Docker, nftables, and an image tagged ndani/gateway:0.1.0 (build it with
# `docker compose -f core/compose.yaml build gateway`, or any image with python3 + probe.py in /srv).
#
# Expected: the "before" run FAILS (the gateway container can reach the internet),
# the "after" run PASSES with non-zero drop counters.
set -euo pipefail
cd "$(dirname "$0")/../.."
export NDANI_CONFIG="$(mktemp -d)/ndani.toml"
./ndani init --profile cpu-small --bundle-dir /tmp/ndani-e2e --force >/dev/null

docker compose -f tests/e2e/compose.e2e.yaml up -d
trap 'sudo ./ndani guard remove >/dev/null; docker compose -f tests/e2e/compose.e2e.yaml down' EXIT
sleep 2

echo "== before guard (expect FAILED) =="
./ndani verify || true

echo "== after guard (expect PASSED) =="
./ndani guard apply
./ndani verify
