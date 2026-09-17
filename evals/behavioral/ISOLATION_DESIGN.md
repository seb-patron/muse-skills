# Reviewer isolation design (issue #24)

Status: **proposal; not approved.** No account, permission, ACL, sudoers
entry, credential or setting has been changed. Every "denied" below is an
intended property to be verified by the plan at the end, not an observed
result.

Tracking: [#24](https://github.com/seb-patron/muse-skills/issues/24), parent
[#5](https://github.com/seb-patron/muse-skills/issues/5). Evidence and
accounting interface: [#25](https://github.com/seb-patron/muse-skills/issues/25).
Consumer: [#19](https://github.com/seb-patron/muse-skills/issues/19).

Revision 3 answers the owner-requested review on PR #26. It makes the account
visible, drops the shared-temp permission sweep and the whole-home mode change,
fixes the group-access flaw in the old H7 check, and moves passwordless sudo to
a separate optional step.

## Goal and non-goals

Goal: the Muse reviewer under evaluation cannot read answer keys, prior
attempts, later fixes of the case source, or inherited review skills. It must
still be able to review the exact change with ordinary tools. The owner's
everyday development continues unchanged.

Non-goals: general workstation hardening, a generic runner, a VM or container
framework, new subscriptions, or changes to how other agents on this Mac work.

## Problem

The reviewer currently runs as the owner. Muse's shell sandbox
(`--sandbox-network restricted`) blocks tool network access but not file reads,
and Muse has no read allow-list. As the owner, the reviewer can reach the eval
repository and its answer keys, prior results and traces, other checkouts of
case sources, the owner's Muse data, and user-installed Muse skills. The last
includes a copy of the production `adversarial-review` skill, which rules out a
true no-skill control. Shared temporary directories also hold world-readable
copies of repositories written by unrelated work. This repository is public,
so any network route to GitHub is also an answer-key route.

## Identity model

- **`<reviewer>`**: a new **visible, standard (non-admin)** macOS account. It is
  not hidden; hiding adds nothing to the boundary. It has no admin rights and
  cannot use `sudo`.
- **Root-owned helper files**: the helper script and the pinned Muse binary are
  *owned* by root only so the reviewer cannot modify them. Ownership does not
  decide who they run as.
- **Execution identity**: the owner starts the helper with
  `sudo -u <reviewer> <helper> ...`. The helper, Muse and every tool Muse
  starts run as `<reviewer>`, never as root and never as the owner.

## Boundary

```
owner (trusted)                                   <reviewer> (subject, standard account)
-----------------                                 --------------------------------------
prepare exact-head clone (existing harness)
bundle of HEAD ─────stdin──►  helper run      ──► /Users/<reviewer>/attempts/<opaque id>/
skill bytes + sha ──stdin──►   (env -i,             repo/   (from bundle, detached, no remote)
                                reset before)       home/   (fresh HOME per attempt)
                                                    tmp/    (TMPDIR, 0700)
                               pinned muse exec (one fixed flag set for every condition)
JSONL ◄──────────── stdout ────
evidence tar ◄──── helper export   (untrusted; extracted safely by the owner)
                   helper cleanup  (reviewer-identity reset only)
```

Reads are denied by one mechanism: **reviewer-specific deny ACL entries** on
the few directories that lead to answer-bearing material (H2). An ACL entry
naming `<reviewer>` affects only that account. File modes, groups and
everything the owner and other accounts can do stay unchanged. Because the
entry denies traversal of the directory itself, it also covers files created
there later by any process, not just today's leftovers.

## Surfaces

| Surface | Reviewer access | Mechanism | Evidence required |
| --- | --- | --- | --- |
| Its checkout (exact head and ancestors only) | read/write | reviewer owns it; bundle of `HEAD` | object-set check (V3) |
| Selected skill (skill conditions only) | read | helper writes stdin bytes after a hash check | V8 |
| Its scratch and fresh `HOME` | read/write | reviewer-owned, 0700 | V7 |
| Pinned Muse binary; tools in `/usr/bin`, `/bin`, `/opt/homebrew/bin` | execute | H3; system paths | V7 |
| Tool network, including GitHub | **denied** | Muse `--sandbox-network restricted` | live probe (separate approval) |
| Everything under the owner home: eval repo, keys, results, traces, owner Muse data and skills, owner checkouts | **denied** | H2 ACL entry on the owner home | V2 effective-access attempts |
| `/private/tmp`, `/private/var/tmp`, `/Users/Shared`, including future artifacts | **denied** (read and write) | H2 ACL entries on those directories | V2, including the shared-group counterexample |
| Earlier attempts and anything they left | **absent** | reviewer-identity reset | V4 |
| Later commits, refs, remotes, alternates | **absent** | bundle fetch; no remote, reflog or `FETCH_HEAD` | V3 |
| Inherited user skills and memory | **absent** | fresh `HOME`; `--no-foreign-personal-context` | V8 |
| Other world-readable locations (for example external volumes or directories outside home) | **not prevented** | exposure scan run as the reviewer at batch boundaries (detection) | V6; residual R1 |
| Other users' process command lines | **visible** (macOS shows them) | none; narrow operating decision | residual R2 |
| Owner GUI-session services (pasteboard, Spotlight) | must not reveal owner data | verify; fallback decision | V5; residual R3 |
| Server-side Muse account state | unknown | none | residual R4 |

## Proposed host changes (each needs owner approval)

Before applying anything, record the current state:
`ls -led /Users/<owner> /private/tmp /private/var/tmp /Users/Shared`,
`dscl . -list /Users UniqueID`, and `ls -l /etc/sudoers.d`. Rollback restores
exactly this recorded output.

### H1 — Visible standard account

- **Why:** operating-system permissions only separate different accounts.
- **Change:**
  `sudo sysadminctl -addUser <reviewer> -fullName "<neutral name>" -password -`
  (standard, not `-admin`). The subject can see `whoami`, so use a neutral
  name.
- **Affects:** adds one login-window account. It is never used for GUI login.
- **Side effects:** the account appears in Users & Groups and the login
  window.
- **Verify:** V1. `id <reviewer>` shows no `admin`/`wheel` membership, and
  `sudo -l -U <reviewer>` shows no rights.
- **Rollback:** `sudo sysadminctl -deleteUser <reviewer>` after the H2 entries
  are removed.

### H2 — Reviewer-only deny ACL entries on four directories

- **Why:**
  - A standard account's primary group is `staff`. The owner home is `0750`
    `owner:staff` today, so without a change the reviewer could traverse it
    and read group-readable content.
  - Shared temporary directories are world-traversable, and other work keeps
    writing world-readable files there.
  - A deny entry for `<reviewer>` blocks both without changing anything for
    anyone else.
- **Change:**
  - `chmod +a "user:<reviewer> deny list,search" /Users/<owner>` (the owner
    owns this directory, so no `sudo` is needed).
  - `sudo chmod +a "user:<reviewer> deny list,search,add_file,add_subdirectory" /private/tmp`
  - the same entry on `/private/var/tmp` and on `/Users/Shared`.
- **Affects:** only access checks for `<reviewer>`.
  - No file mode, group or existing ACL entry changes.
  - Nothing is deleted.
  - Other agents keep writing to `/tmp` normally.
- **Why not change the whole home's mode:**
  - `chmod 700` on the home would also work, but it changes access for every
    other account and service, while the reviewer-only entry changes none.
  - Giving the reviewer a non-`staff` primary group would protect the home but
    not shared temp, and it would depend on macOS group-membership
    computation.
- **Side effects:**
  - The reviewer cannot use shared temp at all; its `TMPDIR` is its attempt
    directory. Tools that hard-code `/tmp` will fail as the reviewer. V7 and
    the live probe check that review tools and Muse still work.
  - A macOS permission repair could remove the home entry, so V9 re-checks
    all four entries before every batch.
  - The entries mention the account, so remove them before deleting the
    account.
- **Verify:** V2 (actual denied reads, lists and writes as the reviewer) and V9
  (entries still present). `ls -le` output alone is not accepted as proof.
- **Rollback:** `chmod -a "<exact entry>" <dir>` for each, then compare
  `ls -led` with the recorded state.

### H3 — Pinned Muse runtime

- **Why:**
  - After H2 the reviewer cannot run the owner's Muse install.
  - All #19 conditions must use identical bytes, with no auto-update.
- **Change:**
  - `sudo install -d -o root -g wheel -m 0755 /usr/local/libexec/muse-eval`
  - `sudo install -o root -g wheel -m 0755 <muse-binary-1.3.0-R3233.1> /usr/local/libexec/muse-eval/muse-1.3.0-R3233.1`
- **Affects:** one new root-owned directory. The owner's own Muse install is
  untouched.
- **Side effects:** none for the owner. The owner's launcher exports
  `MUSE_RELEASE_INFO`, so the helper either sets the same value from a pinned
  copy or records that it is absent.
- **Verify:** V7 runs `--version` as the reviewer and records the binary hash.
- **Rollback:** `sudo rm -r /usr/local/libexec/muse-eval`.

### H4 — Reviewer Muse login (owner performs it)

- **Why:** Muse needs a credential owned by the reviewer account.
- **Change:** `sudo -iu <reviewer>`, then run the pinned binary's `login` and
  complete the browser flow. Claude and coding workers never handle it.
- **Owner decision:** which Muse account and subscription to use. No new
  subscription is proposed. If this is your own account, server-side account
  state is shared with your other work (R4).
- **Rollback:** the pinned binary's logout as the reviewer, or delete the
  account.

### H5 — Root-owned subject helper (bounded coding task after design approval)

- **Why:** a small fixed entry point is easier to verify than ad hoc commands,
  and root ownership stops the reviewer from editing it.
- **Change:** `/usr/local/libexec/muse-eval/muse-review-subject`, 0755 root,
  written in `/bin/bash` or `/usr/bin/python3`. It runs as `<reviewer>`
  (see Identity model). Subcommands: `run`, `export`, `cleanup`, `preflight`.
  - Changes to its own attempt directory by absolute path first; the
    caller's working directory may be unreadable after H2.
  - Rebuilds the environment with `env -i`, setting only `HOME`,
    `CFFIXED_USER_HOME`, `XDG_CONFIG_HOME`, `MUSE_AUTH_PATH`, `TMPDIR`,
    `PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin`,
    `MUSE_NO_AUTO_UPDATE=1` and `LANG`.
  - Accepts only an opaque attempt id; refuses an existing directory; holds a
    lock so attempts are sequential.
  - Takes the bundle and the skill bytes plus SHA-256 on stdin. It builds the
    checkout with `git init`, then
    `git -c core.logAllRefUpdates=false fetch --no-tags --no-write-fetch-head <bundle> HEAD:refs/heads/review`,
    then a detached checkout, then deletes the bundle and the ref.
  - If a case's focused tests need packages, it prepares them the same way for
    every condition before Muse starts. The subject itself has no network.
  - Accepts only the fixed `muse exec` flag set: no `--disable-sandbox`,
    `--yolo` or `--sandbox-network enabled`. It owns the Muse process group and
    the timeout.
  - `export` writes a tar of the attempt's Muse session records and `tmp/` to
    stdout, and reports only whether the credential token appears in them.
  - `preflight` runs the fixed checks below, not arbitrary commands.
  - The reset (next section) touches only the reviewer identity.
- **Rollback:** part of H3's directory removal.

### H6 — How the owner starts the helper

- **H6a, initial verification and attended use: no sudoers change.**
  - You are an admin, and macOS's default sudo rules already let you run
    `sudo -u <reviewer> <helper> ...` after entering your password.
  - The preflight, the live probe and a first attended batch use this.
  - Open item for an attended Promptfoo batch: sudo's cached credential is tied
    to the terminal. The provider must therefore call `sudo -n` without
    detaching from that terminal (the helper, not the provider, creates Muse's
    new process group). If that cannot be made to work, H6b is the smallest
    decision.
- **H6b, optional unattended convenience: separate later approval.**
  - `/etc/sudoers.d/muse-eval`, 0440 root, validated with `sudo visudo -cf`:
    `<owner> ALL=(<reviewer>) NOPASSWD: /usr/local/libexec/muse-eval/muse-review-subject`
  - It lets unattended runs start only that helper, as the reviewer, without a
    password.
  - It is not needed to verify the boundary.
  - Rollback: delete the file.

### Removed from revision 2

- the hidden-account flag;
- `chmod 700` on the owner home;
- the permission sweep and deletion of shared-temp leftovers;
- edits to cron/at deny files;
- the rule that other work must not write to `/tmp` during a batch;
- mandatory passwordless sudo.

## Reset between attempts (reviewer identity only)

Each item closes a concrete way to carry information from one attempt into a
later one. The helper runs these before and after every attempt, and stops the
batch on any mismatch it cannot reset.

| Reset | Leak it prevents |
| --- | --- |
| `kill -KILL -1` as the reviewer; `pgrep -U <reviewer>` then shows only the helper | a background process from attempt N reading or editing attempt N+1 |
| `crontab -r`, remove pending `at` jobs, then verify both are empty | a scheduled job carrying notes into a later attempt |
| `launchctl print user/<uid>` equals the baseline (extra services removed) | a persisted agent doing the same |
| clear the reviewer's `DARWIN_USER_TEMP_DIR` and `DARWIN_USER_CACHE_DIR` | stashed data outside the attempt directory |
| reviewer home manifest (excluding `attempts/` and the credential) equals the baseline | stashed data in dotfiles, `~/Library` or the keychain |
| attempts root is empty after `cleanup` | reading an earlier checkout or evidence |

Shared temp needs no cleanup, because H2 denies the reviewer writes there. No
reset touches the owner's files or other accounts.

## Harness interface (the single #25 writer, after approval)

- #25 already adds `muse_sessions_root` / `MUSE_EVAL_SESSIONS_ROOT`, so the
  provider can read session records from an extracted evidence copy.
- A later bounded change adds a `subject_launcher` option:
  - the provider prepares and verifies the exact-head clone as today;
  - it runs `git bundle create <f> HEAD` and checks that
    `git bundle list-heads` is exactly `<head> HEAD`;
  - it then calls `helper run`, and `helper export` **before**
    `helper cleanup`.
  - A helper failure is an attempt error, never an approval.
- The exported tar is untrusted. Extract it with
  `tarfile.extractall(filter="data")` into a fresh 0700 directory, and
  cross-check model, skill observation and usage against the captured stdout.
- **No-skill condition:** under a fresh `HOME` no user skill exists, so the
  harness's same-name placeholder skill is no longer needed for shadowing.
  Removing it is a #19 configuration decision, recorded before the run.

## Verification plan (effective access as the reviewer)

All checks run through the helper, with the same sudo path and environment as
real attempts. Canaries are synthetic. Output is limited to `DENIED`/`ALLOWED`,
`ABSENT`/`PRESENT` or counts. Permission-bit or ACL listings are diagnostics
only; **an empty mode scan is never evidence of denied access.**

- **V0 — Record the prior state** (see Proposed host changes).
- **V1 — Identity:**
  - `id <reviewer>`;
  - `dsmemberutil checkmembership -U <reviewer> -G <group>` for `staff`,
    `admin` and `wheel`;
  - `sudo -l -U <reviewer>` shows nothing.
  - Record the real memberships; later checks must hold with them.
- **V2 — Effective-access denials.** Run once after H1 and **before** H2 as a
  positive control (the counterexample below must read as `ALLOWED`), then
  after H2 (everything must read as `DENIED`).
  - **Owner home:** canary files opened by absolute path, through the
    `/System/Volumes/Data` path, through a symlink planted in the checkout,
    and by `..` from the checkout; `ls` of the home. Also the eval repository,
    key, results and trace paths (open attempt only; nothing is printed).
  - **Shared temp:** a `0644` owner file directly in `/private/tmp`.
  - **Shared-group counterexample** in each of `/private/tmp`,
    `/private/var/tmp` and `/Users/Shared`: an `owner:staff` directory with
    mode `0750` containing a `0644` file. A mode scan that only looks at
    "other" bits would skip this directory. The reviewer's `staff` membership
    makes it readable before H2; after H2 it must be denied.
  - **Write attempts** into all three shared directories.
  - **Inode-path bypass:** open a canary through `/.vol/<device>/<inode>`. If
    this succeeds after H2, it is a **blocker**: permission-based isolation
    would not hold on this macOS version. Report it; do not work around it.
- **V3 — Git:**
  - `git cat-file --batch-all-objects --batch-check='%(objectname)'` equals the
    object list from `git rev-list --objects HEAD`;
  - a known later commit is absent;
  - no remote, alternates, reflog entries or `FETCH_HEAD`;
  - no owner path in `git config --list --show-origin`.
- **V4 — Reset:** plant a background process, a cron job, an `at` job, a user
  launchd job, a home dotfile and a per-user-temp file in one preflight
  attempt; all must be gone or detected before the next.
- **V5 — Owner session services:**
  - `pbpaste` must not return an owner pasteboard canary;
  - `mdfind -name <canary>` must return nothing for an owner-home canary.
  - If either fails, see R3.
- **V6 — Answer-bearing exposure scan (detection, batch boundaries).** As the
  reviewer, run a bounded search of other readable locations for:
  - Git repositories whose root commit matches this repository or a
    batch case source;
  - the known key file names;
  - mounted non-system volumes.

  Any hit stops the batch until you decide (see R1).
- **V7 — Legitimate work:**
  - `git diff base..head --stat` and `git show head:<file>`;
  - each case's real focused test command, with `TMPDIR` set and `/tmp`
    denied;
  - `node --version`;
  - the pinned Muse binary's `--version`;
  - writes in `repo/` and `tmp/`.
  - Resolved tool paths are recorded.
- **V8 — Equal conditions and no-skill:**
  - identical binary hash, flag set, environment keys, bundled-skill list and
    announced tool list for all three #19 conditions;
  - the skill file present only where intended;
  - the fresh `HOME` lists only `bundled:*` skills.
- **V9 — Before every batch:** the four H2 entries are present exactly as
  approved (they could be lost after a permission repair or restart).
- **Live network probe (separate approval: one Muse call, zero graders):**
  - Runs through the helper against a throwaway synthetic repository.
  - Makes two tool calls: `read_file` of an owner canary, and `bash probe.sh`.
    The probe reports proxy variables, HTTPS to `github.com` and `pypi.org`,
    raw TCP with DNS reported separately, TCP to an owner loopback canary, and
    a write to `/private/tmp`.
  - Results are judged from `tool.result` events, and the announced tool list
    must contain no web, memory or session-search tool.
  - A positive control runs `probe.sh` through `helper preflight` without Muse.
  - This run also shows that Muse works as the reviewer with `/tmp` denied. If
    it does not, that is a **blocker** needing an owner decision (for example,
    a reviewer-only write exception); it is not silently allowed.

## Residuals and the smallest owner decisions

- **R1 — World-readable locations other than the four H2 directories.**
  - Not prevented. V6 detects answer-bearing copies at batch boundaries, but
    not artifacts created and removed mid-attempt.
  - **Decision:** accept this as detection, or add a reviewer-only H2 entry for
    each specific location V6 finds.
- **R2 — Process command lines.**
  - macOS lets any account see other users' command lines, and nothing here
    changes that. The harness and graders never put key or fix text in
    command lines.
  - **Decision:** during a batch, avoid only work that handles *this batch's*
    answer keys, adjudication or case-source fixes. Unrelated work, including
    anything writing to `/tmp`, is unaffected.
  - #25's command scan could also flag `ps`, `pgrep`, `lsof` and `top` in
    subject traces for audit; that needs separate approval.
- **R3 — GUI-session services.**
  - A process started with `sudo -u` may still reach per-session services such
    as the pasteboard.
  - If V5 fails, **decide** between (a) not copying answer text to the
    pasteboard during a batch, or (b) approving a launch route through
    `launchctl asuser` (a broader sudo rule).
- **R4 — Server-side account state.**
  - Unknown for a shared Muse account; recorded, never claimed away.
  - #19 alternates the condition order.

## Approval sequence

1. Design approval (this PR). This does not approve any host change.
2. Approval of H1–H5 with H6a.
3. Bounded helper coding and `subject_launcher` integration, with native worker
   review and independent acceptance.
4. V0–V9, recorded privately with a sanitized summary.
5. Separate approval, then the one-call live network probe.
6. Optional approval of H6b.
7. Independent acceptance of the verified boundary.

Only after step 7 may #5's #24 checkbox change. #19 stays blocked until then
and until its own run packet is approved.

## Proposed acceptance notes (for owner approval; not applied to any issue)

- **#24:** state that shared-temp and owner-home denial rely on reviewer-only
  ACL entries verified by effective-access attempts. Other world-readable
  locations (R1) and process command lines (R2) are residuals that need an
  explicit owner decision. They are not counted as verified denials.
- **#19:** record the no-skill placeholder decision. State whether the batch is
  attended (H6a) or unattended (needs H6b).
