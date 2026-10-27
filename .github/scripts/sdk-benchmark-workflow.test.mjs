import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const workflow = readFileSync(new URL('../workflows/sdk-benchmark-report.yml', import.meta.url), 'utf8')

test('reporting is restricted to completed workflow_run events', () => {
    assert.match(workflow, /workflow_call:/)
    assert.match(workflow, /if: github\.event_name == 'workflow_run' && github\.event\.action == 'completed'/)
    assert.doesNotMatch(workflow, /pull_request_target:/)
})

test('publisher uses pinned trusted code and never checks out caller code', () => {
    const uses = [...workflow.matchAll(/\buses: (\S+)/g)].map((match) => match[1])
    assert.equal(uses.length, 3)
    for (const action of uses) assert.match(action, /^[\w./-]+@[0-9a-f]{40}$/)
    assert.ok(uses.some((action) => action.startsWith('PostHog/posthog-sdk-test-harness/.github/actions/benchmark-report@')))
    assert.ok(uses[0].startsWith('step-security/harden-runner@'))
    assert.doesNotMatch(workflow, /actions\/checkout|actions\/cache|secrets: inherit|script-ref|reporter-ref/)
})

test('publishers serialize without cancellation and share a report-specific lock', () => {
    assert.match(workflow, /group: sdk-benchmark-report-\$\{\{ github\.repository \}\}-\$\{\{ inputs\.report-key \}\}/)
    assert.match(workflow, /cancel-in-progress: false/)
})

test('permissions and token are explicit and scoped to the publisher', () => {
    assert.match(workflow, /permissions: \{\}/)
    assert.match(workflow, /contents: read\s+actions: read\s+pull-requests: write/)
    assert.doesNotMatch(workflow, /write-all|contents: write|id-token: write/)
    assert.equal((workflow.match(/GH_TOKEN:/g) || []).length, 1)
    assert.match(workflow, /GH_TOKEN: \$\{\{ github\.token \}\}/)
    assert.match(workflow, /timeout-minutes: 10/)
    assert.match(workflow, /egress-policy: audit/)
})

test('caller constants reach the publisher as inputs rather than shell interpolation', () => {
    for (const input of ['workflow-path', 'artifact-name', 'report-key', 'sdk']) {
        assert.match(workflow, new RegExp(`${input}: \\$\\{\\{ inputs\\.${input} \\}\\}`))
    }
    assert.match(workflow, /mode: publish/)
    assert.doesNotMatch(workflow, /\brun:|\bscript:/)
    assert.match(workflow, /if: always\(\)/)
    assert.match(workflow, /if-no-files-found: ignore/)
})
