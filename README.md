# Ndani

**Run a private LLM on any server, and prove nothing leaves it.**

*Ndani* is Swahili for "inside". It runs an open-weights model, local RAG and an
OpenAI-compatible API on one host. The host is sealed so that no prompt, document or
output can leave it, and a test suite tries every escape route and writes the evidence
to a report.

```
EC2 · GCP · Azure · Hetzner · a rack in your office · a laptop
```

## Why

"Self-hosted" usually means "the model runs on my box but still phones home to
Hugging Face at boot, the container can reach the internet, and DNS goes anywhere."
Ndani treats *private* as a claim you have to back with evidence:

| Escape route | How Ndani closes it |
|---|---|
| Model/runtime downloading or phoning home | Offline bundle with SHA-256 manifest (+ optional signature); `HF_HUB_OFFLINE=1`; images loaded from the bundle |
| Container opens a connection out | Model, embeddings and DB live on a Docker `internal: true` network with no route out |
| Gateway (the one internet-facing container) dials out | Host guard drops every NEW connection starting on the gateway's bridge; replies to users still flow |
| The host itself | nftables OUTPUT default-deny with an explicit allowlist |
| DNS tunnelling (`<secret>.attacker.com`) | Host DNS blocked by default; on AWS, Route 53 DNS Firewall allows only `*.amazonaws.com` |
| Cloud metadata credential theft | IMDS blocked for containers (guard + IMDSv2 hop limit 1); root-only on the host when SSM needs it |
| Tool calling / prompt injection ("fetch this URL") | The gateway refuses `tools` unless you explicitly enable them |
| Logs leaking content | The audit log stores SHA-256 hashes of prompts, not prompts |

## Architecture

```
            clients (VPN / SSM port-forward / private IP)
                              │
┌──────────── any Linux host ─┼──────────────────────────────────────┐
│                       ┌─────▼─────┐   edge network                 │
│   egress guard        │  gateway  │   (no NEW outbound allowed)    │
│   nftables OUTPUT     │ auth·audit│                                │
│   + DOCKER-USER chain └─────┬─────┘                                │
│                ┌────────────┼───────────────┐  sealed network      │
│                │      ┌─────▼─────┐         │  (internal: true)    │
│                │      │ inference │ vLLM (GPU) / llama.cpp (CPU)   │
│                │      └───────────┘         │                      │
│                │  embeddings     pgvector   │                      │
│                └────────────────────────────┘                      │
└────────────────────────────────────────────────────────────────────┘
        ▲ optional provider adapter (defense in depth)
        AWS: no-IGW VPC · VPC endpoints · DNS Firewall · flow logs · KMS
```

## Quick start (bare metal or any VM)

```bash
git clone <this repo> && cd ndani
./ndani detect                       # what hardware do I have, which profile fits
./ndani init                         # writes ndani.toml, prints an API key ONCE

# On a connected machine, build the bundle (weights + hashes, optionally images):
huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q4_k_m.gguf --local-dir ./src/llm
huggingface-cli download nomic-ai/nomic-embed-text-v1.5-GGUF nomic-embed-text-v1.5.Q8_0.gguf --local-dir ./src/embed
./ndani bundle build --llm ./src/llm/qwen2.5-3b-instruct-q4_k_m.gguf \
    --embed ./src/embed/nomic-embed-text-v1.5.Q8_0.gguf --out /opt/ndani/bundle --name qwen3b

./ndani prepare                      # build/pull images while the network is still open
sudo ./ndani up                      # verify bundle → start stack → seal host
sudo ./ndani verify --api-key ndk_…  # try to escape, write .ndani/reports/verify-*.md
```

Then use any OpenAI SDK:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8443/v1", api_key="ndk_...")
client.chat.completions.create(model="local", messages=[{"role": "user", "content": "Habari?"}])
```

RAG over your own documents (never leaves the box):

```bash
curl -s localhost:8443/v1/rag/documents -H "Authorization: Bearer $KEY" \
  -d '{"source": "hr-policy.md", "text": "..."}'
