# Reviewer isolation design (issue #24)

Status: **proposal awaiting owner approval.** No OS identity, permission,
credential or setting has been changed. Nothing here is verified yet; the
verification record comes after the approved setup is applied.

Tracking: [#24](https://github.com/seb-patron/muse-skills/issues/24), parent
[#5](https://github.com/seb-patron/muse-skills/issues/5). Evidence and
accounting interface: [#25](https://github.com/seb-patron/muse-skills/issues/25).

## Problem

The subject reviewer (Muse Spark 1.3 Contributor under `muse exec`) currently
runs as the owner's OS user. Muse's shell sandbox (`--sandbox-network
restricted`) blocks tool network access but not filesystem reads, and Muse has
no read allow-list flag. The #18 probe read a parent-directory canary, a file in
an unrelated home folder and listed sibling checkouts. On this host the subject
can currently reach, as the owner:

- the eval repository, answer keys, prior results and traces;
- sibling and later clones of case sources;
- the owner's Muse data (`~/.local/share/muse`: other sessions, memory,
  session index);
- user-scope Muse skills, **including an installed copy of the production
  `adversarial-review` skill** (visible to every condition, which matters for a
  true no-skill control);
- shared `/tmp` files written by earlier sessions.

A different TMPDIR, a separate worktree or a clean command scan does not
prevent any of this.

## Chosen boundary: a dedicated OS identity with a fresh per-attempt home

The smallest change that gives an OS-enforced read boundary without disabling
Muse's own tool sandbox:

```
owner (trusted)                                 <reviewer> (subject)
-----------------                               ----------------------------------
prepare exact-head clone (existing code)
git bundle <head>  ──stdin──►  helper run   ──► /Users/<reviewer>/attempts/<id>/
skill file (if any) ─────────►                    repo/   (clone from bundle, no remote)
                                                  home/   (fresh HOME per attempt)
                                                  tmp/    (TMPDIR, 0700)
                               pinned muse exec (same flags for every condition)
JSONL ◄──────────── stdout ────
evidence tar ◄──── helper export  (session records + tmp, before cleanup)
                   helper cleanup (rm attempt dir, verify empty)
```

Why not the alternatives from the #18 brief:

| Option | Why not chosen |
| --- | --- |
| Detect-and-quarantine only | Detection, not prevention (#18's accepted limit). Kept as a second layer. |
| Outer `sandbox-exec` profile | Nesting inside Muse's own seatbelt fails, so it needs `--disable-sandbox`, which drops the tool network restriction. The Muse process itself needs network, so one profile cannot restore it. |
| Unmounted disk image | Hides some material but not the eval repo, owner Muse data or skills. |

## Allowed and denied surfaces

| Surface | Subject access | Enforced by |
| --- | --- | --- |
| Its attempt checkout (`attempts/<id>/repo`), exact head and ancestors only | read/write | reviewer owns it; bundle contains only `head`'s history |
| Selected project skill at `repo/.agents/skills/<name>/SKILL.md` (skill conditions only) | read | written by helper |
| Its attempt scratch (`attempts/<id>/tmp`) and fresh `HOME` | read/write | reviewer owns it, 0700 |
| Pinned Muse binary, system tools (`/usr/bin`, `/bin`, `/opt/homebrew/bin`) | execute | world-executable, owner-independent paths |
| Reviewer Muse credential (`/Users/<reviewer>/.config/muse/auth.json`) | read (by Muse) | 0600 reviewer; unavoidable for authentication |
| Muse service API | network from the Muse process only | Muse's own client |
| Tool network (shell commands) | **denied** | `--sandbox-network restricted` (verify) |
| Owner home, eval repo, answer keys, results, traces | **denied** | owner home 0700 (host change H2) |
| Owner Muse sessions, memory, session index, user skills | **denied** | owner home 0700 |
| Earlier or sibling attempts and their Muse records | **denied** by absence | helper deletes each attempt after export; runs are sequential under a lock; preflight requires an empty attempts root |
| Later commits, refs, remotes, alternates of the case source | **absent** | bundle of `head` only; no remote; existing workspace tests |
| Inherited user skills/memory | **absent** | fresh `HOME` per attempt; `--no-foreign-personal-context` retained |
| Bundled Muse skills (`bundled:*`) | present, identical in all conditions | recorded per attempt with `muse skills list --json` |
| World-readable shared locations (`/private/tmp`, `/private/var/tmp`, `/Users/Shared`) | readable if files are world-readable | **not preventable here**; harness umask 077 plus a preflight scan (H7) |
| Server-side account state for the Muse login | unknown | residual risk; recorded, not claimed |

Traversal, symlinks and Git indirection do not widen access: the kernel checks
the reviewer's permissions on the resolved target, so `..`, symlinks placed in
the checkout, `git -C`, `--git-dir` and alternates all hit the same denials.
The preflight checks each form explicitly rather than assuming it.

The permitted history must itself be free of solution material: for case
sources this is the frozen exact head and its ancestors, which the existing
case-intake checks already cover. The preflight also checks that no object
newer than `head` is reachable.

## Exact proposed host changes (for owner approval)

Nothing below has been run. Commands use placeholders: `<owner>` is the
current user, `<reviewer>` the new account (proposed name `musereviewer`).

- **H1 — Create a standard, hidden, non-admin account.**
  `sudo sysadminctl -addUser <reviewer> -fullName "Muse eval reviewer" -home /Users/<reviewer> -password -`
  then `sudo dscl . -create /Users/<reviewer> IsHidden 1`, then
  `sudo createhomedir -c -u <reviewer>`.
  No admin group, no Secure Token needed, no login items.
- **H2 — Close the owner home to other local users.**
  `chmod 700 /Users/<owner>` (today it is `drwxr-x---+` with group `staff`,
  and every local account's primary group is `staff`, so a new account could
  otherwise traverse it and read the group-readable Muse data and session
  index). Reversible with `chmod 750 /Users/<owner>`. Alternative if you prefer
  not to touch your home: give `<reviewer>` its own primary group instead of
  `staff` (`dscl` edit); H2 is simpler and also covers future accounts.
- **H3 — Pin the Muse runtime for the subject.**
  `sudo install -d -o root -g wheel -m 0755 /usr/local/libexec/muse-eval`
  `sudo install -o root -g wheel -m 0755 <owner-muse-binary-1.3.0-R3233.1> /usr/local/libexec/muse-eval/muse-1.3.0-R3233.1`
  All three #19 conditions use this exact binary; auto-update cannot change it.
- **H4 — Reviewer Muse login (you perform it).** `sudo -iu <reviewer>` then
  `/usr/local/libexec/muse-eval/muse-1.3.0-R3233.1 login`, completing the
  browser flow with the account/subscription you choose for the subject.
  Claude and the coding workers never handle the credential. Confirm this is
  an acceptable use of your Muse subscription before doing it.
- **H5 — Root-owned subject helper** (implemented by a bounded coding task
  only after you approve this design):
  `/usr/local/libexec/muse-eval/muse-review-subject`, 0755 root, with
  subcommands `run`, `export`, `cleanup`, `preflight`. It validates the attempt
  id, refuses an existing attempt directory, holds a lock so attempts run
  sequentially, builds the attempt directory from a bundle on stdin, sets a
  minimal environment (`HOME`, `XDG_CONFIG_HOME`, `MUSE_AUTH_PATH`, `TMPDIR`,
  `PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin`,
  `MUSE_NO_AUTO_UPDATE=1`; nothing else inherited), and accepts only the fixed
  `muse exec` flag set the provider already uses (allow-listed, no
  `--disable-sandbox`, no `--yolo`).
- **H6 — One sudoers drop-in.** `/etc/sudoers.d/muse-eval`, 0440 root, checked
  with `sudo visudo -cf` before install:
  `<owner> ALL=(<reviewer>) NOPASSWD: /usr/local/libexec/muse-eval/muse-review-subject`
  This lets the harness run only that helper as the reviewer, nothing else,
  and never grants the reviewer any privilege.
- **H7 — No firewall or network setting changes.** Tool network denial relies
  on Muse's `--sandbox-network restricted`, which is verified, not assumed.
  Shared-temp exposure is handled by the harness umask (already 077 in the
  managed runner) and a preflight scan that fails if owner-owned
  world-readable files exist in `/private/tmp`, `/private/var/tmp` or
  `/Users/Shared`.

Rollback: remove the sudoers drop-in, the helper directory and the account
(`sudo sysadminctl -deleteUser <reviewer>`), and restore `chmod 750` on the
owner home.

## Harness interface (integrated through the #25 writer, after approval)

- #25 adds `muse_sessions_root` / `MUSE_EVAL_SESSIONS_ROOT` so the trusted
  provider reads session records from an extracted evidence copy instead of
  the owner's store.
- After approval, one bounded change adds a `subject_launcher` provider option:
  the provider still prepares and verifies the exact-head clone as today,
  creates `git bundle create <tmp> <head>`, calls `helper run` with the fixed
  flags, then `helper export` into the attempt's private evidence directory
  **before** `helper cleanup`. Any helper failure is an attempt error, never an
  approval. Identity, completion and accounting stay exactly as #25 defines
  them. The same writer owns `muse_provider.py` and `run.mjs`; no parallel
  edits.

## Synthetic preflight (no inference; run as the reviewer through `helper preflight`)

Canaries are synthetic files the owner creates with known content; no real
answer is read or printed. Each check prints only `DENIED` or `READABLE`.

1. **Owner-side reads denied:** canaries in the owner home, the eval
   repository path, an answer-key path, the results/traces directory, the
   owner Muse sessions directory, session index, memory and user skills
   directory, and a sibling clone path. Each tried by absolute path, by `..`
   traversal from the checkout, through a symlink planted in the checkout,
   and with `ls` on the parent. All must be `DENIED`.
2. **Git boundary:** `git -C repo rev-list --all` equals the ancestors of
   `head`; a known later commit fails `git cat-file -e`; no remote, no
   `objects/info/alternates`, no owner path in `git config --list
   --show-origin`.
3. **Attempt isolation:** attempts root contains only the current attempt;
   a canary left by a previous preflight attempt is absent after its cleanup;
   fresh `HOME` has no user skills (`muse skills list --json` shows only
   `bundled:*`) and no memory directory content.
4. **Shared temp:** an owner canary in `/private/tmp` with mode 0600 is
   `DENIED`; the scan in H7 is empty. (A world-readable file would be readable;
   that is the documented limit.)
5. **Legitimate work still possible:** `git diff base..head --stat`,
   `git show head:<file>`, `python3 -c 'import sys'`, a trivial `unittest` run
   inside the checkout, `node --version`; writes succeed in `repo/` and
   `tmp/`. Record resolved tool paths.
6. **Equal conditions:** the helper records the same binary hash, flag set,
   environment keys and bundled skill list for production, candidate and
   no-skill runs; the no-skill checkout has no `.agents/skills` entry for the
   review skill.

Checks 1–6 prove the filesystem boundary without any model call, because the
subject's shell tools run as the reviewer and Muse's sandbox can only narrow
that access.

**Network boundary needs one live probe.** Confirming that Muse's shell tool
cannot reach the network while the Muse process can reach its API requires a
real `muse exec` call. Proposed: one call, low effort, `--max-model-steps 4`,
against a throwaway synthetic repository with no private data, asking the tool
to run `curl -sS -m 5 https://example.com` and `ls` on one owner canary path.
Expected: both denied, run completes. This needs its own explicit approval
(one Muse call; zero grader calls).

## What remains after approval

1. Apply H1–H4 (owner) and H6 after review of the exact file.
2. Bounded helper coding task (H5) plus the `subject_launcher` integration,
   with native worker review and independent Opus acceptance.
3. Run the synthetic preflight; if approved, the single network probe.
4. Publish a sanitized verification record; keep raw outputs private.
5. Independent acceptance; then #5's #24 checkbox may be updated.
