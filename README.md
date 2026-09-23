# Context Budget

Claude Code compacts when it runs out of room. That point is rarely a good one:
it lands wherever the context happens to fill up, often mid-step, and the
summary that survives is written by a summarizer that does not know what
mattered.

This plugin moves the decision to the model. It watches the context size, nudges
in stages, holds the automatic compaction back until the model has written a
handoff file, and feeds that handoff back in afterwards.

## Stages

| Stage | Default | What happens |
| --- | --- | --- |
| 1 | 100k tokens | Hint: compact at the next clean break. No pressure. |
| Gate | ~110k tokens | Claude Code's auto-compact fires; `PreCompact` blocks it while no handoff exists. |
| 2 | 150k tokens | Sharper hint, repeated every further 15k tokens while the gate holds. |
| 3 | 200k tokens | Hard release: compaction goes through, handoff or not. |
| Carryover | after each compact | `SessionStart` re-injects the handoff, marked as taking precedence over the summary. |

The model compacts by writing its handoff: the moment the file exists, the gate
opens on the next attempt.

The repository also ships a Codex profile for Codex in the ChatGPT desktop app and Codex CLI.
Its hook behavior and setup differ from Claude Code; see below.

## Install

```bash
claude plugin marketplace add david-plugge/context-budget
claude plugin install context-budget@context-budget
```

Then set the auto-compact window once — **the plugin cannot do this for you**,
Claude Code does not let plugins ship `settings.json` values:

```
/autocompact 110k
```

Without it, auto-compact fires only near the model's own limit, the gate is
never reached, and the staging never happens. The plugin checks for the setting
at session start and says so if it is missing.

## Tuning

Thresholds come from environment variables, defaults in brackets:

- `CONTEXT_BUDGET_SOFT` [100000]
- `CONTEXT_BUDGET_FIRM` [150000]
- `CONTEXT_BUDGET_HARD` [200000]

Keep the auto-compact window slightly above `SOFT` so the hint arrives before
the gate starts holding compactions back.

**On a model with a 200k context window, lower `CONTEXT_BUDGET_HARD` to around
160k.** A session that cannot compact dies with `Prompt is too long`, and the
hard release has to come before that.

## How it measures

Hooks are not given the context size, so the script reads it from the session
transcript: the newest `usage` record plus a rough estimate of everything
written since (4 characters per token). The tail matters — without it the
reading lagged by ~30k tokens on a batch of large reads. It stays an estimate.

## Files

- Handoff: `<tmp>/claude-context-budget/<session-id>.handoff.md`, renamed to
  `.consumed` once re-injected. Not under `~/.claude`, which Claude Code
  protects from writes.
- State: `~/.claude/context-budget/<session-id>.json`.

## Verified behaviour

Established by testing against Claude Code 2.1.278, not from documentation:

- `PreCompact` exit code 2 blocks compaction, and Claude Code retries on each
  following request. This is what makes the gate possible.
- Blocking forever kills the session, hence the hard release and the cap on
  blocked attempts.
- The blocking hook's stderr never reaches the model, so all nudging goes
  through `PostToolUse`/`Stop` `additionalContext`.
- `PreCompact` cannot steer the summary: neither `customInstructions` nor
  `additionalContext` survive it. The handoff file is the only carryover that
  works.

## Codex in the ChatGPT desktop app

Add the GitHub marketplace once:

```bash
codex plugin marketplace add david-plugge/context-budget
```

The repo marketplace at `.agents/plugins/marketplace.json` exposes the Codex
profile of this plugin. Restart the desktop app, open the Plugins Directory,
select **Context Budget**, install it, and review/trust its hooks. A new task is
needed for newly installed hooks. Run `codex plugin marketplace upgrade
context-budget` to fetch later repository updates, then restart the app. The
Claude manifest and hooks remain separate.

Set an early auto-compact threshold in `~/.codex/config.toml`, then restart the
app. For the default 258k window, use:

```toml
model_auto_compact_token_limit = 110000
```

This is a host setting, not a plugin setting. Adjust it to sit slightly above
the soft hint for your model. Codex's rollout usage records contain a measured
context size and model window. By default the Codex hints use the lower of
100k/150k/200k and 40%/60%/80% of that window; the hard limit is a safety
release. `CONTEXT_BUDGET_SOFT`, `CONTEXT_BUDGET_FIRM`, and
`CONTEXT_BUDGET_HARD` override these values with exact token counts.

The Codex hook writes its small state file in `PLUGIN_DATA` and asks the agent
to write a handoff under the system temp directory. `PreCompact` defers an
automatic compact until the handoff exists, with a four-attempt cap and a hard
token release. `SessionStart(compact)` re-injects the handoff into the next model
request. Manual compaction is unaffected. Codex's `PreCompact` cancellation may
end the current turn, so an agent that ignores earlier hints can still be
interrupted. The hook cannot itself issue the app's `/compact` command; the
agent chooses when to prepare the handoff, and Codex compacts on its next
automatic trigger. The rollout JSONL format is not a stable API; if Codex changes
it, the hook fails open and allows compaction.

