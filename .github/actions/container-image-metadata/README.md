# Container image metadata

Tags, labels and OCI provenance annotations for a service container build: the deploy-pipeline contract consumed by Kargo. Every service that promotes through Kargo stamps identical provenance: the standard `org.opencontainers.image.*` set plus commit author, committer, subject and committer timestamp, emitted as **index-level** OCI annotations. ASCII control characters in the commit subject, author and committer are replaced with spaces, each `{{` is separated into `{ {`, and a space is put between a comma and a double quote that directly follows it, so in those cases the annotation values and the `commit-subject`, `commit-author` and `commit-committer` outputs differ from the commit. Kargo's freight discovery reads only OCI annotations (image labels are invisible to it), and without a platform constraint it surfaces only the index level. Kargo warehouses key their commit-order freight gate on `org.opencontainers.image.revision`, so the annotation keys are a contract: don't fork this per repo.

## Usage

Run after `actions/checkout`. The job must be logged in to the target registry, with a login step or equivalent, before the build and push in both examples below. Feed all three outputs to `docker/build-push-action`. Forgetting `annotations` silently drops the provenance.

The annotations are index-level, so the pushed image must be an OCI image index. Multi-platform builds with BuildKit's default provenance attestations already produce one.

```yaml
steps:
  - uses: actions/checkout@<sha>
  - name: Image metadata
    id: meta
    uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
    with:
      images: registry.example.com/my-service
      # The contract tag (immutable sha-<gitsha>) is fixed; anything else is
      # opt-in and additive. No latest by default: it fails on immutable
      # ECR repos and nothing in the deploy pipeline reads it.
      # extra-tags: type=raw,value=latest,enable={{is_default_branch}}
  - name: Build and push
    uses: docker/build-push-action@<sha>
    with:
      push: true
      tags: ${{ steps.meta.outputs.tags }}
      labels: ${{ steps.meta.outputs.labels }}
      annotations: ${{ steps.meta.outputs.annotations }}
```

## Ordered release alias

Ordered release aliases require two invocations of the action. The first invocation computes the ordered tag (the generate phase). The second invocation, with `publish-ordered-release-tag: "true"`, writes that tag as an alias of the built digest and reads it back from the registry (the publish phase).

Ordered tag generation and publication need:

- A `push` or `workflow_dispatch` event on the repository's default branch. On any other event or ref, schedules included, both phases are skipped: `ordered-release-tag` is empty, no alias is published and neither phase fails. The standard tags, labels and annotations are still produced. The ref must also match the default branch exactly, letter case included: a ref that differs from it only in letter case passes the step conditions, so generation is skipped with a warning and the publish phase fails.
- A checkout of the triggering revision (`GITHUB_SHA`) before each invocation. If another revision is checked out, no ordered tag is generated.
- A runner whose `timeout` supports `--kill-after`, as GNU coreutils and uutils coreutils on Ubuntu GitHub-hosted runners do.
- `jq` and `curl` on `PATH` for the publish phase's alert, as on Ubuntu GitHub-hosted runners.

### What fails and what only warns

The generate phase never fails for the ordered tag, so the publish call after it runs. When no tag can be made, it writes the warning `Skipping ordered release tag because <reason>` and leaves `ordered-release-tag` empty. On a shallow checkout it fetches the history treeless (`--filter=tree:0`). When that fetch fails, it writes a warning, removes the `remote.origin.promisor` and `remote.origin.partialclonefilter` settings the failed fetch left, and fetches the full history once without the filter. The tag is skipped only when that fetch fails too.

The publish phase fails unless the registry read-back finds the alias at the built digest. Its last step, `Check ordered release alias`, is the only step of the phase that fails. It writes the notice `Ordered image alias present` when the alias is confirmed, and otherwise the error `Ordered image alias not confirmed` with the result or reason, then fails. Every run of that step logs whether an alert token was supplied. When the runner's `timeout` lacks `--kill-after`, publication writes the error `Ordered image alias read-back unavailable` and the result is `unknown`.

Publish inputs are validated before anything is written. When validation fails, the phase writes the warning `Skipping ordered image alias publication because <reason>`, writes nothing to the registry, and the last step fails with that reason. Validation fails for a ref that is not exactly the default branch, an `images` value that is not exactly one repository in registry/name form, a malformed tag, a missing or malformed digest, or a tag whose six-character suffix is not the start of `GITHUB_SHA`. The action does not read the commit from the image: it compares the suffix with the `GITHUB_SHA` of the publishing run, so publish in the same workflow run as the build. An empty ordered tag skips publication the same way and is reported as `tag-unavailable`.

The commit metadata and `Docker meta` steps run in every invocation and fail their step on error. When one of them fails, the job is already failed and the last step does not run, so no alert is sent. Nothing runs after a cancelled step either.

