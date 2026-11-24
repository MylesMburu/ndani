# Ndani egress verification: FAILED

- Host: `vm`  
- Profile: `cpu-small`  
- Run started: 2026-11-24 17:26:24  
- Result: 36 passed, 11 failed, 4 skipped

Escape probes must fail; control probes must succeed (they prove the network works, so failures are caused by the guard, not a broken setup).

## ndani-gateway

| Result | Check | Detail |
|---|---|---|
| PASS | Resolve a public hostname via the system resolver | resolution failed (Name or service not known) |
| FAIL | Raw DNS query to a public resolver (DNS tunnelling path) | got a DNS answer |
| FAIL | TCP to a public IP on 443, bypassing DNS | connected |
| FAIL | TCP to a public IP on 80 | connected |
| PASS | HTTPS to huggingface.co (model phone-home path) | resolution failed (Name or service not known) |
| FAIL | Cloud instance metadata service (credential theft path) | connected |
| PASS | TCP to a public IPv6 address | Address family not supported by protocol |
| PASS | Must reach inference:8000 (proves the network works) | connected |
| PASS | Must reach embeddings:8001 (proves the network works) | connected |
| PASS | Must reach pgvector:5432 (proves the network works) | connected |

## ndani-inference-cpu

| Result | Check | Detail |
|---|---|---|
| PASS | Resolve a public hostname via the system resolver | resolution failed (Temporary failure in name resolution) |
| PASS | Raw DNS query to a public resolver (DNS tunnelling path) | Network is unreachable |
| PASS | TCP to a public IP on 443, bypassing DNS | Network is unreachable |
| PASS | TCP to a public IP on 80 | Network is unreachable |
| PASS | HTTPS to huggingface.co (model phone-home path) | resolution failed (Temporary failure in name resolution) |
| PASS | Cloud instance metadata service (credential theft path) | Network is unreachable |
| PASS | TCP to a public IPv6 address | Address family not supported by protocol |
| PASS | Must reach pgvector:5432 (proves the network works) | connected |

## ndani-embeddings

| Result | Check | Detail |
|---|---|---|
| PASS | Resolve a public hostname via the system resolver | resolution failed (Temporary failure in name resolution) |
| PASS | Raw DNS query to a public resolver (DNS tunnelling path) | Network is unreachable |
| PASS | TCP to a public IP on 443, bypassing DNS | Network is unreachable |
| PASS | TCP to a public IP on 80 | Network is unreachable |
| PASS | HTTPS to huggingface.co (model phone-home path) | resolution failed (Temporary failure in name resolution) |
| PASS | Cloud instance metadata service (credential theft path) | Network is unreachable |
| PASS | TCP to a public IPv6 address | Address family not supported by protocol |
| PASS | Must reach pgvector:5432 (proves the network works) | connected |

## ndani-pgvector

| Result | Check | Detail |
|---|---|---|
| PASS | Resolve a public hostname via the system resolver | resolution failed (Temporary failure in name resolution) |
| PASS | Raw DNS query to a public resolver (DNS tunnelling path) | Network is unreachable |
| PASS | TCP to a public IP on 443, bypassing DNS | Network is unreachable |
| PASS | TCP to a public IP on 80 | Network is unreachable |
| PASS | HTTPS to huggingface.co (model phone-home path) | resolution failed (Temporary failure in name resolution) |
| PASS | Cloud instance metadata service (credential theft path) | Network is unreachable |
| PASS | TCP to a public IPv6 address | Address family not supported by protocol |
| PASS | Must reach embeddings:8001 (proves the network works) | connected |

## host

| Result | Check | Detail |
|---|---|---|
| PASS | Resolve a public hostname via the system resolver | resolution failed (Name or service not known) |
| FAIL | Raw DNS query to a public resolver (DNS tunnelling path) | got a DNS answer |
| FAIL | TCP to a public IP on 443, bypassing DNS | connected |
| FAIL | TCP to a public IP on 80 | connected |
| PASS | HTTPS to huggingface.co (model phone-home path) | resolution failed (Name or service not known) |
| FAIL | Cloud instance metadata service (credential theft path) | connected |
| PASS | TCP to a public IPv6 address | Address family not supported by protocol |
| PASS | Must reach 127.0.0.1:8443 (proves the network works) | connected |

## gateway

| Result | Check | Detail |
|---|---|---|
| PASS | Gateway is up | HTTP 200 |
| PASS | Requests without an API key are rejected | HTTP 401 |
| SKIP | Tool calling is refused | skipped: pass --api-key or set NDANI_API_KEY |
| SKIP | A real completion works end to end | skipped: pass --api-key or set NDANI_API_KEY |
| SKIP | Model cannot fetch a URL it is told to fetch | skipped: pass --api-key or set NDANI_API_KEY |

## guard

| Result | Check | Detail |
|---|---|---|
| FAIL | Host OUTPUT default-deny table loaded | table missing (run as root?) |
| FAIL | Container egress chain hooked into DOCKER-USER | not hooked |
| FAIL | Guard dropped packets during this run | host +0, containers +0 packets dropped |
| SKIP | Blocked attempts during this run were logged | 0 NDANI-EGRESS log lines since 2026-11-24 17:26:24 |
