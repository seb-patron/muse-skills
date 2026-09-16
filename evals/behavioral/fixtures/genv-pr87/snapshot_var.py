"""Hash-verified loader for the Gen V PR #87 title and body snapshots.

These snapshots are pull-request metadata of a private repository, so this public
repository stores only their SHA-256 digests and provenance in ``SNAPSHOTS.yaml``. The
text itself lives in a local directory outside the repository, named by
``GENV_PR87_SNAPSHOT_DIR`` (default: ``~/.local/share/muse-skills/genv-pr87-snapshots``).

Promptfoo calls ``get_var`` for any case variable whose value is ``file://`` this file.
Delivery fails loudly when the directory is missing or a file does not match its
recorded digest; it never silently substitutes another stage or an unverified body.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import yaml

SNAPSHOTS = Path(__file__).resolve().parent / "SNAPSHOTS.yaml"
ENV_VAR = "GENV_PR87_SNAPSHOT_DIR"
DEFAULT_DIR = Path("~/.local/share/muse-skills/genv-pr87-snapshots")
SERVED_VARS = {"pr_body_snapshot": "body", "pr_title_snapshot": "title"}


class SnapshotError(RuntimeError):
    """A missing, unreadable, or digest-mismatched snapshot."""


def snapshot_dir() -> Path:
    configured = os.environ.get(ENV_VAR)
    return Path(configured).expanduser() if configured else DEFAULT_DIR.expanduser()


def manifest() -> dict[str, Any]:
    with SNAPSHOTS.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _expected_files(data: dict[str, Any]) -> list[str]:
    files = [str(entry["file"]) for entry in (data.get("stages") or {}).values()]
    files.append(str(data.get("pull_request_title_file", "")))
    return [name for name in files if name]


def available() -> bool:
    """True when every frozen snapshot is present locally; offline tests skip otherwise."""

    directory = snapshot_dir()
    return all((directory / name).is_file() for name in _expected_files(manifest()))


def _verified_text(path: Path, stored: str, delivered_digest: str, label: str) -> str:
    if not path.is_file():
        raise SnapshotError(
            f"PR #87 {label} snapshot is not available at {path.name}. Set {ENV_VAR} to the "
            f"directory holding the sanitized snapshots (default {DEFAULT_DIR}); this public "
            "repository stores only their digests. DEVELOPMENT_V4.md explains how to "
            "reconstruct them from the source pull request."
        )
    content = path.read_bytes()
    delivered = content.decode("utf-8").strip()
    for kind, actual, expected in (
        ("stored", hashlib.sha256(content).hexdigest(), stored),
        ("delivered", hashlib.sha256(delivered.encode("utf-8")).hexdigest(), delivered_digest),
    ):
        if expected and actual != expected:
            raise SnapshotError(
                f"PR #87 {label} {kind} digest mismatch: expected {expected}, got {actual}. "
                "Refusing to deliver an unverified snapshot."
            )
    return delivered


def load_title() -> str:
    data = manifest()
    return _verified_text(
        snapshot_dir() / str(data.get("pull_request_title_file", "")),
        str(data.get("pull_request_title_file_sha256", "")),
        str(data.get("pull_request_title_sha256", "")),
        "title",
    )


def load_stage(stage: str) -> dict[str, str]:
    """Return the digest-verified title and body for one frozen stage."""

    data = manifest()
    entry = (data.get("stages") or {}).get(stage)
    if entry is None:
        raise SnapshotError(f"unknown PR #87 chain stage {stage!r}")
    body = _verified_text(
        snapshot_dir() / str(entry["file"]),
        str(entry.get("sha256", "")),
        str(entry.get("delivered_sha256", "")),
        f"stage {stage} body",
    )
    return {"title": load_title(), "body": body}


def get_var(var_name: str, prompt: str, other_vars: dict[str, Any]) -> dict[str, str]:
    """Promptfoo variable loader for pr_body_snapshot and pr_title_snapshot."""

    del prompt
    field = SERVED_VARS.get(var_name)
    if field is None:
        raise SnapshotError(f"{var_name} is not served by the PR #87 snapshot loader")
    stage = str((other_vars or {}).get("pr_body_stage", "")).strip()
    if not stage:
        raise SnapshotError(f"{var_name} requires the case to declare pr_body_stage")
    return {"output": load_stage(stage)[field]}
