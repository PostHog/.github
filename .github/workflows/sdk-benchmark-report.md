# SDK benchmark reporting

`sdk-benchmark-report.yml` publishes informational performance reports from an SDK repository's completed PR benchmark runs. The SDK owns execution and measurements. The reporting action in this repository owns the result schema, comparison, renderer, artifact verification, and comment updater; this workflow provides the trusted job boundary and permissions.

The [SDK benchmark specification](sdk-benchmark-specification.md) defines the shared
Node, Python and .NET workload inventory, controlled fixtures, measurement boundaries
and producer conformance checks. It complements the result schema below; runtime-native
counters and lifecycle differences remain explicit.

## Reporting action

Use `PostHog/.github/.github/actions/benchmark-report@<full-commit-sha>` from an SDK workflow. Pin both the action and reusable workflow to reviewed, published commits of this repository.

| Input | Default | Purpose |
| --- | --- | --- |
| `mode` | `render` | Offline `render` or `collect`, or trusted `workflow_run` `publish` |
| `base-results`, `head-results` | empty | Paired result paths for legacy rendering |
| `bundle-results` | empty | Saved bundle path for rendering, instead of base/head paths |
| `shard-manifest`, `shard-directory` | empty | Required manifest and download directory paths for collection |
| `source-status` | empty | Measurement step or matrix outcome for rendering/collection; publication uses the verified run conclusion |
| `output-directory` | `benchmark-report` | Destination for `report.md` and `comparison.json`; collection also writes `bundle.json` and `revisions.json` |
| `workflow-path`, `artifact-name`, `report-key`, `sdk` | empty | Trusted publishing constants, as shown below |

Outputs `report-path` and `comparison-path` contain absolute paths. Rendering needs no token and makes no API calls. Publishing receives `GH_TOKEN` through the environment. The action uses locked Python 3.12.12 / uv 0.13.4 dependencies with caching disabled.

To render local results, run from `.github/actions/benchmark-report`:

```sh
uv sync --frozen --no-dev --no-install-project
uv run --no-sync python -I benchmarks/report/cli.py \
  --base-results /path/to/results/base.json --head-results /path/to/results/head.json \
  --output-directory /path/to/report
```

Reports also populate `GITHUB_STEP_SUMMARY` when available. Malformed inputs and execution failures produce nonzero exits while preserving failure reports. A supplied non-success `source-status` suppresses numerical comparisons, even when the last saved pair contains only successful workers. Missing, unsupported, or incompatible measurements remain explicit rather than becoming zeros.

## Result contract v1

[`result.schema.json`](../actions/benchmark-report/benchmarks/schema/result.schema.json) is the machine-readable
schema. `benchmarks/report/validate.py` adds semantic and resource bounds. Both must pass:

- Maximum 8 MiB per document, depth 20, 250,000 JSON nodes, 1,024 fields per
  object, 2,000 items per generic array, 4,096 characters per generic string.
  Raw stdout/stderr are capped at 65,536 characters and never rendered.
- At most 128 workloads, 64 metrics/workload, 100 repetitions and samples/metric,
  400 raw runs/workload and 1,000 checkpoints/run. IDs are at most 256 characters.
- Full lowercase 40-character commit SHAs and 64-character SHA-256 digests.
  Duplicate JSON keys, workload IDs, metric IDs, sample repetition IDs and raw
  `(pass, repetition)` IDs are invalid. Repetitions must be in the configured
  range; checkpoint operation positions must be unique and increasing.
- Finite, nonnegative measurements, bounded at `1e100`, and positive integer
  completed operation counts. Signed exit codes are termination evidence, not
  measurements. Checkpoints cannot exceed the run's completed operations.
- Statuses: `ok`, `error`, `timeout`, `oom`, `unsupported`. Successful metrics
  require samples, method, direction (`lower`/`higher`) and quality
  (`measured`/`estimated`); unavailable metrics require an explanation.
- Units: `ns/op`, `bytes/op`, `count/op`, `bytes`, `ns`, `count`, `ops/s`.
  Allocation estimates remain labeled as estimates. Memory deltas are not
  substitutes for allocation volume.

Suite and execution objects allow bounded extra configuration so producers can
record instrumentation policy without runtime-specific reporter branches. Raw
run objects also allow runtime-native measurement and correctness evidence.
Additional suite/execution fields participate in exact compatibility matching.

### Comparison semantics

