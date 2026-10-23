# Release approval guard

Fails a release when someone who approved its environment authored or queued a PR in the push that triggered the run. In `report` mode it only names who is excluded, so the approval request can say so.

This restores what GitHub's "Prevent self-review" environment setting did before merge queues. That setting excludes the run's triggering actor. When a person merged a PR, the actor was that person, so they couldn't approve the release. With a merge queue (GitHub or Trunk), the actor is a bot, so nobody is excluded.

## Usage

Add the step to the job that uses the approval environment, before the job uses its secrets:

```yaml
jobs:
    version-bump:
        environment: 'NPM Release'
        permissions:
            contents: read
            actions: read # approvals of this run, `enforce` mode only
            pull-requests: read # PRs in the push
        steps:
            - uses: PostHog/.github/.github/actions/release-approval-guard@<sha>
              with:
                  environment: 'NPM Release'
```

It needs no checkout. It needs Node, which GitHub-hosted runners include.

## Inputs

| Input          | Default          | Purpose                                                                                      |
| -------------- | ---------------- | -------------------------------------------------------------------------------------------- |
| `environment`  |                  | Environment whose approval gates the release. Required in `enforce` mode.                    |
| `mode`         | `enforce`        | `enforce` fails the job on a blocked approval. `report` never fails and only writes outputs. |
| `github-token` | `github.token`   | Token for the GitHub API.                                                                    |

## Report mode

Use `mode: report` before the approval exists, for example in the job that posts the approval request to Slack:

```yaml
- id: guard
  uses: PostHog/.github/.github/actions/release-approval-guard@<sha>
  with:
      mode: report
```

It lists the PRs in the push and who can't approve them. It never calls the approvals API, so it needs only `contents: read` and `pull-requests: read`, not `actions: read`. On any error it logs a warning and writes empty outputs. Both modes write these outputs, empty when the event isn't a `push`:

| Output           | Example                                                         |
| ---------------- | --------------------------------------------------------------- |
| `blocked_logins` | `alice,bob`                                                     |
| `blocked_mrkdwn` | `<https://github.com/alice\|alice> (queued #5273), <https://github.com/bob\|bob> (author of #5270)` |
| `violations_mrkdwn` | `<https://github.com/alice\|alice> (queued #5273)` (approvers who violated the rule; empty in `report` mode and when none did) |
| `prs_mrkdwn`     | `<https://github.com/PostHog/posthog-js/pull/5273\|#5273> fix: flush queue` (one PR per line) |
| `prs`            | `5270,5273`                                                     |

Titles are escaped for Slack (`&`, `<`, `>`). `prs_mrkdwn` is capped to fit a Slack field. Bots such as the merge queue are left out of the blocked list.

## Behavior

- It finds every PR in the push that triggered the run (`before...after`). A merge queue can merge several PRs in one push.
- It blocks approval by each PR's author and by whoever queued it. With GitHub's merge queue, `merged_by` is the person who added the PR to the queue. With Trunk, it's `trunk-io[bot]`, so only the author is checked.
- On events other than `push`, it does nothing, because GitHub's own check already excludes whoever triggered the run.
- It fails closed if the run has no approval for the environment, if a commit in the push isn't part of a merged PR, or if the push range can't be listed in full.
- After a block, re-run the failed jobs. That requests a new approval, and a different approver must give it. The approvals endpoint then lists only the new approval, so the guard passes on the next attempt.
- Like the setting it replaces, it only covers the PRs in the triggering push. If a release also publishes earlier unreleased changes, their authors aren't excluded.

Run the tests with `node --test .github/actions/release-approval-guard/guard.test.mjs`.
