# Container image metadata

Tags, labels and OCI provenance annotations for a service container build: the deploy-pipeline contract consumed by Kargo. Every service that promotes through Kargo stamps identical provenance: the standard `org.opencontainers.image.*` set plus commit author, committer, subject and committer timestamp, emitted as **index-level** OCI annotations (the only level Kargo's freight discovery reads; plain labels are invisible to it). Kargo warehouses key their commit-order freight gate on `org.opencontainers.image.revision`, so the annotation keys are a contract: don't fork this per repo.

## Usage

Run after `actions/checkout`. Feed all three outputs to `docker/build-push-action`. Forgetting `annotations` silently drops the provenance:

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

Ordered release aliases require two invocations of the action. The first invocation computes the ordered tag before the image build. The second invocation publishes that tag after the build returns its digest. The caller must authenticate to the target registry with a login step or equivalent before the build and publication steps.

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
    uses: PostHog/.github/.github/actions/container-image-metadata@<sha>
    with:
      images: ${{ env.IMAGE_REPOSITORY }}
      publish-ordered-release-tag: "true"
      ordered-release-tag: ${{ steps.meta.outputs.ordered-release-tag }}
      ordered-release-digest: ${{ steps.image.outputs.digest }}
```