### Attempts

An attempt starts with a probe: it reads the alias up to twice and, only when neither read succeeds, reads the built digest. When the alias was not read and the built digest was, the probe calls it `absent`, and only then does the attempt write the alias and read it back the same way. `absent` is an inference from two failed reads, not a confirmed absence: the write goes ahead whatever the registry holds under that tag, so an existing alias that could not be read may be overwritten. What is guaranteed is that a mismatch the action observes, at either attempt's probe or read-back, ends the phase as `integrity-mismatch` with no further write. A probe that can read neither ends the attempt `unknown` without writing. A read-back that can read neither after the write also ends the attempt `unknown`, so an `unknown` attempt may have written.

When the first attempt ends `absent` or `unknown`, the phase waits five seconds and makes a second attempt, which probes again before it writes. The second attempt's result is the phase's result, and the log names the attempt that gave it. When the phase ends without a confirmed alias, the log also carries the registry client's output and exit status from the last failed read and the last write, inside a block that cannot issue workflow commands. A read whose client exited 0 with output that is not a digest is labelled an output-format failure. An exit status of 124 or 137 means the time limit stopped the client.

Two things are visible to a caller: the `ordered-release-result` output of the publish invocation (`steps.alias.outputs.ordered-release-result` in the example), and the result the last step reports in its annotation and alert. They differ when the output is empty, and when a cancellation keeps the last step from running; the table rows with a non-empty output assume the last step runs. The output is set only by the publication script, and it is empty in four cases:
- Outside the event and ref gate, the publish phase's own steps (validation, publication and the last step) do not run, so the publish phase cannot fail. The commit metadata and `Docker meta` steps still run, as in every invocation.
- When validation skips publication (no ordered tag, or a failed check), the last step reports `tag-unavailable` or the reason and fails.
- When the publication script fails before it sets the output, the last step reports `unknown` and fails. A caller that branches on the output cannot tell this case from a validation skip by the output alone; the last step's annotation names the result it reported.
- When an earlier step of the publish invocation fails, publication and the last step do not run and no alert is sent. When the job is cancelled before or during publication, the last step does not run and no alert is sent; a cancellation during publication may leave the alias written.

A cancellation keeps a last step that has not started from starting, whatever the output already holds. A last step that was already running when the cancellation arrived may have written its error annotation, which it writes before the post, and may have started its post, which may still reach Slack. Nothing already done is undone: an alias that publication wrote stays written.

| `ordered-release-result` output | Result the last step reports | What the publish phase did | Last step | Message |
|---|---|---|---|---|
| `present-at-expected-digest` | `present-at-expected-digest` | Found the alias at the built digest, already there or written and read back. | Passes. | None. |
| `absent` | `absent` | In the last attempt, the read-back after the write could not read the alias while the built digest could be read. | Fails. | Kargo creates no Freight for this build, so deploys stay on the previous build. Re-running the failed job retries. |
| `unknown` | `unknown` | In the last attempt, the probe or the read-back after a write could read neither the alias nor the built digest; or no `timeout` with `--kill-after` was found. | Fails. | The alias could not be verified at the expected digest, so whether Kargo creates Freight for this build is uncertain. Re-running the failed job retries. |
| `integrity-mismatch` | `integrity-mismatch` | Found the alias at another digest in a probe or a read-back and ended the phase with no further write. | Fails. | The same consequence as `unknown`. A re-run cannot clear it. |
| empty | `tag-unavailable` or the validation reason | Validation skipped publication; nothing was written. | Fails. | Kargo creates no Freight for this build, so deploys stay on the previous build. Re-running the failed job retries. |
| empty | `unknown` | The publication script failed before setting the output; it may have written before it failed. | Fails. | The same as `unknown` above. |
| empty | None: the last step does not run. | Outside the gate, validation, publication and the last step did not run; after an earlier failed step, publication did not run; after a cancellation before or during publication, publication may have written the alias. | Does not run. | None. |

