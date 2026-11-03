# .github

The `.github` repository is a special repository in GitHub organizations. It serves several purposes:

## 🔄 Shared workflows and actions

This repository allows us to define **reusable workflows** and **composite actions** that can be shared across all repositories in the PostHog organization. Instead of duplicating CI/CD configurations in every repo, we can centralize them here and reference them from any repository.

For example, repositories can reference workflows from here using:

```yaml
uses: PostHog/.github/.github/workflows/reusable-workflow.yml@main
```

### SDK performance reports

[SDK benchmark reporting](.github/workflows/sdk-benchmark-report.md) provides the result schema, reporting action, and reusable publishing workflow. SDK repositories run their benchmarks on PRs; a separate trusted `workflow_run` job consumes the result artifacts and updates an informational comment on the originating PR.

## 📄 Organization-wide community health files

Files like `CODE_OF_CONDUCT.md`, `SECURITY.md`, and other community health files placed here become the default for all repositories in the organization that don't have their own versions.

## 👋 Organization profile

The `profile/README.md` file is displayed on the [PostHog GitHub organization page](https://github.com/PostHog). It serves as a welcome message and introduction for anyone visiting our organization on GitHub.
