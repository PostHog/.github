import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { checkReleaseApproval, createApi, GuardError } from './guard.mjs';

const repo = 'PostHog/posthog-js';
const API = 'https://api.github.test';
const before = 'b'.repeat(40);
const after = 'a'.repeat(40);

function fakeApi(routes, failures = {}) {
    const fetchImpl = async (url) => {
        const path = url.slice(API.length);
        if (failures[path] > 0) {
            failures[path]--;
            return new Response('unavailable', { status: 502 });
        }
        if (!(path in routes)) return new Response(`no route ${path}`, { status: 404 });
        return Response.json(routes[path]);
    };
    return createApi({ token: 't', apiUrl: API, fetchImpl, retryDelayMs: 0 });
}

const approval = (login, environment = 'NPM Release', state = 'approved') => ({
    state,
    user: { login },
    environments: [{ name: environment }],
});

// Each entry is [commit sha, PR number, author, merged_by].
function push(approvals, prs) {
    const routes = {
        [`/repos/${repo}/actions/runs/7/approvals`]: approvals,
        [`/repos/${repo}/compare/${before}...${after}`]: {
            total_commits: prs.length,
            commits: prs.map(([sha]) => ({ sha })),
        },
    };
    for (const [sha, number, author, mergedBy] of prs) {
        routes[`/repos/${repo}/commits/${sha}/pulls`] = [{ number, merged_at: '2026-10-01T00:00:00Z' }];
        routes[`/repos/${repo}/pulls/${number}`] = {
            number,
            user: { login: author },
            merged_by: mergedBy && { login: mergedBy },
        };
    }
    return routes;
}

const check = (routes, environment = 'NPM Release') =>
    checkReleaseApproval({ api: fakeApi(routes), repo, runId: 7, environment, before, after });

test('passes when no approver authored or merged a PR in the push', async () => {
    const result = await check(push([approval('bob')], [['c1', 1, 'alice', 'alice']]));
    assert.deepEqual(result.violations, []);
    assert.deepEqual(result.prs, [1]);
});

test('blocks the PR author', async () => {
    const result = await check(push([approval('alice')], [['c1', 1, 'alice', 'github-merge-queue[bot]']]));
    assert.deepEqual(result.violations, [{ login: 'alice', reasons: ['author of #1'] }]);
});

test('blocks whoever merged the PR', async () => {
    const result = await check(push([approval('bob')], [['c1', 1, 'external', 'bob']]));
    assert.deepEqual(result.violations, [{ login: 'bob', reasons: ['merged #1'] }]);
});

test('checks every PR in a batched merge queue push', async () => {
    const prs = [
        ['c1', 1, 'alice', 'alice'],
        ['c2', 2, 'carol', 'carol'],
    ];
    const result = await check(push([approval('carol')], prs));
    assert.deepEqual(result.prs, [1, 2]);
    assert.deepEqual(result.violations.map((v) => v.login), ['carol']);
});

test('matches logins case-insensitively', async () => {
    const result = await check(push([approval('Alice')], [['c1', 1, 'alice', 'alice']]));
    assert.equal(result.violations[0].login, 'Alice');
});

test('blocks when any approval in the run came from an author', async () => {
    const result = await check(push([approval('bob'), approval('alice')], [['c1', 1, 'alice', 'alice']]));
    assert.deepEqual(result.violations.map((v) => v.login), ['alice']);
});

test('ignores approvals that are rejected or for other environments', async () => {
    const approvals = [approval('alice', 'S3 Upload'), approval('alice', 'NPM Release', 'rejected'), approval('bob')];
    const result = await check(push(approvals, [['c1', 1, 'alice', 'alice']]));
    assert.deepEqual(result.approvers, ['bob']);
    assert.deepEqual(result.violations, []);
});

test('fails closed when the run has no approval for the environment', async () => {
    await assert.rejects(check(push([approval('bob', 'Other')], [['c1', 1, 'alice', 'alice']])), GuardError);
});

test('fails closed when a commit in the push is not from a merged PR', async () => {
    const routes = push([approval('bob')], [['c1', 1, 'alice', 'alice']]);
    routes[`/repos/${repo}/commits/c1/pulls`] = [{ number: 1, merged_at: null }];
    await assert.rejects(check(routes), /c1 in the push does not belong to a merged PR/);
});

test('fails closed when the comparison does not list every commit', async () => {
    const routes = push([approval('bob')], [['c1', 1, 'alice', 'alice']]);
    routes[`/repos/${repo}/compare/${before}...${after}`].total_commits = 300;
    await assert.rejects(check(routes), /Cannot list all 300 commits/);
});

test('fails closed when the push has no previous commit', async () => {
    const routes = push([approval('bob')], [['c1', 1, 'alice', 'alice']]);
    await assert.rejects(
        checkReleaseApproval({ api: fakeApi(routes), repo, runId: 7, environment: 'NPM Release', before: '0'.repeat(40), after }),
        /has no previous commit/,
    );
});

test('surfaces GitHub API errors', async () => {
    const routes = push([approval('bob')], [['c1', 1, 'alice', 'alice']]);
    delete routes[`/repos/${repo}/pulls/1`];
    await assert.rejects(check(routes), /GitHub API 404/);
});

test('retries GitHub server errors', async () => {
    const routes = push([approval('bob')], [['c1', 1, 'alice', 'alice']]);
    const compare = `/repos/${repo}/compare/${before}...${after}`;
    const api = fakeApi(routes, { [compare]: 2 });
    const result = await checkReleaseApproval({ api, repo, runId: 7, environment: 'NPM Release', before, after });
    assert.deepEqual(result.prs, [1]);
    await assert.rejects(
        checkReleaseApproval({ api: fakeApi(routes, { [compare]: 3 }), repo, runId: 7, environment: 'NPM Release', before, after }),
        /GitHub API 502/,
    );
});

test('leaves runs that are not pushes to GitHub\'s own check', () => {
    const script = fileURLToPath(new URL('./guard.mjs', import.meta.url));
    const out = execFileSync('node', [script], {
        env: { ...process.env, GITHUB_EVENT_NAME: 'workflow_dispatch' },
        encoding: 'utf8',
    });
    assert.match(out, /Not a push \(workflow_dispatch\)/);
});
