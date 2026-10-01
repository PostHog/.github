import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { checkReleaseApproval, createApi, GuardError, listChangesets } from './guard.mjs';

const repo = 'PostHog/posthog-js';
const ref = 'head-sha';
const API = 'https://api.github.test';

function fakeApi(routes) {
    const fetchImpl = async (url) => {
        const path = url.slice(API.length);
        const [route, page] = path.split('&page=');
        if (!(route in routes)) return new Response(`no route ${path}`, { status: 404 });
        const body = routes[route];
        if (Array.isArray(body) && Array.isArray(body[0])) {
            const n = Number(page || 1);
            const headers = n < body.length ? { link: `<${API}${route}&page=${n + 1}>; rel="next"` } : {};
            return Response.json(body[n - 1], { headers });
        }
        return Response.json(body);
    };
    return createApi({ token: 't', apiUrl: API, fetchImpl });
}

const approval = (login, environment = 'NPM Release', state = 'approved') => ({
    state,
    user: { login },
    environments: [{ name: environment }],
});
const commitsFor = (file) => `/repos/${repo}/commits?sha=${ref}&path=${encodeURIComponent(file)}&per_page=100`;
const pullsFor = (sha) => `/repos/${repo}/commits/${sha}/pulls`;
const prCommits = (n) => `/repos/${repo}/pulls/${n}/commits?per_page=100`;
const pr = (number, login) => ({ number, merged_at: '2026-10-01T00:00:00Z', user: { login } });
const commit = (author, committer = 'web-flow', verified = true) => ({
    sha: 'abcdef0123',
    author: author && { login: author },
    committer: committer && { login: committer },
    commit: { verification: { verified } },
});

function singlePr({ author = 'alice', approvals = [approval('bob')], commits = [commit(author)] } = {}) {
    return {
        [`/repos/${repo}/actions/runs/7/approvals`]: approvals,
        [commitsFor('.changeset/a.md')]: [{ sha: 'c1' }],
        [pullsFor('c1')]: [pr(1, author)],
        [prCommits(1)]: commits,
    };
}

const check = (routes, files = ['.changeset/a.md'], environment = 'NPM Release') =>
    checkReleaseApproval({ api: fakeApi(routes), repo, runId: 7, environment, ref, files });

test('passes when no approver contributed to the release', async () => {
    const result = await check(singlePr());
    assert.deepEqual(result.violations, []);
    assert.deepEqual(result.approvers, ['bob']);
    assert.deepEqual(result.contributors, ['alice']);
});

test('blocks the PR author approving their own release', async () => {
    const result = await check(singlePr({ approvals: [approval('alice')] }));
    assert.deepEqual(result.violations, [{ login: 'alice', reasons: ['author of #1', 'commit in #1'] }]);
});

test('blocks someone who pushed commits to another person\'s PR', async () => {
    const result = await check(singlePr({ commits: [commit('alice'), commit('bob', 'bob')] }));
    assert.deepEqual(result.violations, [{ login: 'bob', reasons: ['commit in #1'] }]);
});

test('matches logins case-insensitively', async () => {
    const result = await check(singlePr({ approvals: [approval('Alice')] }));
    assert.equal(result.violations[0].login, 'Alice');
});

test('blocks when any approval in the run came from a contributor', async () => {
    const result = await check(singlePr({ approvals: [approval('bob'), approval('alice')] }));
    assert.deepEqual(result.violations.map((v) => v.login), ['alice']);
});

test('checks every PR in a batched release, including PRs that edited a changeset', async () => {
    const routes = {
        [`/repos/${repo}/actions/runs/7/approvals`]: [approval('carol')],
        [commitsFor('.changeset/a.md')]: [{ sha: 'c1' }],
        [commitsFor('.changeset/b.md')]: [{ sha: 'c3' }, { sha: 'c2' }],
        [pullsFor('c1')]: [pr(1, 'alice')],
        [pullsFor('c2')]: [pr(2, 'bob')],
        [pullsFor('c3')]: [pr(3, 'carol')],
        [prCommits(1)]: [commit('alice')],
        [prCommits(2)]: [commit('bob')],
        [prCommits(3)]: [commit('carol')],
    };
    const result = await check(routes, ['.changeset/a.md', '.changeset/b.md']);
    assert.deepEqual(result.contributors, ['alice', 'bob', 'carol']);
    assert.deepEqual(result.violations.map((v) => v.login), ['carol']);
});

test('fails closed when a PR has a commit without a verified signature', async () => {
    const routes = singlePr({ commits: [commit('alice'), commit('bob', 'bob', false)] });
    await assert.rejects(check(routes), /abcdef0123 in #1 has no verified signature/);
});

test('fails closed when a PR reaches the 250-commit listing limit', async () => {
    const routes = singlePr();
    routes[prCommits(1)] = [Array(100).fill(commit('alice')), Array(100).fill(commit('alice')), Array(50).fill(commit('alice'))];
    await assert.rejects(check(routes), /#1 has 250 or more commits/);
});

test('follows pagination of PR commits', async () => {
    const routes = singlePr();
    routes[prCommits(1)] = [[commit('alice')], [commit('bob')]];
    const result = await check(routes);
    assert.deepEqual(result.violations.map((v) => v.login), ['bob']);
});

test('ignores approvals that are rejected or for other environments', async () => {
    const approvals = [approval('alice', 'S3 Upload'), approval('alice', 'NPM Release', 'rejected'), approval('bob')];
    const result = await check(singlePr({ approvals }));
    assert.deepEqual(result.approvers, ['bob']);
    assert.deepEqual(result.violations, []);
});

test('fails closed when the run has no approval for the environment', async () => {
    await assert.rejects(check(singlePr({ approvals: [approval('bob', 'Other')] })), GuardError);
});

test('fails closed when a changeset commit is not from a merged PR', async () => {
    const routes = singlePr();
    routes[pullsFor('c1')] = [{ ...pr(1, 'alice'), merged_at: null }];
    await assert.rejects(check(routes), /does not belong to a merged PR/);
});

test('fails closed when a changeset has no commits', async () => {
    const routes = singlePr();
    routes[commitsFor('.changeset/a.md')] = [];
    await assert.rejects(check(routes), /has no commits/);
});

test('fails closed when there are no changesets', async () => {
    await assert.rejects(check(singlePr(), []), /No changesets found/);
});

test('surfaces GitHub API errors', async () => {
    const routes = singlePr();
    delete routes[prCommits(1)];
    await assert.rejects(check(routes), /GitHub API 404/);
});

test('lists pending changesets across directories', (t) => {
    const cwd = mkdtempSync(join(tmpdir(), 'release-approval-guard-'));
    t.after(() => rmSync(cwd, { recursive: true, force: true }));
    for (const file of ['.changeset/b.md', '.changeset/a.md', '.changeset/README.md', '.changeset/config.json', 'cli/.sampo/changesets/c.md']) {
        mkdirSync(join(cwd, file, '..'), { recursive: true });
        writeFileSync(join(cwd, file), '');
    }
    assert.deepEqual(listChangesets(['.changeset', 'cli/.sampo/changesets/', 'missing'], cwd), [
        '.changeset/a.md',
        '.changeset/b.md',
        'cli/.sampo/changesets/c.md',
    ]);
});
