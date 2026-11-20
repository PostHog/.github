# Container image metadata

Tags, labels and OCI provenance annotations for a service container build: the deploy-pipeline contract consumed by Kargo. Every service that promotes through Kargo stamps identical provenance: the standard `org.opencontainers.image.*` set plus commit author, committer, subject and committer timestamp, emitted as **index-level** OCI annotations. ASCII control characters in the commit subject, author and committer are replaced with spaces, and each `{{` is separated into `{ {`, so in those cases the annotation values and the `commit-subject`, `commit-author` and `commit-committer` outputs differ from the commit. Kargo's freight discovery reads only OCI annotations (image labels are invisible to it), and without a platform constraint it surfaces only the index level. Kargo warehouses key their commit-order freight gate on `org.opencontainers.image.revision`, so the annotation keys are a contract: don't fork this per repo.

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

Ordered release aliases require two invocations of the action. The first invocation computes the ordered tag before the image build. The second invocation publishes that tag after the build returns its digest.

Ordered tag generation and publication need:

- A `push` or `workflow_dispatch` event on the repository's default branch. On any other event or ref, schedules included, generation and publication are skipped: `ordered-release-tag` is empty and no alias is published. The standard tags, labels and annotations are still produced.
- A checkout of the triggering revision (`GITHUB_SHA`) before the action. If another revision is checked out, no ordered tag is generated.
- A runner whose `timeout` supports `--kill-after`, as GNU coreutils and uutils coreutils on Ubuntu GitHub-hosted runners do.

Today a failure in generation or publication does not fail the job. Generation logs a warning and leaves `ordered-release-tag` empty. Publication reports a warning or an error annotation. Read the second invocation's `ordered-release-result` output (`steps.alias.outputs.ordered-release-result` in the example) for the outcome: `present-at-expected-digest`, `absent`, `integrity-mismatch` or `unknown`. It is empty when publication did not run, for example because no ordered tag was generated. When an ordered tag is passed, invalid publication inputs do fail the job: an `images` value that is not exactly one repository in registry/name form, a malformed tag, a missing or malformed digest, or a tag that does not match the revision.

```yaml
env:
  IMAGE_REPOSITORY: registry.example.com/my-service
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
  - name: Publish ordered release alias
    id: alias
    uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
    with:
      images: ${{ env.IMAGE_REPOSITORY }}
      publish-ordered-release-tag: "true"
      ordered-release-tag: ${{ steps.meta.outputs.ordered-release-tag }}
      ordered-release-digest: ${{ steps.image.outputs.digest }}
```
