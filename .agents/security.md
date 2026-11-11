# Security guidelines for agents

PostHog/.github is the organization's special repository. It holds reusable
workflows (`on: workflow_call`), composite actions in `.github/actions/`,
custom Semgrep rules in `.semgrep/rules/`, the org-wide Semgrep workflows, and
community health files. Other PostHog repositories run this code in their CI,
often with write tokens and secrets. Many callers are public SDK repositories
that accept fork PRs.

Read this as your checklist and your calibration. It replaces the Django
fallback checklist in your prompt (`permission_classes`, HogQL, `team_id`),
which does not apply here. Your threat model and the generic vulnerability
classes still apply. Assess every workflow and action change yourself. The
Semgrep checks are a backstop, not the review.

## How to read a diff here

- A reusable workflow runs in the caller's context. `github.repository`,
  `github.event`, `github.token` and the permission ceiling are the caller's.
  `workflow_call` keeps the caller's event name, such as `pull_request`
  (`AGENTS.md`). Judge each `if:` gate with that fact.
- A change reaches a caller that pins a SHA at its next SHA bump, and a caller
  on `@main` at merge.
- These paths read `main` at run time, so a merge reaches every caller at once:
  `semgrep.yml`, `semgrep-package-managers.yml`, `.semgrep/rules/`,
  `report-semgrep-results.py`, `check-changeset-coverage.mjs` (at the default
  `script-ref`) and `.github/flags-boards.json`.
- In each finding, name the path, when it reaches callers (at merge or at the
  next bump), and the token or secret in reach.

## Org-wide Semgrep checks

**The bug class:** a change makes a required check pass on code that it must
fail, or skip code that it must scan, in every repository at once.

Findings to report:

- A rule in `.semgrep/rules/` that is deleted, narrowed (`paths`, `pattern-not`,
  `pattern-inside`, `metavariable-regex`) or lowered from `severity: ERROR`, or a
  `.test.yaml` edit that deletes a `ruleid:` case or changes it to `ok:`. Name an
  input that the old rule catches and the new rule passes.
- A new `--exclude-rule` or `--exclude`, or a removed `--config`, `--error` or
  `--strict`, in `semgrep.yml`, `semgrep-package-managers.yml` or `ci-security.yml`.
- A new `ref:` or `repository:` on the `Checkout .github repo` step, or a
  changed `--config` or script path.
- A change to the `Check whether Semgrep must scan` step.
- A change to `report-semgrep-results.py`, `command_escape` or the
  `Report Semgrep results` step.
- A rename or move of `semgrep.yml` or `semgrep-package-managers.yml`.

## Script injection

**The bug class:** an attacker-controlled value is expanded into a script body
before the interpreter parses it. The step then runs attacker code with the
caller's token.

Untrusted values: `github.event.pull_request.*` (title, body, head ref),
`github.head_ref`, `github.event.workflow_run.head_branch`, caller `inputs.*`,
`steps.*.outputs.*` that carry API data, and artifact content. The safe pattern
is an `env:` entry and a quoted `"$VAR"`, as in `detect-markdown-only.yml`.
Findings to report:

- A new untrusted `${{ }}` in a `run:` body, a `script:` body or a JSON `with:`
  value. Also a value moved from `env:` into one of those.
- A new `execSync`, `bash -c` or `eval` call, or a changed command string in one.
- A new multi-line value written to `$GITHUB_OUTPUT` or `$GITHUB_ENV` with a
  fixed delimiter. `writeOutputs` in `guard.mjs` shows the safe form.

## Tokens, permissions and secrets

**The bug class:** a job gets a wider token or a secret that it does not need,
or a token reaches code that a PR author controls.

| Workflow: job                                     | Token or secret                              | Caller code in the job      |
| ------------------------------------------------- | -------------------------------------------- | --------------------------- |
| `flags-project-board.yml`: `add-to-project-board` | project-board app token                      | none                        |
| `notify-approval-needed.yml`: `notify`            | Slack bot token, PostHog project key         | caller checkout, `git` only |
| `sdk-benchmark-report.yml`: `report`              | `pull-requests: write`, `actions: read`      | none, artifacts only        |

Findings to report:

- A new or wider scope (`contents`, `id-token`, `actions`, `packages` or
  `pull-requests` write, or `write-all`), or a job that loses its `permissions:`.
- A new `secrets:` entry on a `workflow_call`, or a doc that says `secrets: inherit`.
- Removal or weakening of the filter
  `github.event.pull_request.head.repo.full_name == github.repository` in
  `changeset-hygiene.yml` or `flags-project-board.yml`.
- A new token or secret in a step that runs after a checkout of caller PR code.
  Name the tool that reads the checkout.
