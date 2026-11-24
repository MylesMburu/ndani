# Article series plan (AWS Community Builder blog)

Each post maps to code in this repo, so every claim can be checked.

## 1. "Private" is a claim. Here's how to prove it.
- The threat model: what leaves a server when you "self-host" an LLM (HF phone-home,
  telemetry, DNS, metadata service, tool calls, logs).
- When Bedrock (no training on your data, PrivateLink, in-region) is already enough,
  and the four reasons to self-host anyway.
- Show `docs/example-reports/before-guard.md`: a container that looks fine but can tunnel
  DNS and reach IMDS.

## 2. Building the portable core
- Docker `internal: true` and why it is the portable guarantee.
- The gateway as the only edge container; the DOCKER-USER chain that lets replies out
  but no new connections.
- Offline bundles: SHA-256 manifests, rejecting extra files, ssh-keygen signatures.
- Hardware detection: one config for GPU (vLLM) and CPU (llama.cpp).

## 3. The zero-egress VPC on AWS (Terraform)
- No IGW, no NAT: what still works (SSM, S3, KMS via endpoints) and what breaks.
- S3 endpoint policy + bucket policy `aws:SourceVpce`: why stolen creds can't copy data out.
- Route 53 Resolver DNS Firewall: the exfil path almost every guide misses.
- IMDSv2 with hop limit 1, and why containers must never see the instance role.

## 4. Proving it: the verify suite
- Running a probe inside another container's network namespace (`--network container:x`).
- Control probes, and why a test that only checks for failure proves nothing.
- Drop counters vs kernel logs as evidence; VPC Flow Logs query as the outside witness.
- The before/after reports (47/47).

## 5. Cost and performance
- g6.xlarge self-hosted vs Bedrock per 1,000 requests for a realistic internal workload.
- Break-even volume; when idle GPU hours kill the case.
- CPU profile numbers: what a 3B Q4 model on a cheap VPS actually delivers.

## Demo ideas
- Live: run `ndani verify`, then `ndani guard remove`, run again, watch it fail.
- Swahili/Sheng RAG over a local document set, fully offline.
