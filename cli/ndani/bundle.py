"""Offline model bundles: build on a connected machine, verify on the sealed one.

Layout:
    <bundle>/
      manifest.json        every file with sha256 + size
      manifest.json.sig    optional ssh-keygen signature over manifest.json
      llm/                 model weights (HF dir for vLLM, or a .gguf for llama.cpp)
      embed/               embedding model .gguf
      images/              optional `docker save` tarballs for fully air-gapped hosts
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
SIGN_NAMESPACE = "ndani-bundle"
CHUNK = 8 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _copy_into(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        for item in src.rglob("*"):
            # skip HF cache metadata and hidden files; they are not needed offline
            rel = item.relative_to(src)
            if any(part.startswith(".") for part in rel.parts) or not item.is_file():
                continue
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            _link_or_copy(item, target)
    else:
        _link_or_copy(src, dest_dir / src.name)


def _link_or_copy(src: Path, dest: Path) -> None:
    src = src.resolve()  # HF snapshots are symlinks into a blob store
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def build(out: Path, llm: Path, embed: Path, name: str, images: list[str] | None = None,
          sign_key: Path | None = None) -> dict:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    _copy_into(llm, out / "llm")
    _copy_into(embed, out / "embed")
    image_entries = []
    if images:
        (out / "images").mkdir()
        for ref in images:
            tar = out / "images" / (ref.replace("/", "_").replace(":", "__") + ".tar")
            subprocess.run(["docker", "save", "-o", str(tar), ref], check=True)
            image_entries.append({"ref": ref, "path": str(tar.relative_to(out))})
    manifest = {
        "format": 1,
        "name": name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": _hash_tree(out),
        "images": image_entries,
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    if sign_key:
        sign(out, sign_key)
    return manifest


def _hash_tree(root: Path) -> list[dict]:
    entries = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in (MANIFEST, SIGNATURE):
            continue
        entries.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return entries


def sign(bundle: Path, key: Path) -> Path:
    subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", SIGN_NAMESPACE,
                    str(bundle / MANIFEST)], check=True)
    return bundle / SIGNATURE


@dataclass
class VerifyResult:
    ok: bool
    checked: int = 0
    problems: list[str] = field(default_factory=list)
    signature: str = "absent"   # absent | valid | invalid | unchecked

    def summary(self) -> str:
        status = "OK" if self.ok else "FAILED"
        return f"bundle {status}: {self.checked} files checked, signature {self.signature}"


def verify(bundle: Path, allowed_signers: Path | None = None, signer: str = "ndani",
           require_signature: bool = False, progress=None) -> VerifyResult:
    result = VerifyResult(ok=True)
    manifest_path = bundle / MANIFEST
    if not manifest_path.exists():
        return VerifyResult(ok=False, problems=[f"missing {MANIFEST}"])
    manifest = json.loads(manifest_path.read_text())

    expected = {f["path"]: f for f in manifest.get("files", [])}
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()} - {MANIFEST, SIGNATURE}

    for extra in sorted(actual - expected.keys()):
        result.problems.append(f"unexpected file not in manifest: {extra}")
    for missing in sorted(expected.keys() - actual):
        result.problems.append(f"missing file: {missing}")
    for rel in sorted(expected.keys() & actual):
        entry = expected[rel]
        path = bundle / rel
        if progress:
            progress(rel)
        if path.stat().st_size != entry["bytes"]:
            result.problems.append(f"size mismatch: {rel}")
        elif sha256_file(path) != entry["sha256"]:
            result.problems.append(f"hash mismatch: {rel}")
        result.checked += 1

    sig_path = bundle / SIGNATURE
    if sig_path.exists() and allowed_signers:
        with open(manifest_path, "rb") as fh:
            proc = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", signer,
                 "-n", SIGN_NAMESPACE, "-s", str(sig_path)],
                stdin=fh, capture_output=True,
            )
        result.signature = "valid" if proc.returncode == 0 else "invalid"
        if proc.returncode != 0:
            result.problems.append("signature verification failed")
    elif sig_path.exists():
        result.signature = "unchecked"
    if require_signature and result.signature != "valid":
        result.problems.append("a valid signature is required (pass --allowed-signers)")

    result.ok = not result.problems
    return result
