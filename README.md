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

Currently ships as a Claude Code plugin. A Codex CLI port is planned: Codex has
the same `PreCompact`, `SessionStart` and `additionalContext` surface, and its
rollout files even report the exact context size and window, so the estimate
below would not be needed there.

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
