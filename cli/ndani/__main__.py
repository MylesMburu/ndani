"""ndani: run a private LLM on any server, and prove nothing leaves it."""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import subprocess
import sys
from pathlib import Path

from . import bundle, guard, hardware
from . import verify as verifier
from .config import (COMPOSE_FILE, CONFIG_PATH, STATE_DIR, ConfigError, compose_env, gateway_env,
                     list_profiles, load_config, new_db_password, read_env_file, render_config,
                     secrets_path, write_env_file)

__version__ = "0.1.0"
COMPOSE_ENV = STATE_DIR / "compose.env"
GATEWAY_ENV = STATE_DIR / "gateway.env"


def _say(msg: str) -> None:
    print(f"ndani: {msg}")


def _compose(cfg, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(COMPOSE_ENV),
            "--profile", cfg.compose_profile, *args]


def _require_root(action: str) -> None:
    if os.geteuid() != 0:
        raise SystemExit(f"ndani: {action} needs root (firewall rules). Re-run with sudo.")


# ---------------------------------------------------------------- commands

def cmd_detect(_args) -> int:
    hw = hardware.detect()
    print(f"CPUs: {hw.cpus}\nRAM:  {hw.ram_gb:.1f} GB")
    for name, vram in hw.gpus or [("none", 0)]:
        print(f"GPU:  {name}" + (f" ({vram:.0f} GB)" if vram else ""))
    print(f"Recommended profile: {hardware.recommend_profile(hw)}")
    return 0


def cmd_profiles(_args) -> int:
    for name, p in list_profiles().items():
        print(f"{name:<12} {p['engine']:<9} {p['description']}  [{p['suggested_source']}]")
    return 0


def cmd_init(args) -> int:
    if CONFIG_PATH.exists() and not args.force:
        _say(f"{CONFIG_PATH} already exists (use --force to overwrite)")
        return 1
    profile = args.profile or hardware.recommend_profile(hardware.detect())
    if profile not in list_profiles():
        raise SystemExit(f"ndani: unknown profile {profile}")
    CONFIG_PATH.write_text(render_config(profile, args.bundle_dir, args.allow_cidr))
    existing = read_env_file(secrets_path())
    raw_key = None
    if not existing.get("NDANI_API_KEY_HASHES"):
        raw_key = "ndk_" + secrets.token_urlsafe(32)
        existing["NDANI_API_KEY_HASHES"] = hashlib.sha256(raw_key.encode()).hexdigest()
    existing.setdefault("NDANI_DB_PASSWORD", new_db_password())
    write_env_file(secrets_path(), existing)
    _say(f"wrote {CONFIG_PATH} with profile '{profile}'")
    if raw_key:
        print("\n  API key (shown once, only its hash is stored):\n")
        print(f"    {raw_key}\n")
    print(f"Next: put a model bundle at {args.bundle_dir}, then `ndani prepare` and `sudo ndani up`.")
    return 0


def cmd_keys_add(_args) -> int:
    values = read_env_file(secrets_path())
    raw_key = "ndk_" + secrets.token_urlsafe(32)
    hashes = [h for h in values.get("NDANI_API_KEY_HASHES", "").split(",") if h]
    hashes.append(hashlib.sha256(raw_key.encode()).hexdigest())
    values["NDANI_API_KEY_HASHES"] = ",".join(hashes)
    write_env_file(secrets_path(), values)
    print(raw_key)
    _say("restart the gateway to load it: `ndani up` (no downtime for other containers)")
    return 0


def cmd_bundle_build(args) -> int:
    llm, embed = Path(args.llm), Path(args.embed)
    for p in (llm, embed):
        if not p.exists():
            raise SystemExit(f"ndani: {p} does not exist")
    images = None
    if args.with_images:
        cfg = load_config()
        images = cfg.image_refs()
        _say(f"saving images: {', '.join(images)}")
    manifest = bundle.build(Path(args.out), llm, embed, args.name, images,
                            Path(args.sign_key) if args.sign_key else None)
    total = sum(f["bytes"] for f in manifest["files"]) / 1e9
    _say(f"bundle '{args.name}' written to {args.out}: {len(manifest['files'])} files, {total:.2f} GB")
    return 0


