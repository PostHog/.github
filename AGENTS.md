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

## Testing

No general test suite. The one locally runnable thing is the semgrep rule tests:

```bash
semgrep --test .semgrep/
```

Each rule has a paired `.test.yaml` fixture — update it when you touch a rule. Workflows themselves can't be unit-tested; reusable workflows expose a `script-ref` / ref input (default `main`) so you can point a caller at a branch of this repo while iterating against a real PR.

## Conventions (enforced, match them)

- **Pin every action to a full commit SHA** with a trailing `# vX.Y.Z` comment — third-party *and* first-party `PostHog/*`.
- **No shell injection.** Never interpolate `${{ steps.*.outputs.* }}` or other untrusted values into a `run:`/`script:` block. Route through an `env:` var and reference `"$VAR"` double-quoted. The custom `github-actions-shell-injection` rule enforces this.
- **`pull_request_target` is effectively banned** (`github-actions-pull-request-target` rule errors on it). If truly required: don't check out the PR head, scope `permissions:` minimally, and add a justified `# nosemgrep:` line reviewed by security.
- Set explicit least-privilege `permissions:` on every workflow/job.

## Keeping this file current

When you change how this repo works in a way that contradicts or outdates the above — a new gotcha, a changed convention, a removed workflow that's referenced here — update this file in the same change. Keep it to non-obvious, file-listing-independent guidance; if something becomes plainly visible from the files, drop it from here rather than duplicating it.
