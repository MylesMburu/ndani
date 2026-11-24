import shutil
import subprocess

import pytest

from ndani import bundle


@pytest.fixture
def sources(tmp_path):
    llm = tmp_path / "src-llm"
    (llm / "sub").mkdir(parents=True)
    (llm / "config.json").write_text('{"arch": "test"}')
    (llm / "sub" / "model.safetensors").write_bytes(b"\x00" * 4096)
    (llm / ".cache").mkdir()
    (llm / ".cache" / "junk").write_text("hf metadata")
    embed = tmp_path / "embed.gguf"
    embed.write_bytes(b"GGUF" + b"\x01" * 1000)
    return llm, embed


def test_build_then_verify_ok(tmp_path, sources):
    llm, embed = sources
    out = tmp_path / "bundle"
    manifest = bundle.build(out, llm, embed, "test")
    paths = {f["path"] for f in manifest["files"]}
    assert paths == {"llm/config.json", "llm/sub/model.safetensors", "embed/embed.gguf"}
    assert ".cache" not in str(paths)
    result = bundle.verify(out)
    assert result.ok, result.problems
    assert result.checked == 3


def test_tampered_weight_is_caught(tmp_path, sources):
    out = tmp_path / "bundle"
    bundle.build(out, *sources, "test")
    target = out / "llm" / "sub" / "model.safetensors"
    target.unlink()  # break the hardlink before editing, like an attacker would
    target.write_bytes(b"\x01" + b"\x00" * 4095)
    result = bundle.verify(out)
    assert not result.ok
    assert any("hash mismatch" in p for p in result.problems)


def test_extra_and_missing_files_are_caught(tmp_path, sources):
    out = tmp_path / "bundle"
    bundle.build(out, *sources, "test")
    (out / "llm" / "backdoor.py").write_text("import os")
    (out / "embed" / "embed.gguf").unlink()
    problems = bundle.verify(out).problems
    assert any("unexpected file" in p and "backdoor.py" in p for p in problems)
    assert any("missing file" in p for p in problems)


def test_refuses_non_empty_output(tmp_path, sources):
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "x").write_text("x")
    with pytest.raises(FileExistsError):
        bundle.build(out, *sources, "test")


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="ssh-keygen missing")
def test_signature_roundtrip(tmp_path, sources):
    key = tmp_path / "signer"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    signers = tmp_path / "allowed_signers"
    signers.write_text(f"ndani {(tmp_path / 'signer.pub').read_text()}")
    out = tmp_path / "bundle"
    bundle.build(out, *sources, "test", sign_key=key)
    good = bundle.verify(out, allowed_signers=signers, require_signature=True)
    assert good.ok and good.signature == "valid", good.problems

    manifest = out / "manifest.json"
    manifest.write_text(manifest.read_text().replace('"test"', '"evil"'))
    bad = bundle.verify(out, allowed_signers=signers, require_signature=True)
    assert not bad.ok and bad.signature == "invalid"


def test_require_signature_without_one_fails(tmp_path, sources):
    out = tmp_path / "bundle"
    bundle.build(out, *sources, "test")
    assert not bundle.verify(out, require_signature=True).ok