def cmd_bundle_verify(args) -> int:
    result = bundle.verify(Path(args.path), Path(args.allowed_signers) if args.allowed_signers else None,
                           require_signature=args.require_signature,
                           progress=(lambda rel: print(f"  hashing {rel}")) if args.verbose else None)
    for problem in result.problems:
        print(f"  - {problem}")
    _say(result.summary())
    return 0 if result.ok else 1


def cmd_prepare(args) -> int:
    """Fetch/build images while the machine still has network access."""
    cfg = load_config()
    _write_runtime_env(cfg)
    if args.from_bundle:
        return _load_bundled_images(cfg)
    subprocess.run(_compose(cfg, "build"), check=True)
    subprocess.run(_compose(cfg, "pull", "--ignore-buildable"), check=True)
    _say("images ready. You can now cut network access and run `sudo ndani up`.")
    return 0


def _load_bundled_images(cfg) -> int:
    images_dir = cfg.bundle_dir / "images"
    tars = sorted(images_dir.glob("*.tar")) if images_dir.exists() else []
    if not tars:
        _say(f"no image tarballs in {images_dir}")
        return 1
    for tar in tars:
        subprocess.run(["docker", "load", "-i", str(tar)], check=True)
    _say(f"loaded {len(tars)} images from bundle")
    return 0


def _write_runtime_env(cfg) -> None:
    values = read_env_file(secrets_path())
    if not values.get("NDANI_DB_PASSWORD"):
        raise SystemExit("ndani: missing secrets, run `ndani init` first")
    write_env_file(COMPOSE_ENV, compose_env(cfg, values["NDANI_DB_PASSWORD"]))
    write_env_file(GATEWAY_ENV, gateway_env(cfg, values["NDANI_DB_PASSWORD"],
                                            values.get("NDANI_API_KEY_HASHES", "")))


def cmd_up(args) -> int:
    cfg = load_config()
    if cfg.guard.get("enabled", True) and not args.no_guard:
        _require_root("up")

    _say(f"verifying bundle {cfg.bundle_dir} (this hashes every weight file)")
    result = bundle.verify(cfg.bundle_dir, Path(args.allowed_signers) if args.allowed_signers else None,
                           require_signature=args.require_signature)
    if not result.ok:
        for problem in result.problems:
            print(f"  - {problem}")
        raise SystemExit("ndani: refusing to start with an unverified bundle")
    _say(result.summary())

    _write_runtime_env(cfg)
    cfg.audit_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(cfg.audit_dir, 10001, 10001)  # gateway runs as uid 10001
    except PermissionError:
        os.chmod(cfg.audit_dir, 0o777)

    if (cfg.bundle_dir / "images").exists():
        _load_bundled_images(cfg)

    # Start containers first so Docker creates the bridges, then seal.
    subprocess.run(_compose(cfg, "up", "-d", "--no-build", "--pull", "never", "--remove-orphans"), check=True)
    if cfg.guard.get("enabled", True) and not args.no_guard:
        for action in guard.apply(guard.GuardPolicy.from_config(cfg.guard)):
            _say(action)
    else:
        _say("WARNING: egress guard disabled; containers on the edge network can reach out")
    _say(f"gateway on http://{cfg.bind_addr}:{cfg.port}/v1. Run `sudo ndani verify --api-key ...` next.")
    return 0


def cmd_down(_args) -> int:
    cfg = load_config()
    subprocess.run(_compose(cfg, "down"), check=True)
    _say("stack stopped. Guard rules stay in place (`sudo ndani guard remove` to lift them).")
    return 0


