import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import test from 'node:test';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
    blockedApprovers,
    blockedMrkdwn,
    checkReleaseApproval,
    createApi,
    GuardError,
    guardOutputs,
    prsMrkdwn,
    writeOutputs,
} from './guard.mjs';

const repo = 'PostHog/posthog-js';
const API = 'https://api.github.test';
const script = fileURLToPath(new URL('./guard.mjs', import.meta.url));
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
            html_url: `https://github.com/${repo}/pull/${number}`,
            title: `PR ${number}`,
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
    assert.deepEqual(result.pullRequests.map((p) => p.number), [1]);
});

test('blocks the PR author', async () => {
    const result = await check(push([approval('alice')], [['c1', 1, 'alice', 'github-merge-queue[bot]']]));
    assert.deepEqual(result.violations, [{ login: 'alice', reasons: ['author of #1'] }]);
});

test('blocks whoever queued the PR', async () => {
    const result = await check(push([approval('bob')], [['c1', 1, 'external', 'bob']]));
    assert.deepEqual(result.violations, [{ login: 'bob', reasons: ['queued #1'] }]);
});

test('checks every PR in a batched merge queue push', async () => {
    const prs = [
        ['c1', 1, 'alice', 'alice'],
        ['c2', 2, 'carol', 'carol'],
    ];
    const result = await check(push([approval('carol')], prs));
    assert.deepEqual(result.pullRequests.map((p) => p.number), [1, 2]);
    assert.deepEqual(result.violations.map((v) => v.login), ['carol']);
});

test('matches logins case-insensitively', async () => {
    const result = await check(push([approval('Alice')], [['c1', 1, 'alice', 'alice']]));
    assert.deepEqual(result.violations.map((v) => v.login), ['alice']);
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
    assert.deepEqual(result.pullRequests.map((p) => p.number), [1]);
    await assert.rejects(
        checkReleaseApproval({ api: fakeApi(routes, { [compare]: 3 }), repo, runId: 7, environment: 'NPM Release', before, after }),
        /GitHub API 502/,
    );
});

const run = (env, options = {}) =>
    execFileSync('node', [script], { env: { ...process.env, ...env }, encoding: 'utf8', ...options });

test('leaves runs that are not pushes to GitHub\'s own check', () => {
    assert.match(run({ GITHUB_EVENT_NAME: 'workflow_dispatch' }), /Not a push \(workflow_dispatch\)/);
});

const pr = (number, author, mergedBy, title = `PR ${number}`) => ({
    number,
    title,
    html_url: `https://github.com/${repo}/pull/${number}`,
    user: author && { login: author },
    merged_by: mergedBy && { login: mergedBy },
});

test('blockedApprovers lists authors and enqueuers sorted by login', () => {
    const blocked = blockedApprovers([pr(2, 'zed', 'bob'), pr(1, 'alice', 'bob')]);
    assert.deepEqual(blocked, [
        { login: 'alice', reasons: ['author of #1'] },
        { login: 'bob', reasons: ['queued #2', 'queued #1'] },
        { login: 'zed', reasons: ['author of #2'] },
    ]);
});

test('blockedApprovers dedupes logins case-insensitively and keeps the first casing', () => {
    const blocked = blockedApprovers([pr(1, 'Alice', 'alice')]);
    assert.deepEqual(blocked, [{ login: 'Alice', reasons: ['author of #1', 'queued #1'] }]);
});

test('blockedApprovers skips bots and missing users', () => {
    const blocked = blockedApprovers([pr(1, 'dependabot[bot]', 'github-merge-queue[bot]'), pr(2, null, null)]);
    assert.deepEqual(blocked, []);
});

test('blockedMrkdwn links each login with its reasons', () => {
    const blocked = [
        { login: 'alice', reasons: ['queued #5273'] },
        { login: 'bob', reasons: ['author of #1', 'queued #2'] },
    ];
    assert.equal(
        blockedMrkdwn(blocked),
        '<https://github.com/alice|alice> (queued #5273), <https://github.com/bob|bob> (author of #1, queued #2)',
    );
    assert.equal(blockedMrkdwn([]), '');
});

