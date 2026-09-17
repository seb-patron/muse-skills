# Reviewer isolation design (issue #24)

Status: **proposal awaiting owner approval.** No OS identity, permission,
credential or setting has been changed. Nothing here is verified yet; the
verification record comes after the approved setup is applied.

Tracking: [#24](https://github.com/seb-patron/muse-skills/issues/24), parent
[#5](https://github.com/seb-patron/muse-skills/issues/5). Evidence and
accounting interface: [#25](https://github.com/seb-patron/muse-skills/issues/25).

Revision 2 incorporates an independent adversarial design review (two blocking
and nine should-fix findings, all adopted below).

## Problem

The subject reviewer (Muse Spark 1.3 Contributor under `muse exec`) runs as the
owner's OS user. Muse's shell sandbox (`--sandbox-network restricted`) blocks
tool network access but not filesystem reads, and Muse has no read allow-list.
The #18 probe read a parent-directory canary and a file in an unrelated home
folder, and listed sibling checkouts. On this host the subject can currently
reach:

- the eval repository, answer keys, prior results and traces;
- sibling and later clones of case sources — including **world-readable owner
  clones of the case source and of this repository left in `/private/tmp`**, which
  any local account could read as well;
- the owner's Muse data (other sessions, memory, session index);
- user-scope Muse skills, **including an installed copy of the production
  `adversarial-review` skill**, visible to every condition;
- other owner processes' command lines, owner loopback services and
  world-connectable sockets.

A different TMPDIR, a separate worktree or a clean command scan prevents none
of this. This repository is public, so any network route to GitHub is also an
answer-key route.

## Chosen boundary: a dedicated OS identity, fresh per-attempt home, reset between attempts

```
owner (trusted)                                   <reviewer> (subject)
-----------------                                 ----------------------------------
lock batch; close shared temp (H7)
prepare exact-head clone (existing code)
bundle of HEAD ─────stdin──►  helper run      ──► /Users/<reviewer>/attempts/<opaque id>/
skill bytes + sha ──stdin──►   (env -i,             repo/   (fetched from bundle, detached, no remote)
                                reset before)       home/   (fresh HOME per attempt)
                                                    tmp/    (TMPDIR, 0700)
                               pinned muse exec (one fixed flag set for every condition)
JSONL ◄──────────── stdout ────
evidence tar ◄──── helper export   (untrusted; extracted safely by the owner)
                   helper cleanup  (kill reviewer processes, remove attempt and
                                    reviewer droppings, verify baseline)
```

Alternatives from the #18 brief were rejected: detect-and-quarantine is
detection, not prevention (kept as a second layer); an outer `sandbox-exec`
profile cannot nest inside Muse's own seatbelt, so it would need
`--disable-sandbox` and lose the tool network restriction; an unmounted disk
image hides too little.

## Allowed and denied surfaces

| Surface | Subject access | Enforced by |
| --- | --- | --- |
| Its checkout: exact head and its ancestors only | read/write | reviewer owns it; bundle of `HEAD`; object-set check |
| Selected project skill (skill conditions only) | read | helper writes bytes received on stdin after a hash check |
| Its attempt scratch and fresh `HOME` | read/write | reviewer-owned, 0700 |
| Pinned Muse binary; system tools in `/usr/bin`, `/bin`, `/opt/homebrew/bin` | execute | H3; owner-independent paths |
| Reviewer Muse credential | read (by Muse and, unavoidably, by its tools) | 0600 reviewer; helper reports only "token present in output: yes/no" |
| Muse service API | network from the Muse process | Muse client |
| Tool network, including GitHub | **denied** | `--sandbox-network restricted` — verified by the probe below |
| Owner home: eval repo, keys, results, traces, Muse data, user skills | **denied** | H2 (owner home 0700) |
| Owner entries in `/private/tmp`, `/private/var/tmp`, `/Users/Shared` | **denied** | H7 closes owner top-level entries before each batch; pruned scan must be empty |
| Earlier attempts and anything they left behind | **absent** | helper reset: kill all reviewer processes, remove reviewer droppings, no cron/at/launchd jobs, home manifest equals baseline |
| Later commits, refs, remotes, alternates, unreferenced objects | **absent** | bundle fetch without remote/reflog/FETCH_HEAD; object-set check |
| Inherited user skills and memory | **absent** | fresh `HOME`; `--no-foreign-personal-context` retained |
| Bundled Muse skills (`bundled:*`) | present, identical in all conditions | recorded per attempt |
| Owner process command lines | **visible** (macOS shows other users' arguments) | operating rule: while the batch lock is held, no owner process carries case, key or grader text in its arguments; preflight canary |
| Owner session services (clipboard, Spotlight, preferences, Docker, loopback listeners, world-connectable sockets) | must be unreachable or empty | helper uses `env -i` and `CFFIXED_USER_HOME`; preflight canaries |
| Server-side state of the Muse account | unknown | residual risk; recorded, not claimed |

## Exact proposed host changes (for owner approval)

Nothing below has been run. `<owner>` is the current user. `<reviewer>` is a
new account with a neutral name and full name, because the subject can see
`whoami` and paths.

- **H1 — Standard, hidden, non-admin account.**
  `sudo sysadminctl -addUser <reviewer> -fullName "<neutral>" -home /Users/<reviewer> -password -`
  then `sudo dscl . -create /Users/<reviewer> IsHidden 1`, and deny scheduled
  jobs: append `<reviewer>` to `/usr/lib/cron/cron.deny` and
  `/usr/lib/cron/at.deny`.
- **H2 — Owner home owner-only.** `chmod 700 /Users/<owner>` (today
  `drwxr-x---+`, group `staff`, which every local account belongs to).
  Reversible with `chmod 750`. Side effects: guest access to `~/Public` (Drop
  Box) stops working, and a macOS permission repair can revert the mode, so
  the preflight re-checks it on every batch. A dedicated primary group for the
  reviewer is optional extra protection, not required (`staff` has no nested
  groups).
- **H3 — Pinned runtime (required after H2).**
  `sudo install -d -o root -g wheel -m 0755 /usr/local/libexec/muse-eval` and
  `sudo install -o root -g wheel -m 0755 <owner-muse-binary-1.3.0-R3233.1> /usr/local/libexec/muse-eval/muse-1.3.0-R3233.1`.
  The owner launcher exports `MUSE_RELEASE_INFO` before starting the binary.
  The helper either sets the same value from a pinned root-owned copy or
  records that it is absent. The preflight runs `--version` as the reviewer.
- **H4 — Reviewer Muse login (owner performs it).** `sudo -iu <reviewer>`, then
  run the pinned binary's `login` and complete the browser flow. Coding workers
  and Claude never handle the credential. Decide which account and
  subscription the subject uses. If it is the owner's account, record that
  server-side account state is shared with other work.
- **H5 — Root-owned subject helper** (a bounded coding task after approval):
  `/usr/local/libexec/muse-eval/muse-review-subject`, 0755 root, written in
  `/bin/bash` or `/usr/bin/python3` (never a Homebrew interpreter). Its
  subcommands are `run`, `export`, `cleanup` and `preflight`. Requirements:
  - rebuild the environment with `env -i`, setting only `HOME`,
    `CFFIXED_USER_HOME`, `XDG_CONFIG_HOME`, `MUSE_AUTH_PATH`, `TMPDIR`,
    `PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin`,
    `MUSE_NO_AUTO_UPDATE=1` and `LANG`;
  - accept only an opaque attempt id, refuse an existing directory, and hold a
    lock so attempts are sequential;
  - receive the bundle and the skill bytes plus SHA-256 on stdin, and
    materialise the checkout with `git init`, then
    `git -c core.logAllRefUpdates=false fetch --no-tags --no-write-fetch-head <bundle> HEAD:refs/heads/review`,
    then a detached checkout, then delete the bundle and the branch ref;
  - accept only the fixed `muse exec` flag set (no `--disable-sandbox`,
    `--yolo` or `--sandbox-network enabled`);
  - **reset before and after every attempt**:
    - `kill -KILL -1` as the reviewer, and `pgrep -U <reviewer>` shows only the
      helper;
    - `crontab -l` is empty, `atq` is empty, and
      `launchctl print user/<uid>` matches the baseline;
    - delete reviewer-owned files under `/private/tmp`, `/private/var/tmp` and
      `/Users/Shared`, and clear the reviewer's `DARWIN_USER_TEMP_DIR` and
      `DARWIN_USER_CACHE_DIR`;
    - the reviewer home manifest (excluding `attempts/` and the credential)
      equals the baseline, and the attempts root is empty;
  - `export` writes a tar of the attempt's Muse session records and `tmp/` to
    stdout, and reports whether the credential token appears in any exported
    file (yes/no only);
  - `preflight` runs a fixed list of checks, not arbitrary commands.
- **H6 — One sudoers drop-in.** `/etc/sudoers.d/muse-eval`, 0440 root,
  validated with `sudo visudo -cf` before installation:
  `<owner> ALL=(<reviewer>) NOPASSWD: /usr/local/libexec/muse-eval/muse-review-subject`
- **H7 — Close shared temp before every batch** (changes modes of the owner's
  own leftovers). Run
  `find /private/tmp /private/var/tmp /Users/Shared -xdev -mindepth 1 -maxdepth 1 -user <owner> -type d -perm -o+x -exec chmod o-rwx {} +`
  and the same with `-type f -perm -o+r -exec chmod o-r {} +`. Then the pruned
  scan
  `find /private/tmp /private/var/tmp /Users/Shared -xdev -user <owner> -type d ! -perm -001 -prune -o -user <owner> -perm -004 -print`
  must print nothing, before and after each attempt. Operating rule: no other
  owner agent work that writes to `/tmp` while the batch lock is held.
  (Deleting the stale clones instead is simpler, but that is your call.)
- **No firewall or network setting changes.**

Rollback: remove the sudoers drop-in, the helper directory, the cron/at deny
entries and the account (`sudo sysadminctl -deleteUser <reviewer>`), then
restore `chmod 750` on the owner home.

## Harness interface (through the single #25 writer, after approval)

- #25 adds `muse_sessions_root` / `MUSE_EVAL_SESSIONS_ROOT` so the trusted
  provider reads session records from an extracted evidence copy.
- A later bounded change adds a `subject_launcher` option. The provider
  prepares and verifies the exact-head clone as today. It then runs
  `git bundle create <f> HEAD` on the detached clone, checks that
  `git bundle list-heads <f>` is exactly `<head> HEAD`, and calls
  `helper run`. It then calls `helper export` **before** `helper cleanup`.
  Any helper failure is an attempt error, never an approval.
- The exported tar is untrusted. Extract it with Python
  `tarfile.extractall(filter="data")` into a fresh 0700 directory, rejecting
  links and devices. Cross-check model, skill observation and usage against the
  stdout the owner captured.
- **No-skill condition:** `_prepare_workspace` currently writes a same-name
  placeholder skill for `variant: none`. Under a fresh `HOME` that placeholder
  is no longer needed to shadow a user skill. Whether #19 removes it (a true
  no-skill condition) is an explicit #19 configuration decision, recorded
  before the run.
- Identity, completion and accounting stay as #25 defines them.

## Synthetic preflight (no inference; `helper preflight`, identical sudo path and environment)

Canaries are synthetic. Every check prints only `DENIED`/`READABLE`,
`ABSENT`/`PRESENT` or a count.

1. **Owner files:**
   - canaries in the owner home;
   - the eval repository, answer-key, results and traces paths (open attempts
     only; nothing is printed);
   - the owner Muse sessions, session index, memory and user skills;
   - a sibling clone;
   - owner entries in `/private/tmp`.

   Each is tried by absolute path, by `..` from the checkout, through a
   symlink planted in the checkout, and with `ls` on the parent. All must be
   denied. The owner home mode must be `0700`.
2. **Git:**
   - `git cat-file --batch-all-objects --batch-check='%(objectname)'` equals the
     object list of `git rev-list --objects HEAD`;
   - a known later commit is absent;
   - no remote, no `objects/info/alternates`, no reflog entries and no
     `FETCH_HEAD`;
   - no owner path in `git config --list --show-origin`.
3. **Attempt reset:**
   - a canary process, a file in `/private/tmp` and a file in the reviewer home
     planted by a previous preflight attempt are all gone after cleanup;
   - `crontab`/`at` are refused;
   - the fresh `HOME` lists only `bundled:*` skills and has no memory content.
4. **Shared temp:** the H7 scan is empty.
5. **Owner session surfaces:**
   - `pbpaste` does not return an owner clipboard canary;
   - `mdfind -name <canary>` is empty;
   - `docker version` fails;
   - list world-connectable Unix sockets and loopback listeners (connection
     attempts from inside Muse are covered by the probe);
   - an owner canary process argument is not visible in `ps -axww` while the
     rule holds. If it is visible, the operating rule is the only control.
     That is recorded as a limit, not a pass.
6. **Legitimate work still possible:**
   - `git diff base..head --stat` and `git show head:<file>`;
   - each case's real focused test command (after checking where `uv` and
     other tool caches resolve);
   - `node --version`;
   - writes succeed in `repo/` and `tmp/`;
   - resolved tool paths are recorded.
7. **Equal conditions:** the same binary hash, flag set, environment keys,
   bundled skill list and announced tool list for production, candidate and
   no-skill runs. The skill file is present only where intended. #19
   alternates the order of conditions.

What checks 1–4 establish: kernel permissions apply to every process running as
the reviewer. That includes Muse's own in-process file tools, which get exactly
the reviewer's access. An unsandboxed preflight run through the identical
helper path therefore bounds what Muse can read. They do **not** establish
network denial, owner-session service isolation beyond check 5, or server-side
account state.

**Network boundary: one live probe, requiring its own approval (one Muse call,
zero graders).** It runs against a throwaway synthetic repository with no
private data, at low effort with `--max-model-steps 4`. The prompt asks for
exactly two tool calls:

- a `read_file` of an owner canary path through Muse's own file tool;
- `bash probe.sh`, a committed deterministic script that reports:
  - proxy variables;
  - HTTPS to `github.com` and `pypi.org`;
  - raw TCP to `1.1.1.1:443`, with DNS reported separately;
  - TCP to an owner loopback canary listener;
  - a connection to an owner canary Unix socket;
  - writes to `$HOME` and `/private/tmp`.

Results are judged from `tool.result` events, not from the model's summary. The
announced tool list must contain no web, memory, history or session-search
tool. A positive control runs the same `probe.sh` through `helper preflight`
without Muse, and its network steps must succeed, so any denial in the probe is
attributable to Muse's sandbox. Expected: network, loopback and socket attempts
denied; the canary read denied; the run completes.

## What remains after approval

1. Owner applies H1–H4 and H7, and reviews the exact H6 file.
2. Bounded helper task (H5) plus the `subject_launcher` integration, with
   native worker review and independent Opus acceptance.
3. Synthetic preflight, then the single approved network probe.
4. Sanitized verification record; raw outputs stay private.
5. Independent acceptance; only then update #5's #24 checkbox.
