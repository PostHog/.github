# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This file captures only what *isn't* obvious from reading the files themselves — quirks, conventions, and gotchas. Don't restate the file tree or what each file plainly contains.

## What this repo is

`PostHog/.github` is the org-level special repository: reusable workflows + composite actions referenced by every other repo in the org, org-wide community health file defaults, and the org profile (`profile/README.md`).

There is no build step and no app. Changes are config (YAML workflows, semgrep rules) plus a couple of Node scripts, but the blast radius is the whole org — hence `.github/` and `workflows/` are CODEOWNED by `@PostHog/team-security` and most changes get their review.

## Gotchas

- **The doubled path.** Reusable workflows here are referenced as `PostHog/.github/.github/workflows/<name>.yml@main` — the `.github/.github/` is correct, not a typo.
- **`workflow_call` preserves the original event.** When a workflow here is invoked via `workflow_call` from another repo, `github.event_name` keeps the *original* event (e.g. `pull_request`), not `workflow_call`. This is an undocumented special case of the `.github` repo; several workflows branch on it. Read the comments in `flags-project-board.yml` before "fixing" any event-name check.
- **`flags-boards.json` is loaded at runtime**, not baked into the workflow SHA — so editing the team→board map doesn't require callers to re-pin.
- **Registry rules are pinned as snapshots.** Scan workflows must never use live `--config p/...` / `--config r/...` registry configs — they resolve at scan time, so a registry-side rule change breaks CI org-wide with no code change. Instead, packs are vendored under `.semgrep/registry/` (generated files — don't hand-edit) and workflows point at those. The `semgrep-registry-update` workflow re-fetches on a schedule, dry-runs added/changed rules against critical repos (the `SEMGREP_REGISTRY_DRY_RUN_REPOS` variable, default `PostHog/posthog`), notifies Slack, and opens a snapshot-bump PR; *merging that PR is the moment new rules start being enforced*. To add a pack, add it to `.semgrep/registry/sources.json` and run `python3 .github/scripts/semgrep_registry.py sync`. Like `flags-boards.json`, snapshots load from `main` at runtime, so merging applies org-wide without re-pinning.

## Testing

No general test suite. The one locally runnable thing is the semgrep rule tests:

```bash
semgrep --test .semgrep/rules/
```

(Scoped to `rules/` — `.semgrep/registry/` holds vendored registry snapshots with no test fixtures.)

Each rule has a paired `.test.yaml` fixture — update it when you touch a rule. Workflows themselves can't be unit-tested; reusable workflows expose a `script-ref` / ref input (default `main`) so you can point a caller at a branch of this repo while iterating against a real PR.

## Conventions (enforced, match them)

- **Pin every action to a full commit SHA** with a trailing `# vX.Y.Z` comment — third-party *and* first-party `PostHog/*`.
- **No shell injection.** Never interpolate `${{ steps.*.outputs.* }}` or other untrusted values into a `run:`/`script:` block. Route through an `env:` var and reference `"$VAR"` double-quoted. The custom `github-actions-shell-injection` rule enforces this.
- **`pull_request_target` is effectively banned** (`github-actions-pull-request-target` rule errors on it). If truly required: don't check out the PR head, scope `permissions:` minimally, and add a justified `# nosemgrep:` line reviewed by security.
- Set explicit least-privilege `permissions:` on every workflow/job.
- **Declare `timeout-minutes` on every job.** The GitHub default is 360 minutes, so a hung job holds a runner for six hours before anything kills it. Set a generous multiple of the job's normal runtime, not a tight bound.

## Keeping this file current

When you change how this repo works in a way that contradicts or outdates the above — a new gotcha, a changed convention, a removed workflow that's referenced here — update this file in the same change. Keep it to non-obvious, file-listing-independent guidance; if something becomes plainly visible from the files, drop it from here rather than duplicating it.