Both sides must match SDK identity, the entire suite (version, benchmark source
commit and digest, profile, repetitions and measurement policy), and the entire
execution environment, including pair ID, runtime versions, flags, OS/kernel,
architecture, CPU, runner/image, limits and measurement-tool versions. SDK build
fields `dependency_lock_digest`, `build_digest`, `core_build`, `entry`,
`sdk_version` and `packages` may differ: those describe the code under test.
Profiler versions belong in `measurement_tools` (or exact execution metadata),
not solely in the dependency package inventory.

Each workload additionally requires identical parameters, fixture digest, raw
run identities, operations, warmup operations and checkpoint positions. Each
metric requires identical unit, direction, quality, method, scope/configuration,
paired repetition IDs and operation counts. Missing or incompatible metrics are
explicit rows, never zeros. Zero baselines have no percentage change.

The report shows medians, percentage change in the median, and descriptive
paired absolute-delta median/min/max. JSON retains individual paired percentage
changes, original metric samples, parameters, checkpoints, failure explanations
with pass/repetition identities, and both contexts. Markdown shows up to three
raw failure explanations per side; raw stdout/stderr are never copied.
All numeric comparisons are **inconclusive**: these ranges are not calibrated
confidence intervals. There is no universal threshold or aggregate score.
Incomplete repetitions are labeled. Execution failures sort first. The headline
is capped at 12 rows, preferring representative single-flag timing, all-flags
scaling timing/allocation bytes, load/refresh memory, and capture/drain cases.
Selection uses shared workload parameters and metric semantics, not runtime
profiler logic. Unsupported measurements remain in the collapsible details
rather than crowding out numerical headline rows. Markdown is bounded below 58,000 bytes;
`comparison.json` retains omitted rows. Artifact text is escaped, URLs/mentions
are neutralized, and raw stderr is not included in reports.

## Sharded collection and bundle v1

Node and Python use four independent jobs: `flags-single`, `flags-scaling`,
`flags-memory`, and `capture`. Each workload's complete paired repetitions stay on
one runner. Each producer's head-side `shards.json` declares `manifest_version: 1`,
`sdk`, and a `shards` array of `{id, workloads}` entries. Native controls require
an exact partition of both full and smoke inventories; the shared collector
requires exact coverage of that manifest on both sides. The manifest is data,
not proof that arbitrary PR code executed honestly.

Upload `base.json`, `head.json`, and `revisions.json` from each job under the unique
artifact name `benchmark-shard-<id>`. Download with `merge-multiple: false` so the
input root contains exactly those artifact directories. Collect without executing
producer scripts:

```sh
uv run --no-sync python -I benchmarks/report/cli.py \
  --mode collect --shard-manifest /path/to/shards.json \
  --shard-directory /path/to/downloads --source-status success \
  --output-directory /path/to/report
```

In CI, pass the matrix's `needs.benchmark.result` as `source-status`, not a literal
success value. Aggregate after all noncancelled matrix outcomes, including failure.
A failed source suppresses numeric comparisons across every shard and returns a
nonzero exit, while preserving valid raw data. Missing or malformed shards fail
collection rather than producing a reduced-coverage bundle. Retain the individual
raw shard artifacts for diagnostics.

The canonical raw artifact contains **only** `bundle.json` and `revisions.json`.
Upload `report.md` and `comparison.json` separately. Bundle shape:

```json
{
  "bundle_version": 1,
  "manifest": {
    "manifest_version": 1,
    "sdk": "posthog-node",
    "shards": [{"id": "flags-scaling", "workloads": ["flags.all.10"]}]
  },
  "shards": [{"id": "flags-scaling", "base": {}, "head": {}}]
}
```

The empty result objects above stand for complete schema-v1 documents. Collection
and saved-bundle validation live in `benchmarks/report/bundle.py`:

- At most 16 shards; IDs match `[a-z0-9][a-z0-9-]{0,63}`. Workload lists are
  nonempty, unique and disjoint, with at most 128 workloads per shard.
- Envelope, manifest and shard-entry fields are exact. Undeclared, duplicate,
  missing, extra, symlink and nonregular inputs are rejected.
- Every document has the expected SDK, role, `execution.shard_id` and workload
  coverage. Each shard's pair ID matches within its pair and is unique across
  shards. All documents share the same suite/source/profile/configuration and
  consistent per-role revision identity. Sidecar revisions are identical exact
  `target`, `base`, `head` SHA objects; collection checks result commits and
  head-side benchmark source against them.