The retry sentence is true because the publish phase runs in a job of its own (see [What a caller must do](#what-a-caller-must-do)): the failed job is the alias job, which generates the tag again and builds nothing. A validation failure that a re-run repeats, such as a malformed input or a ref that is not the default branch, is told apart by its reason, which the message names.

### Alert

When the last step is about to fail and `alert-slack-bot-token` is set, the action attempts one post with Slack's `chat.postMessage` to `alert-slack-channel` (default `C0B11J29GP9`). The bot must be a member of that channel. The message names the repository, the image repository, the ordered tag or `none`, the result or validation reason, the short commit and the run link, followed by the two sentences in the table above. The alert escapes `&`, `<` and `>` in the supplied image repository and ordered tag, which prevents angle-bracket mentions and labelled links. Slack can still format the rest of those values, such as asterisks, underscores, tildes and backticks, and can still link a bare URL.

An attempted post is not always an accepted one. The log says `Alert sent to Slack` only when Slack's reply is exactly one JSON object whose `ok` is the boolean `true`. Otherwise the step writes the warning `Ordered image alias alert not sent`, in these cases:
- the message could not be built;
- curl failed or passed its 10-second limit;
- the reply was anything else, in which case the warning names Slack's error code, such as `not_in_channel`, when there is one.

None of these changes the outcome: the step still fails for the alias. Without a token nothing is posted and the step still fails.

### Time budget

The figures below are totals of the timed operations, not a limit on the phase. The action has no phase timer, and other commands add a little. The alias job needs no `timeout-minutes`, because the action bounds its own work. A caller that does limit the job sets the limit above the two budgets together, 905 seconds (440 + 465), because a job that is ended early never reaches the step that alerts.

- Generate phase: the history fetches total at most 440 seconds, 120 seconds for the treeless fetch and 300 for the full fetch that follows when it fails, each with a 10-second kill grace (130 + 310).
- Publish phase: the registry operations and the alert total at most 465 seconds. An attempt makes up to six 20-second registry reads and one 35-second write, each with a 10-second kill grace (6 × 30 + 45 = 225 seconds). Two attempts, the 5-second pause between them and the alert's 10 seconds make 2 × 225 + 5 + 10.

### What a caller must do

- Pass `alert-slack-bot-token` to the publish invocation, from a secret, to get the Slack alert. It is optional: without it the phase still fails and writes its error annotation, with no Slack message, for callers that alert another way.
- Call the publish phase once per job. A failed call fails its job, and under the default step condition that skips every later step of the job, so a second publish call in the same job would never run when the first fails: its alias would not be written and no message would name its image repository. Do not put two publish calls in one job. For several image repositories, use one job per repository or a matrix over `images`; each call that reaches its last step and fails then sends its own message, when a token is passed, naming its own image repository.
- In every calling repository that publishes the alias, the publish phase runs in a job of its own, after the build. That job checks out the commit, logs in to the registry, calls the action to generate the ordered tag and calls it again to publish and check the alias. Only the digest crosses from the build job, as a job output. The image repository is named in the alias job itself. Re-running that job alone then repairs a missing alias, an alias that could not be verified and a tag that could not be generated, and builds nothing. The reason is the registry: a re-run that builds the image again pushes another digest, and a registry that keeps its tags immutable refuses to push a tag that already exists, so the job that can be re-run must build nothing.
- The two calls stay two. The action's phases are separate invocations, and an empty tag passed to the publish phase keeps its meaning, `tag-unavailable`.
- The build job calls the action once for `tags`, `labels` and `annotations` and does not ask for the ordered tag: the action adds it to no metadata tag and writes it into no annotation.
- Where other jobs depend on the build, nothing depends on the alias job and it is marked `continue-on-error: true` at job level. The job shows failed, the run does not fail, and the other jobs run. A step-level `continue-on-error` reports the step as succeeded and hides the failure, so it is not the way. Where no other job depends on the build, as in the example below, the alias job is not marked and a missing alias fails the run.
- The checkout may be shallow: the action fetches the history the tag needs. The alias job sets no time limit of its own, as [Time budget](#time-budget) explains.

```yaml
env:
  IMAGE_REPOSITORY: registry.example.com/my-service
jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      # Add what your registry login needs, for example id-token: write or packages: write.
    outputs:
      digest: ${{ steps.image.outputs.digest }}
    steps:
      - uses: actions/checkout@<sha>
      # Log in to the registry here.
      - name: Image metadata
        id: meta
        uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
        with:
          images: ${{ env.IMAGE_REPOSITORY }}
      - name: Build and push
        id: image
        uses: docker/build-push-action@<sha>
        with:
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          annotations: ${{ steps.meta.outputs.annotations }}
  ordered-release-alias:
    runs-on: ubuntu-latest
    needs: build
    permissions:
      contents: read
      # Add what your registry login needs, for example id-token: write or packages: write.
    steps:
      - uses: actions/checkout@<sha>
      # Log in to the registry here.
      - name: Generate ordered release tag
        id: ordered-tag
        uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
        with:
          images: ${{ env.IMAGE_REPOSITORY }}
          generate-ordered-release-tag: "true"
      - name: Publish ordered release alias
        id: alias
        uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
        with:
          images: ${{ env.IMAGE_REPOSITORY }}
          publish-ordered-release-tag: "true"
          ordered-release-tag: ${{ steps.ordered-tag.outputs.ordered-release-tag }}
          ordered-release-digest: ${{ needs.build.outputs.digest }}
          alert-slack-bot-token: ${{ secrets.ALERT_SLACK_BOT_TOKEN }}
```
