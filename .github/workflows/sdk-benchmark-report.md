# SDK benchmark reporting

`sdk-benchmark-report.yml` publishes informational performance reports from an SDK repository's completed PR benchmark runs. The SDK owns execution and measurements. `PostHog/posthog-sdk-test-harness` owns the result schema, comparison, renderer, artifact verification, and comment updater; this workflow provides the trusted job boundary and permissions.

## Caller

Add a **separate** workflow in the SDK repository, pinning this reusable workflow to a reviewed full commit SHA:

```yaml
name: Node SDK benchmark report
on:
  workflow_run:
    workflows: [Node SDK benchmarks]
    types: [completed]
permissions: {}
jobs:
  report:
    permissions:
      contents: read
      actions: read
      pull-requests: write
    uses: PostHog/.github/.github/workflows/sdk-benchmark-report.yml@<full-commit-sha>
    with:
      workflow-path: .github/workflows/node-benchmarks.yml
      artifact-name: node-benchmark-results
      report-key: posthog-node
      sdk: posthog-node
```

Use repository-owned constants for all four inputs. `report-key` is a stable, SDK-specific comment namespace, not a PR number. `sdk` must match both result documents. The producer artifact contains `base.json`, `head.json`, and `revisions.json` (`target`, `base`, and `head` full commit SHAs).

The producing `pull_request` workflow remains read-only and can separately invoke the harness action in render-only mode for a job summary. It must not receive the publisher's writable token or service credentials. No manually supplied PR number or artifact-controlled command is accepted by the publisher.

## Trust and ordering

- Only completed `workflow_run` events can invoke the publishing job. No caller checkout occurs; reporting code is pinned to a harness commit in this workflow.
- The action verifies the workflow identity, source run and attempt, repository, PR association, head SHA, and merge-base against GitHub metadata before consuming results. Fork associations must be unambiguous.
- Artifacts are bounded, validated data, never executable code. Report text is escaped; result metadata cannot choose the comment's target or URLs.
- The action only updates its exact marker on the expected GitHub Actions bot's comment. It rechecks the current PR head and run attempt and rejects older reports before publication.
- A repository/report-key concurrency group serializes publishers, including fork runs whose event does not identify a PR. Cancellation is disabled so a late older event cannot cancel a newer report. `queue: max` preserves up to 100 pending publishers instead of replacing another PR's pending report. GitHub cancels additional arrivals when that queue is full; the source-run guard still prevents stale writes.
- Egress auditing runs first. Audit mode observes traffic; it does not prevent exfiltration or replace least privilege.
- Rendered Markdown and comparison JSON are retained for 14 days, including when publishing cannot complete. The source SDK workflow retains the raw measurement artifacts.

## Rollout and validation

The caller's `workflow_run` workflow must be on its default branch before it can service completed benchmark runs. Adding it to an unmerged stacked PR does not enable privileged comment publication. Land the reporting components and caller configuration through normal review; do not work around activation by executing PR code with a writable token.

Unit tests for schema, comparisons, hostile artifacts, PR identity, forks, and stale comments live with the harness action. This repository's structural tests guard the workflow's pins, event restrictions, permissions, concurrency, token scope, and lack of caller checkout:

```sh
node --test .github/scripts/sdk-benchmark-workflow.test.mjs
```

Also run Actionlint on both `sdk-benchmark-report.yml` and `sdk-benchmark-report-tests.yml`. GitHub [supports `queue: max`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idconcurrency), but Actionlint 1.7.12 does not yet recognize that key; ignore only its `unexpected key "queue" for "concurrency" section` diagnostic until support lands. The read-only test job also uses this setting so GitHub validates the syntax before publisher rollout. Performance differences are informational, not merge gates; invalid inputs and operational errors remain visible. Linux runner calibration is separate from correctness of report generation.
