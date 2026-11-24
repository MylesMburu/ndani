import json
import subprocess

import pytest

from ndani import config, hardware, verify


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "ndani.toml"
    path.write_text(config.render_config("gpu-small", str(tmp_path / "bundle"), ["10.40.0.0/16"]))
    return config.load_config(path)


def test_profile_merge_and_overrides(tmp_path):
    path = tmp_path / "ndani.toml"
    text = config.render_config("cpu-small", "/b").replace(
        '# model_id = "..."', 'model_id = "custom"')
    path.write_text(text)
    cfg = config.load_config(path)
    assert cfg.engine == "llamacpp" and cfg.compose_profile == "cpu"
    assert cfg.model["model_id"] == "custom"
    assert cfg.inference_container == "ndani-inference-cpu"


def test_guard_cidrs_from_init(cfg):
    assert cfg.guard["allow_cidrs"] == ["10.40.0.0/16"]
    assert cfg.gateway["allow_tools"] is False


def test_secrets_only_reach_gateway_env(cfg):
    compose = config.compose_env(cfg, "dbpw")
    gw = config.gateway_env(cfg, "dbpw", "abc123")
    assert "NDANI_API_KEY_HASHES" not in compose
    assert gw["NDANI_API_KEY_HASHES"] == "abc123"
    assert gw["NDANI_DATABASE_URL"].endswith("@pgvector:5432/ndani")


def test_env_file_permissions(tmp_path):
    p = tmp_path / "s.env"
    config.write_env_file(p, {"A": "1"})
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert config.read_env_file(p) == {"A": "1"}


@pytest.mark.parametrize("vram,ram,expected", [
    (48, 64, "gpu-large"), (24, 32, "gpu-small"), (0, 32, "cpu-medium"), (0, 8, "cpu-small"),
])
def test_profile_recommendation(vram, ram, expected):
    gpus = [("GPU", vram)] if vram else []
    assert hardware.recommend_profile(hardware.Hardware(8, ram, gpus)) == expected


def test_nvidia_smi_parsing():
    out = "NVIDIA L4, 23034\nNVIDIA A10G, 23028\n"
    gpus = hardware.parse_nvidia_smi(out)
    assert [g[0] for g in gpus] == ["NVIDIA L4", "NVIDIA A10G"]
    assert round(gpus[0][1]) == 22


def test_container_probe_parsing_and_failure(cfg):
    good = subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "results": [
        {"id": "dns_system", "description": "d", "passed": True, "detail": "resolution failed"}]}))
    checks = verify.probe_container("ndani-gateway", [], lambda cmd: good)
    assert checks[0].passed is True and checks[0].group == "ndani-gateway"

    broken = subprocess.CompletedProcess([], 125, stdout="", stderr="No such container: ndani-gateway")
    checks = verify.probe_container("ndani-gateway", [], lambda cmd: broken)
    assert checks[0].passed is False and "No such container" in checks[0].detail


def test_probe_runs_in_target_namespace(cfg):
    seen = {}

    def runner(cmd):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true, "results": []}')

    verify.probe_container("ndani-pgvector", ["embeddings:8001"], runner)
    assert "container:ndani-pgvector" in seen["cmd"]
    assert seen["cmd"][-2:] == ["--expect-reachable", "embeddings:8001"]


def test_gateway_checks_without_key_skip_not_fail(cfg):
    responses = {"/healthz": (200, {}), "/v1/chat/completions": (401, {})}
    fake = lambda url, body=None, key=None: responses[url.split(":8443")[1]]
    checks = verify.gateway_checks(cfg, None, http=fake)
    assert [c.passed for c in checks[:2]] == [True, True]
    assert all(c.passed is None for c in checks[2:])


def test_markdown_report(cfg):
    r = verify.Report("now", "host1", "gpu-small", [
        verify.Check("ndani-gateway", "dns", "Resolve", True, "failed"),
        verify.Check("guard", "evidence", "Logged", False, "0 lines")])
    md = verify.render_markdown(r)
    assert md.startswith("# Ndani egress verification: FAILED")
    assert "| PASS | Resolve | failed |" in md and "## guard" in md