# then add "ndani_rag": true to any chat request; the answer cites [n] sources
```

## On AWS

```bash
cd adapters/aws
terraform init && terraform apply -var profile=gpu-small -var instance_type=g6.xlarge
aws s3 sync /path/to/bundle s3://$(terraform output -raw bundle_bucket)/bundle/
aws s3 sync ../../ s3://$(terraform output -raw bundle_bucket)/repo/ --exclude ".git/*" --exclude ".ndani/*"
aws s3 cp /dev/null s3://$(terraform output -raw bundle_bucket)/READY   # bootstrap waits for this
# watch progress: aws ssm start-session --target <id>, then tail /var/log/ndani-bootstrap.log
$(terraform output -raw port_forward_command)     # gateway on localhost:8443 via SSM
terraform output -raw api_key
```

The instance has **no public IP, no internet gateway and no NAT**. The bundle comes in
through an S3 gateway endpoint whose policy only allows reading *this* bucket, and secrets
come from SSM SecureString. Every flow is recorded in VPC Flow Logs (`terraform output
flow_log_query` gives you the Logs Insights query that proves nothing went out).

> The AMI must already contain Docker + compose, the AWS CLI, Python ≥ 3.11 and nftables
> (and NVIDIA drivers + container toolkit for GPU), because the host can't install
> anything. The default is the AWS Deep Learning Base GPU AMI; for CPU profiles bring a
> golden AMI built with Packer.

## Profiles

| Profile | Engine | Model (default) | Hardware |
|---|---|---|---|
| `cpu-small` | llama.cpp | Qwen2.5-3B Q4_K_M | 8 GB RAM, any CPU |
| `cpu-medium` | llama.cpp | Qwen2.5-7B Q4_K_M | 16 GB RAM, 8+ cores |
| `gpu-small` | vLLM | Qwen2.5-7B fp16 | 1× 24 GB GPU (g5/g6.xlarge) |
| `gpu-large` | vLLM | Qwen2.5-32B AWQ | 1× 48 GB GPU (g6e.xlarge) |

Override anything under `[model]` in `ndani.toml`. Profiles are just TOML in `core/profiles/`.

## What `ndani verify` checks

For **every container** (run inside its own network namespace) and **the host**:
DNS resolution, a raw DNS query to 8.8.8.8, TCP to public IPs on 443/80, HTTPS to
huggingface.co, the cloud metadata service, and IPv6. All of these must fail.
**Control probes** must succeed (gateway → model, model → DB, host → gateway), which
shows the failures come from the guard rather than a broken network.

It also checks that the gateway rejects missing keys and tool calls, that a real
completion works end to end, and that the guard's **drop counters increased** during
the run.

See `docs/example-reports/` for a real before/after pair from the e2e test: the gateway
container could reach the internet, tunnel DNS and reach the metadata service before
the guard, and **47/47 checks passed** after it.

## Repo layout

```
cli/ndani/        stdlib-only CLI (Python 3.11+, no pip install on the server)
core/compose.yaml the stack: sealed + edge networks
core/gateway/     OpenAI-compatible gateway: API keys, rate limit, audit log, RAG
core/profiles/    hardware profiles
core/guard/       systemd unit that re-applies the guard on boot
verify/probe.py   the escape probe (stdlib only, runs inside any container namespace)
adapters/aws/     Terraform: zero-egress VPC, endpoints, DNS Firewall, flow logs, KMS, EC2
adapters/baremetal/ Ansible playbook for any VPS / on-prem host
adapters/gcp/     the adapter contract, for whoever builds it next
tests/            unit tests (pytest) + tests/e2e isolation test
```

## Honest limits

- **Your infrastructure provider is still in the trust boundary.** On EC2 you rely on
  AWS Nitro isolation and your KMS keys. For "nobody but us, ever", use the bare-metal
  adapter on hardware you control.
- **Getting things in is the weak point.** The bundle and images are verified by hash,
  so a compromised build machine is the threat. Sign bundles (`--sign-key`) and build
  them somewhere you trust.
- **`allow_tools = true` changes the threat model.** If you give the model tools, those
  tools run inside the sealed network and are only as safe as what you let them reach.
- The `exfil_prompt` check confirms there is no tool path; the network probes are what
  prove the absence of an exit.

## Tests

```bash
python3 -m pytest tests          # 36 unit tests, no Docker needed
sudo tests/e2e/run.sh            # real Docker networks + real guard, stand-in services
```
