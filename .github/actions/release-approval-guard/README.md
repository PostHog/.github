# Release approval guard

Fails a release when someone who approved its environment authored or merged a PR in the push that triggered the run.

This restores what GitHub's "Prevent self-review" environment setting did before merge queues. That setting excludes the run's triggering actor. When a person merged a PR, the actor was that person, so they couldn't approve the release. With a merge queue (GitHub or Trunk), the actor is a bot, so nobody is excluded.

## Usage

Add the step to the job that uses the approval environment, before the job uses its secrets:

```yaml
jobs:
    version-bump:
        environment: 'NPM Release'
        permissions:
            contents: read
            actions: read # approvals of this run
            pull-requests: read # PRs in the push
        steps:
            - uses: PostHog/.github/.github/actions/release-approval-guard@<sha>
              with:
                  environment: 'NPM Release'
```

It needs no checkout. It needs Node, which GitHub-hosted runners include.

## Behavior

- It finds every PR in the push that triggered the run (`before...after`). A merge queue can merge several PRs in one push.
- It blocks approval by each PR's author and by whoever merged it. With GitHub's merge queue, `merged_by` is the person who added the PR to the queue. With Trunk, it's `trunk-io[bot]`, so only the author is checked.
- On events other than `push`, it does nothing, because GitHub's own check already excludes whoever triggered the run.
- It fails closed if the run has no approval for the environment, if a commit in the push isn't part of a merged PR, or if the push range can't be listed in full.
- The approvals API doesn't say which run attempt an approval belongs to, so every approval in the run counts. After a block, a different approver must approve a **new run**. A re-run can still see the blocked approval.
- Like the setting it replaces, it only covers the PRs in the triggering push. If a release also publishes earlier unreleased changes, their authors aren't excluded.

Run the tests with `node --test .github/actions/release-approval-guard/guard.test.mjs`.
