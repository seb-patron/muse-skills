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

    clone = run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(repo_root), str(destination)],
        repo_root,
    )
    if clone.returncode != 0:
        raise ProviderError(clone.stderr.strip() or "local fixture clone failed")
    checkout = run(["git", "checkout", "--quiet", "--detach", head_sha], destination)
    if checkout.returncode != 0:
        raise ProviderError(checkout.stderr.strip() or "fixture checkout failed")
    return base_sha, head_sha


def repo_root(options: dict[str, Any]) -> Path:
    config = options.get("config") or {}
    explicit = config.get("repo_root")
    if explicit:
        return Path(explicit).expanduser().resolve()
    base_path = config.get("basePath")
    if not base_path:
        raise ProviderError("Promptfoo did not supply config.basePath")
    return Path(base_path).resolve().parents[1]
