"""Deterministic offline provider: returns the raw text of a skill file.

Expects test var `skill` (e.g. "adversarial-review"). No network, no model.
"""

from pathlib import Path


def call_api(prompt, options, context):
    try:
        vars_ = (context or {}).get("vars", {}) or {}
    except Exception:
        vars_ = {}
    skill = (vars_.get("skill") or "").strip()
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "skills" / skill / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"output": "", "error": f"missing skills/{skill}/SKILL.md"}
    except OSError as exc:
        return {"output": "", "error": f"cannot read skills/{skill}/SKILL.md: {exc}"}
    return {"output": text}