test('prsMrkdwn escapes Slack control characters in titles', () => {
    const out = prsMrkdwn([pr(1, 'a', 'b', 'fix <b> & "quotes"'), pr(2, 'a', 'b')]);
    assert.equal(
        out,
        `<https://github.com/${repo}/pull/1|#1> fix &lt;b&gt; &amp; "quotes"\n<https://github.com/${repo}/pull/2|#2> PR 2`,
    );
});

test('prsMrkdwn truncates to the length limit', () => {
    const prs = [pr(1, 'a', 'b', 'x'.repeat(50)), pr(2, 'a', 'b', 'y'.repeat(50)), pr(3, 'a', 'b', 'z'.repeat(50))];
    const out = prsMrkdwn(prs, 130);
    assert.equal(out.split('\n').length, 2);
    assert.match(out, /…and 2 more$/);
});

test('guardOutputs summarizes the push', () => {
    const prs = [pr(1, 'alice', 'bob'), pr(2, 'carol', 'github-merge-queue[bot]')];
    const outputs = guardOutputs(prs, blockedApprovers(prs));
    assert.equal(outputs.blocked_logins, 'alice,bob,carol');
    assert.equal(outputs.prs, '1,2');
    assert.match(outputs.prs_mrkdwn, /^<.*\|#1> PR 1\n<.*\|#2> PR 2$/);
});

test('writeOutputs writes multiline values as heredocs', () => {
    const file = join(mkdtempSync(join(tmpdir(), 'guard-')), 'output');
    writeOutputs(file, { prs: '1,2', prs_mrkdwn: 'a\nb' });
    const text = readFileSync(file, 'utf8');
    assert.match(text, /^prs<<(ghadelimiter_[\w-]+)\n1,2\n\1\nprs_mrkdwn<<(ghadelimiter_[\w-]+)\na\nb\n\2\n$/);
});

test('checkReleaseApproval exposes the blocked list and PRs for the outputs', async () => {
    const result = await check(push([approval('alice')], [['c1', 1, 'alice', 'github-merge-queue[bot]']]));
    assert.deepEqual(result.blocked, [{ login: 'alice', reasons: ['author of #1'] }]);
    assert.deepEqual(result.pullRequests.map((p) => p.number), [1]);
});

test('report mode warns and writes empty outputs on errors', () => {
    const dir = mkdtempSync(join(tmpdir(), 'guard-'));
    const output = join(dir, 'output');
    const out = run({
        MODE: 'report',
        GITHUB_EVENT_NAME: 'push',
        GITHUB_EVENT_PATH: join(dir, 'missing.json'),
        GITHUB_OUTPUT: output,
    });
    assert.match(out, /::warning title=Release approval guard::/);
    assert.match(readFileSync(output, 'utf8'), /blocked_logins<<ghadelimiter_[\w-]+\n\nghadelimiter_/);
});

test('rejects an unknown mode', () => {
    const script = fileURLToPath(new URL('./guard.mjs', import.meta.url));
    assert.throws(
        () => run({ MODE: 'bogus' }, { stdio: 'pipe' }),
        (e) => /Unknown mode "bogus"/.test(e.stdout),
    );
});

test('guardOutputs lists only violating approvers in violations_mrkdwn', async () => {
    const result = await check(push([approval('alice')], [['c1', 1, 'alice', 'bob']]));
    const outputs = guardOutputs(result.pullRequests, result.blocked, result.violations);
    assert.equal(outputs.violations_mrkdwn, '<https://github.com/alice|alice> (author of #1)');
    assert.match(outputs.blocked_mrkdwn, /bob/);
    assert.equal(guardOutputs(result.pullRequests, result.blocked).violations_mrkdwn, '');
});
