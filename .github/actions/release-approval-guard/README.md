# Release approval guard

Fails a release when someone who approved its environment authored, or pushed commits to, a merged PR whose changeset is in the release.

GitHub's "Prevent self-review" environment setting compares the approver only with the run's triggering actor. With a merge queue (GitHub or Trunk), that actor is a bot, so a PR author can approve their own release. The setting also never checked the other authors in a release that batches several PRs.

## Usage

Add the step to the job that uses the approval environment, after the checkout of the release ref and before the job uses its secrets:

```yaml
jobs:
    version-bump:
        environment: 'NPM Release'
        permissions:
            contents: read
            actions: read # approvals of this run
            pull-requests: read # PRs and their commits
        steps:
            - uses: actions/checkout@<sha>
            - uses: PostHog/.github/.github/actions/release-approval-guard@<sha>
              with:
                  environment: 'NPM Release'
                  changeset-dirs: '.changeset' # e.g. cli/.sampo/changesets for sampo
```

The checkout can be shallow. The step reads the changeset files in the working tree and resolves their history through the GitHub API at `HEAD`. It needs Node, which GitHub-hosted runners include.

## Behavior

- Contributors are the author of every merged PR that added or edited a pending changeset, plus the author and committer of every commit in those PRs.
- It fails closed. It fails if there are no changesets, if the run has no approval for the environment, or if a commit that changed a changeset isn't part of a merged PR.
- The approvals API doesn't say which run attempt an approval belongs to, so every approval in the run counts. After a block, a different approver must approve a **new run**. A re-run can still see the blocked approval.

Run the tests with `node --test .github/actions/release-approval-guard/guard.test.mjs`.