- Inputs are size-checked before retaining the collection: aggregate manifest and
  shard files are capped at 8 MiB. The serialized bundle independently satisfies
  the 8-MiB JSON bound and existing structure/text limits. ZIPs remain capped at
  20 MiB compressed and uncompressed, with existing member and compression limits.
- Each pair is compared independently using the legacy compatibility rules.
  CPUs/runtimes may differ across shards, but a mismatch within a pair suppresses
  that pair's comparisons. Build provenance and environments remain per shard;
  no cross-shard ratios are averaged.

Bundle comparisons use `comparison_version: 2`, with `shards` containing per-shard
contexts, compatibility and failure state; `workload_shards` and row `shard_id`
identify workload placement. Reports explicitly label each shard's environments.
Raw bundles retain all samples, runs and checkpoints. Legacy comparisons remain
version 1. To render a saved bundle offline, use `--bundle-results /path/to/bundle.json`
instead of `--base-results`/`--head-results`; these inputs cannot be combined.

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

Use repository-owned constants for all four inputs. `report-key` is a stable, SDK-specific comment namespace, not a PR number. `sdk` must match every result document. The producer artifact contains either the legacy `base.json`, `head.json`, and `revisions.json`, or the sharded `bundle.json` and `revisions.json`. Mixed or extra members are rejected. Revisions contain `target`, `base`, and `head` full commit SHAs. Publication verifies every shard's clean SDK revision and head-side benchmark source against the trusted run, PR and merge-base.

The producing `pull_request` workflow remains read-only and can separately invoke `PostHog/.github/.github/actions/benchmark-report@<full-commit-sha>` in render-only mode for a job summary, passing `steps.measure.outcome` as `source-status`. It must not receive the publisher's writable token or service credentials. No manually supplied PR number or artifact-controlled command is accepted by the publisher.

## Trust and ordering

- Only completed `workflow_run` events can invoke the publishing job. No caller checkout occurs; reporting code is pinned to a commit of this repository in the workflow.
- The action verifies the workflow identity, source run and attempt, repository, PR association, head SHA, and merge-base against GitHub metadata before consuming results. Fork associations must be unambiguous.
- Artifacts are bounded, validated data, never executable code. Report text is escaped; result metadata cannot choose the comment's target or URLs.
- The action only updates its exact marker on the expected GitHub Actions bot's comment. It rechecks the current PR head and run attempt and rejects older reports before publication.
- A repository/report-key concurrency group serializes publishers, including fork runs whose event does not identify a PR. Cancellation is disabled so a late older event cannot cancel a newer report. `queue: max` preserves up to 100 pending publishers instead of replacing another PR's pending report. GitHub cancels additional arrivals when that queue is full; the source-run guard still prevents stale writes.
- Egress auditing runs first. Audit mode observes traffic; it does not prevent exfiltration or replace least privilege.
- Rendered Markdown and comparison JSON are retained for 14 days, including when publishing cannot complete. The source SDK workflow retains the raw measurement artifacts.

## Rollout and validation

The caller's `workflow_run` workflow must be on its default branch before it can service completed benchmark runs. Adding it to an unmerged stacked PR does not enable privileged comment publication. Land the reporting components and caller configuration through normal review; do not work around activation by executing PR code with a writable token.

Unit tests for schema, comparisons, hostile artifacts, PR identity, forks, and stale comments live with the reporting action. Its locked environment contains only reporting dependencies; SDK builds and benchmark instrumentation remain in the SDK repositories. Structural tests guard the workflow's pins, event restrictions, permissions, concurrency, token scope, and lack of caller checkout:

```sh
node --test .github/scripts/sdk-benchmark-workflow.test.mjs
cd .github/actions/benchmark-report
uv run --frozen pytest
uv run --frozen ruff check benchmarks tests
uv run --frozen black --check benchmarks tests
```

The workflow tests also invoke the composite action with Node and Python fixtures. API calls and comment writes are mocked in the unit tests; these tests never post live PR comments.

Also run Actionlint on both `sdk-benchmark-report.yml` and `sdk-benchmark-report-tests.yml`. GitHub [supports `queue: max`](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idconcurrency), but Actionlint 1.7.12 does not yet recognize that key; ignore only its `unexpected key "queue" for "concurrency" section` diagnostic until support lands. The read-only test job also uses this setting so GitHub validates the syntax before publisher rollout. Performance differences are informational, not merge gates; invalid inputs and operational errors remain visible. Linux runner calibration is separate from correctness of report generation.