def cmd_verify(args) -> int:
    cfg = load_config()
    if os.geteuid() != 0:
        _say("not root: guard checks will be incomplete")
    api_key = args.api_key or os.environ.get("NDANI_API_KEY")
    report = verifier.run(cfg, api_key)
    md, js = verifier.save(report)
    for check in report.checks:
        print(f"[{check.mark}] {check.group:<22} {check.description}: {check.detail}")
    counts = report.counts()
    print(f"\n{'PASSED' if report.ok else 'FAILED'}: {counts['PASS']} passed, "
          f"{counts['FAIL']} failed, {counts['SKIP']} skipped")
    _say(f"report: {md}")
    return 0 if report.ok else 1


def cmd_guard(args) -> int:
    if args.action == "status":
        for k, v in guard.status().items():
            print(f"{k}: {v}")
        return 0
    if args.action == "show":
        cfg = load_config()
        for action in guard.apply(guard.GuardPolicy.from_config(cfg.guard), dry_run=True):
            print(action)
        return 0
    _require_root(f"guard {args.action}")
    if args.action == "apply":
        cfg = load_config()
        actions = guard.apply(guard.GuardPolicy.from_config(cfg.guard))
    else:
        actions = guard.remove()
    for action in actions:
        _say(action)
    return 0


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ndani", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="show hardware and the recommended profile").set_defaults(func=cmd_detect)
    sub.add_parser("profiles", help="list hardware profiles").set_defaults(func=cmd_profiles)

    s = sub.add_parser("init", help="write ndani.toml and generate secrets")
    s.add_argument("--profile")
    s.add_argument("--bundle-dir", default="/opt/ndani/bundle")
    s.add_argument("--allow-cidr", action="append", default=[], help="host egress allowlist CIDR")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    k = sub.add_parser("keys", help="manage API keys").add_subparsers(dest="keys_cmd", required=True)
    k.add_parser("add", help="create a new API key").set_defaults(func=cmd_keys_add)

    b = sub.add_parser("bundle", help="build or verify an offline model bundle").add_subparsers(
        dest="bundle_cmd", required=True)
    bb = b.add_parser("build", help="on a connected machine: package model files with hashes")
    bb.add_argument("--llm", required=True, help="model dir (vLLM) or .gguf file (llama.cpp)")
    bb.add_argument("--embed", required=True, help="embedding model .gguf")
    bb.add_argument("--out", required=True)
    bb.add_argument("--name", required=True)
    bb.add_argument("--with-images", action="store_true", help="also `docker save` all images")
    bb.add_argument("--sign-key", help="ssh private key to sign manifest.json")
    bb.set_defaults(func=cmd_bundle_build)
    bv = b.add_parser("verify", help="check every file against the manifest")
    bv.add_argument("path")
    bv.add_argument("--allowed-signers")
    bv.add_argument("--require-signature", action="store_true")
    bv.add_argument("-v", "--verbose", action="store_true")
    bv.set_defaults(func=cmd_bundle_verify)

    pr = sub.add_parser("prepare", help="build/pull images while network is still available")
    pr.add_argument("--from-bundle", action="store_true", help="load images from <bundle>/images instead")
    pr.set_defaults(func=cmd_prepare)

    u = sub.add_parser("up", help="verify bundle, start the stack, seal the host")
    u.add_argument("--allowed-signers")
    u.add_argument("--require-signature", action="store_true")
    u.add_argument("--no-guard", action="store_true", help="development only")
    u.set_defaults(func=cmd_up)

    sub.add_parser("down", help="stop the stack").set_defaults(func=cmd_down)

    v = sub.add_parser("verify", help="attempt every escape route and write a report")
    v.add_argument("--api-key")
    v.set_defaults(func=cmd_verify)

    g = sub.add_parser("guard", help="manage the egress guard")
    g.add_argument("action", choices=["apply", "remove", "status", "show"])
    g.set_defaults(func=cmd_guard)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"ndani: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"ndani: command failed: {' '.join(map(str, exc.cmd))}", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
