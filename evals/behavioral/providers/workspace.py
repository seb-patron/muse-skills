"""Shared historical-workspace preparation for behavioral eval providers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ProviderError(RuntimeError):
    """A deterministic fixture or agent execution failure."""


def run(args: list[str], cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def git_output(repo: Path, *args: str) -> str:
    result = run(["git", *args], repo)
    if result.returncode != 0:
        raise ProviderError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def resolve_commit(repo: Path, revision: str) -> str:
    if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
        raise ProviderError(f"revision must be a 7-40 character hexadecimal SHA: {revision!r}")
    return git_output(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")


def validate_skill_name(skill_name: str) -> None:
    if not SKILL_RE.fullmatch(skill_name):
        raise ProviderError(f"invalid skill name {skill_name!r}")


def prepare_historical_workspace(
    repo_root: Path,
    destination: Path,
    base_revision: str,
    head_revision: str,
) -> tuple[str, str]:
    base_sha = resolve_commit(repo_root, base_revision)
    head_sha = resolve_commit(repo_root, head_revision)
    ancestry = run(["git", "merge-base", "--is-ancestor", base_sha, head_sha], repo_root)
    if ancestry.returncode != 0:
        raise ProviderError(f"base {base_sha} is not an ancestor of head {head_sha}")

    initialized = run(["git", "init", "--quiet", str(destination)], repo_root)
    if initialized.returncode != 0:
        raise ProviderError(initialized.stderr.strip() or "fixture repository initialization failed")
    fetched = run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-c",
            "fetch.writeCommitGraph=false",
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            str(repo_root),
            head_sha,
        ],
        destination,
    )
    if fetched.returncode != 0:
        raise ProviderError(fetched.stderr.strip() or "allowed fixture history fetch failed")
    checkout = run(["git", "checkout", "--quiet", "--detach", head_sha], destination)
    if checkout.returncode != 0:
        raise ProviderError(checkout.stderr.strip() or "fixture checkout failed")
    local_base = resolve_commit(destination, base_sha)
    local_head = resolve_commit(destination, head_sha)
    local_ancestry = run(["git", "merge-base", "--is-ancestor", local_base, local_head], destination)
    if local_ancestry.returncode != 0:
        raise ProviderError("fixture transfer did not preserve base-to-head ancestry")
    return local_base, local_head


def prepare_remote_historical_workspace(
    source_url: str,
    destination: Path,
    base_revision: str,
    head_revision: str,
) -> tuple[str, str]:
    """Fetch one authenticated exact-head history without retaining its remote identity."""

    for label, revision in (("base", base_revision), ("head", head_revision)):
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ProviderError(f"remote {label} revision must be a full lowercase SHA")

    initialized = run(["git", "init", "--quiet", str(destination)], destination.parent)
    if initialized.returncode != 0:
        raise ProviderError(initialized.stderr.strip() or "fixture repository initialization failed")
    fetched = run(
        [
            "git",
            "-c",
            "fetch.writeCommitGraph=false",
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            source_url,
            head_revision,
        ],
        destination,
    )
    if fetched.returncode != 0:
        raise ProviderError(fetched.stderr.strip() or "allowed remote fixture history fetch failed")
    checkout = run(["git", "checkout", "--quiet", "--detach", head_revision], destination)
    if checkout.returncode != 0:
        raise ProviderError(checkout.stderr.strip() or "fixture checkout failed")
    local_base = resolve_commit(destination, base_revision)
    local_head = resolve_commit(destination, head_revision)
    ancestry = run(["git", "merge-base", "--is-ancestor", local_base, local_head], destination)
    if ancestry.returncode != 0:
        raise ProviderError(f"base {local_base} is not an ancestor of head {local_head}")
    return local_base, local_head


def repo_root(options: dict[str, Any]) -> Path:
    config = options.get("config") or {}
    explicit = config.get("repo_root")
    if explicit:
        return Path(explicit).expanduser().resolve()
    base_path = config.get("basePath")
    if not base_path:
        raise ProviderError("Promptfoo did not supply config.basePath")
    return Path(base_path).resolve().parents[1]
