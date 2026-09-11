"""Deterministic offline provider: reports the skills/ directory layout.

Ignores test vars. Output is a fixed header line followed by one sorted
relative path per file under skills/, so assertions can pin the layout
(exactly one SKILL.md per skill directory, no stray files).
"""

from pathlib import Path


def call_api(prompt, options, context):
    repo_root = Path(__file__).resolve().parents[2]
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return {"output": "", "error": "missing skills/ directory"}
    files = sorted(
        str(p.relative_to(repo_root))
        for p in skills_dir.rglob("*")
        if p.is_file()
    )
    return {"output": "SKILL_FILES:\n" + "\n".join(files)}