The Codex adapter has passed synthetic hook tests and a live Codex CLI
`Stop` → `/compact` → `SessionStart(compact)` test with Luna. In a later
GPT-6 Luna CLI test, the globally registered hooks captured the session's
visible messages, produced a threshold hint, and blocked an automatic compact
without a handoff. That run ended when the early test threshold was reached;
the automatic compact and subsequent restore have not yet been observed in one
continuing CLI run. The desktop app's plugin hook discovery remains unverified.
Check `/hooks` for the plugin handlers before relying on the installed package.
Plugin hooks require explicit trust review when they are discovered.

## Native memory and conversation history

Native memory and this plugin serve different scopes. Codex's experimental
`features.memories` and Claude Code's auto memory can retain useful facts for
later sessions. This plugin does not write those native memories or treat them
as a copy of the conversation. Enable native memory in each host if desired;
for Codex, set `[features] memories = true` in `~/.codex/config.toml` and start
a new task. Claude Code exposes auto memory through `/memory`.

The plugin also archives the **visible user and assistant text** of each
session on `Stop` and `PreCompact`. After a compact, `SessionStart(compact)`
provides a lookup command so the agent can search older messages on demand.
The archive retains full message text without the 4,000-character truncation
used by the optional excerpt prototype. It does not put the entire archive
back into the model's finite context. Tool outputs, hidden reasoning, system
and developer messages, attachments, and Claude sidechains are excluded.
Transcript formats are host internals and can change.

For example, in a Codex task (replace the session ID):

```bash
python3 plugins/context-budget/memory/history.py --host codex --session-id SESSION_ID search --query 'earlier decision'
python3 plugins/context-budget/memory/history.py --host codex --session-id SESSION_ID show --id codex:MESSAGE_ID --offset 0 --limit-chars 4000
```

Use `--host claude` for Claude Code. Search is case-insensitive; `show` can
page through a long message using `--offset`. The per-session JSON archive is
stored under `PLUGIN_DATA/history` or `CLAUDE_PLUGIN_DATA/history`, falling back
to `~/.codex/context-budget/history` or `~/.claude/context-budget/history`.
The directory is user-only (mode `0700`) and files are mode `0600` on POSIX.
Delete that history directory to erase these local archives. No network or
model call is used to capture or search it.

### Measure archive cost and use

Each archive also gets a private `<session-id>.stats.jsonl` file. It records
capture time, transcript and archive bytes scanned, bytes rewritten, new
message count, context injections, search hit counts, and text returned by
`show`. It does **not** record queries, message IDs, or conversation text.
Metrics are best-effort: a write failure never blocks a hook or lookup.
Agent search and show commands emit a content-free metric marker. The
`PostToolUse` hook records it even when the agent's shell is read-only;
duplicate records from writable shells count only once in the report.

To aggregate all local sessions for one host:

```bash
python3 plugins/context-budget/memory/history.py --host codex report
python3 plugins/context-budget/memory/history.py --host claude report
```

`sessions_searched / sessions_archived` estimates how often the archive is
used. `sessions_shown` and `lookup_chars_returned` show whether searches lead
to actual reading. `capture_elapsed_ms_p95`, `capture_transcript_bytes_scanned`,
and `capture_archive_bytes_written` expose the cost of rescanning transcripts
and rewriting the per-session archive. These byte counts describe local I/O,
not model tokens or time saved. The current capture implementation rescans the
whole transcript and rewrites the archive whenever new messages arrive, so
watch these values as sessions grow. Stats collection starts with the updated
plugin; earlier sessions have no retrospective metrics.

## Experimental transcript memory

The optional `CONTEXT_BUDGET_MEMORY=1` prototype works with both host profiles.
On `Stop` and `PreCompact`, it reads visible user and assistant text from the
session transcript, skips Codex's startup messages before the first turn, and
keeps the latest 64 excerpts in a local, per-session file.
`SessionStart(compact)` adds at most 2,000 characters of that memory to
the handoff context. Replaying the same transcript does not duplicate entries.
Memory capture is local and makes no model or network calls.

The file lives under `PLUGIN_DATA/observational-memory` for Codex or
`CLAUDE_PLUGIN_DATA/observational-memory` for Claude, falling back to
`~/.codex/context-budget/observational-memory` or
`~/.claude/context-budget/observational-memory` respectively. It contains raw
conversation excerpts, so remove that directory to erase the prototype's
memory. The directory and files are restricted to the local user on POSIX
systems.

This exercises capture, deduplication, persistence, and reinjection. It does
not yet create model-written observations, topic files, or a replacement for
either host's compaction summary. Transcript formats are host internals and
may change. The flag is disabled by default; set it in the host process
environment before starting a new task to test it.
