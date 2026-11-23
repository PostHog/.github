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

Ordered release aliases require two invocations of the action. The first invocation computes the ordered tag before the image build (the generate phase). The second invocation, with `publish-ordered-release-tag: "true"`, writes that tag as an alias of the built digest and reads it back from the registry (the publish phase).

Ordered tag generation and publication need:

- A `push` or `workflow_dispatch` event on the repository's default branch. On any other event or ref, schedules included, both phases are skipped: `ordered-release-tag` is empty, no alias is published and nothing fails. The standard tags, labels and annotations are still produced. The ref must also match the default branch exactly, letter case included: a ref that differs from it only in letter case passes the step conditions, so generation is skipped with a warning and the publish phase fails.
- A checkout of the triggering revision (`GITHUB_SHA`) before each invocation. If another revision is checked out, no ordered tag is generated.
- A runner whose `timeout` supports `--kill-after`, as GNU coreutils and uutils coreutils on Ubuntu GitHub-hosted runners do.

### What fails and what only warns

The generate phase never fails for the ordered tag, so the build it runs in finishes. When no tag can be made, it writes the warning `Skipping ordered release tag because <reason>` and leaves `ordered-release-tag` empty. On a shallow checkout it fetches the history treeless (`--filter=tree:0`). When that fetch fails, it writes a warning, removes the `remote.origin.promisor` and `remote.origin.partialclonefilter` settings the failed fetch left, and fetches the full history once without the filter. The tag is skipped only when that fetch fails too.

The publish phase fails unless the registry read-back finds the alias at the built digest. Its last step, `Check ordered release alias`, is the only step of the phase that fails. It writes the notice `Ordered image alias present` when the alias is confirmed, and otherwise the error `Ordered image alias not confirmed` with the result or reason, then fails. Every run of that step logs whether an alert token was supplied. When the runner's `timeout` lacks `--kill-after`, publication writes the error `Ordered image alias read-back unavailable` and the result is `unknown`.

Publish inputs are validated before anything is written. When validation fails, the phase writes the warning `Skipping ordered image alias publication because <reason>`, writes nothing to the registry, and the last step fails with that reason. Validation fails for a ref that is not exactly the default branch, an `images` value that is not exactly one repository in registry/name form, a malformed tag, a missing or malformed digest, or a tag whose six-character suffix is not the start of `GITHUB_SHA`. The action does not read the commit from the image: it compares the suffix with the `GITHUB_SHA` of the publishing run, so publish in the same workflow run as the build. An empty ordered tag skips publication the same way and is reported as `tag-unavailable`.

The commit metadata and `Docker meta` steps run in both invocations and fail their step on error. When one of them fails, the job is already failed and the last step does not run, so no alert is sent. Nothing runs after a cancelled step either.

### Attempts

One attempt reads the alias up to twice and, only when neither read succeeds, reads the built digest. The alias is written only when it could not be read and the built digest could (`absent`). When neither can be read the attempt ends `unknown` and nothing is written. An alias found at another digest is never moved. After a write the attempt reads the alias back the same way.

When the first attempt ends `absent` or `unknown`, the phase waits five seconds and makes a second attempt, which probes again before it writes. The second attempt's result is the phase's result, and the log names the attempt that gave it. When the phase ends without a confirmed alias, the log also carries the registry client's output and exit status from the last failed read and the last write, inside a block that cannot issue workflow commands.

The `ordered-release-result` output of the publish invocation (`steps.alias.outputs.ordered-release-result` in the example) is empty when publication did not run.

| `ordered-release-result` | What the publish phase did | Last step | Message |
|---|---|---|---|
| `present-at-expected-digest` | Found the alias at the built digest, already there or written and read back. | Passes. | None. |
| `absent` | After two attempts, could not read the alias after writing it, while the built digest could be read. | Fails. | Kargo creates no Freight for this build, so deploys stay on the previous build. Re-running the failed job retries. |
| `unknown` | Could read neither the alias nor the built digest at the end of the last attempt, or found no `timeout` with `--kill-after`. An attempt whose probe ends `unknown` writes nothing. | Fails. | The alias could not be verified at the expected digest, so whether Kargo creates Freight for this build is uncertain. Re-running the failed job retries. |
| `integrity-mismatch` | Found the alias at another digest and left it there, with no second attempt. | Fails. | The same consequence as `unknown`. A re-run cannot clear it. |
| empty | Skipped publication: no ordered tag (`tag-unavailable`) or failed validation. | Fails with `tag-unavailable` or the reason. | Kargo creates no Freight for this build, so deploys stay on the previous build. Re-run all jobs: the failed job alone reuses the build's empty value. |

### Alert

When the last step is about to fail and `alert-slack-bot-token` is set, the action posts one message with Slack's `chat.postMessage` to `alert-slack-channel` (default `C0B11J29GP9`). The bot must be a member of that channel. The message names the repository, the image repository, the ordered tag or `none`, the result or validation reason, the short commit and the run link, followed by the two sentences in the table above. The post is limited to 10 seconds and cannot change the outcome: when curl fails or Slack's reply is not `"ok": true`, the step writes the warning `Ordered image alias alert not sent`, naming Slack's error code such as `not_in_channel` when there is one, and still fails for the alias. Without a token nothing is posted and the step still fails.

### Time budget

- Generate phase: at most 440 seconds of history fetch, 120 seconds for the treeless fetch and 300 for the full fetch that follows when it fails, each with a 10-second kill grace (130 + 310).
- Publish phase: at most 465 seconds. An attempt makes up to six 20-second registry reads and one 35-second write, each with a 10-second kill grace (6 × 30 + 45 = 225 seconds). Two attempts, the 5-second pause between them and the alert's 10 seconds make 2 × 225 + 5 + 10.

### What a caller must do

- Pass `alert-slack-bot-token` to the publish invocation, from a secret.
- Where other jobs depend on the job that publishes the alias, put the publish invocation in its own job with `continue-on-error: true` at job level, so a missing alias fails that job without blocking its dependents. A step-level `continue-on-error` reports the step as succeeded and hides the failure, so it is not the way. Take `tags`, `labels` and `annotations` from the first invocation.

```yaml
env:
  IMAGE_REPOSITORY: registry.example.com/my-service
jobs:
  build:
    runs-on: ubuntu-24.04
    outputs:
      ordered-release-tag: ${{ steps.meta.outputs.ordered-release-tag }}
      digest: ${{ steps.image.outputs.digest }}
    steps:
      - uses: actions/checkout@<sha>
      - name: Image metadata and ordered release tag
        id: meta
        uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
        with:
          images: ${{ env.IMAGE_REPOSITORY }}
          generate-ordered-release-tag: "true"
      - name: Build and push
        id: image
        uses: docker/build-push-action@<sha>
        with:
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          annotations: ${{ steps.meta.outputs.annotations }}
  ordered-release-alias:
    runs-on: ubuntu-24.04
    needs: build
    continue-on-error: true
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@<sha>
      # Log in to the registry here.
      - name: Publish ordered release alias
        id: alias
        uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
        with:
          images: ${{ env.IMAGE_REPOSITORY }}
          publish-ordered-release-tag: "true"
          ordered-release-tag: ${{ needs.build.outputs.ordered-release-tag }}
          ordered-release-digest: ${{ needs.build.outputs.digest }}
          alert-slack-bot-token: ${{ secrets.ALERT_SLACK_BOT_TOKEN }}
```

When nothing depends on the build job, the publish invocation can stay in it as a last step after `Build and push`, with the same inputs taken from `steps.meta` and `steps.image`.
