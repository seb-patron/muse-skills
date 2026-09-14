# Machine lore (Sebastians-MacBook-Pro, Darwin arm64)

Environment-specific workarounds that are true of this machine, not of any
repository. Do not copy these into project repos. Each entry carries a
delete-when condition; delete the entry (not the file) when it fires.

## Broken `~/.local/bin/python3.13` shim

The `python3.13` on PATH is a uv-managed stub with a wrong install prefix:
it crashes on startup (`ModuleNotFoundError: No module named 'encodings'`).
Bit gen-v validation twice (direct suite runs and the pre-push hook during
push on PR #94, 2026-09-14).

- Workaround: export
  `VALIDATION_PYTHON=/Users/sebastianpatronsoto/.local/share/uv/python/cpython-3.13-macos-aarch64-none/bin/python3.13`
  (verified: Python 3.13.15). The gen-v hook honors it; it is the documented
  override, not a bypass (`--no-verify` remains forbidden).
- Durable fix proposed: gen-v #61 comment (hook error should name the
  override). Machine-side shim repair is separate.
- Delete when: bare `python3.13 --version` prints 3.13.x on this machine.

## Child-result archaeology fallback

If a subagent verdict arrives truncated (marker `[native child final answer
truncated]` or a mid-sentence cut), the child run itself completed: its full
text sits in its own session log. Recipe (verified 2026-09-14, ~15–20 min
for two verdicts):

1. Open `<parent-session-dir>/subagent/<run-id>/session.jsonl` (parent dir:
   `~/.local/share/muse/sessions/YYYY/MM/DD/<session-id>/`).
2. Scan records for `payload.event.text` containing the verdict keyword with
   kind `assistant_message_committed`; keep the longest match.
3. Treat the recovered text as the verdict; note the recovery in the review
   record.

- Delete when: full verdicts deliver intact for 3 consecutive reviews
  (record the dates here when retiring). If the harness fixes transport,
  this whole section goes.