- A new use of the `flags-project-board.yml` app token beyond project writes and
  team reads.
- A change to `GitHub._request` or `GitHub.download` (`benchmark-report`,
  `github.py`) that sends the token to a new origin or on the artifact redirect.

## Triggers and untrusted artifacts

**The bug class:** a privileged trigger gives a write token to input that an
outside contributor controls, or a fork PR's artifact reaches a privileged job
as code, as a write target, or as trusted text.

`sdk-benchmark-report.yml` is the one `workflow_run` consumer. It checks out
nothing, and `sdk-benchmark-workflow.test.mjs` asserts that. Its
`benchmark-report` action verifies the source in `Publisher.verified_run`,
`Publisher.associate` and `Publisher.results` before it reads results.
`read_archive` bounds the archive, `render.safe_text` escapes artifact text, and
`Publisher.upsert` checks the comment marker, run order and PR head.
Findings to report:

- A new `pull_request_target`, `issue_comment`, `workflow_run` or
  `repository_dispatch` trigger, or a new `nosemgrep` comment.
- A new checkout of `github.event.pull_request.head.*` or `github.event.workflow_run.head_*`,
  or a new `actions/cache` or `actions/download-artifact`, in a job with a write
  scope or a secret.
- In `sdk-benchmark-report.yml`: a change to the `if:` gate on `report`, or a new
  `actions/checkout`, `actions/cache`, `run:`, `script:`, `secrets: inherit` or
  reporter ref input. Also a weaker assertion in `sdk-benchmark-workflow.test.mjs`.
- A loosened check in the functions above, or artifact content used as a path,
  a command, a URL, a PR number or a comment target. Name the artifact that passes.
- Removal of `python -I`, `--no-config` or `--frozen` in `benchmark-report/action.yml`.
- A change to an event-name check in `flags-project-board.yml` or `detect-markdown-only.yml`.
- A `runs-on` change that moves a job that PRs reach onto a self-hosted or
  non-ephemeral runner.

## Pinning and self-references

**The bug class:** a mutable reference lets someone outside the review change
the code that runs with a caller's token. Every remote `uses:` here pins a full
SHA, and every image pins a `@sha256:` digest. Findings to report:

- A `uses:` on a tag, a branch or a short SHA, or an image with no `@sha256:`.
  Also a new action owner: name the token that the step can read.
- A SHA bump with no matching change to its version comment.
- A self-reference (`PostHog/.github/.github/actions/...@<sha>`) moved to a SHA
  that is not on `main`.
- A tool version off its pin (`setup-uv` `version`, `python-version`,
  `node-version`, the Semgrep image digest), or a download that runs with no checksum.
- An `AGENTS.md` change that weakens a stated convention: SHA pinning, image
  digests, `env:` routing, the `pull_request_target` ban or explicit `permissions:`.

## Release approval guard

**The bug class:** a person approves the release of their own PR.
`release-approval-guard` in `enforce` mode fails the release job when an
environment approver authored or queued a PR in the push. Findings to report:

- A change to `approversFor`, `pushedPrs`, `blockedApprovers` or
  `checkReleaseApproval` in `guard.mjs` that drops an approver, a PR or a reason,
  or that changes the case-insensitive login match.
- A change that turns an error into a pass in `enforce` mode (`runEnforce`, the
  `main().catch` handler), or a deleted fail-closed case in `guard.test.mjs`.
- A change to `escapeMrkdwn`, `prsMrkdwn` or `blockedMrkdwn` that lets PR text
  add a link or a mention. `notify-approval-needed.yml` posts that text next to
  the approval button.

## Calibration — what is NOT a finding

- Text in `profile/`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `README.md` and the
  `sdk-benchmark-*.md` docs. A new doc example that tells callers to pin
  `@main`, to use `secrets: inherit` or to grant a wider scope is a finding.
- New benchmark fixtures, new cases in `.semgrep/rules/*.test.yaml`, and
  changes to `compare.py` or `render.py` that keep `safe_text` on artifact text.
- Edits to the team-to-project map in `.github/flags-boards.json`.
- `timeout-minutes`, a Semgrep image digest bump with its tag, and the
  `depot-ubuntu-latest` or `ubuntu-latest` choice in the Semgrep workflows.
- `pull-requests: write` on `changeset-hygiene.yml` and `sdk-benchmark-report.yml`.
  Both post PR comments.
- `${{ }}` of `github.repository`, `github.run_id`, `github.action_path` or
  `runner.temp` in a script body.
- Generic hardening with no change in the diff.
- Anything you cannot tie to a concrete wrong outcome: a token in reach of
  attacker input, a check that stops failing, or a caller that runs changed code.
