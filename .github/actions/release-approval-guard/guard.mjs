#!/usr/bin/env node
import { appendFileSync, readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export class GuardError extends Error {}

export function createApi({ token, apiUrl = 'https://api.github.com', fetchImpl = fetch, retryDelayMs = 2000 }) {
    return {
        async get(path) {
            for (let attempt = 1; ; attempt++) {
                const res = await fetchImpl(`${apiUrl}${path}`, {
                    headers: {
                        Accept: 'application/vnd.github+json',
                        Authorization: `Bearer ${token}`,
                        'X-GitHub-Api-Version': '2022-11-28',
                    },
                });
                if (res.ok) return res.json();
                if (res.status >= 500 && attempt < 3) {
                    await new Promise((resolve) => setTimeout(resolve, retryDelayMs * attempt));
                    continue;
                }
                throw new GuardError(`GitHub API ${res.status} for ${path}: ${await res.text()}`);
            }
        },
    };
}

export async function approversFor(api, repo, runId, environment) {
    const approvals = await api.get(`/repos/${repo}/actions/runs/${runId}/approvals`);
    const logins = approvals
        .filter((a) => a.state === 'approved' && a.environments.some((e) => e.name === environment))
        .map((a) => a.user.login);
    return [...new Set(logins)].sort();
}

export async function pushedPrs(api, repo, before, after) {
    if (!before || /^0+$/.test(before)) {
        throw new GuardError(`The push has no previous commit (before=${before}), so its PRs cannot be found.`);
    }
    const compare = await api.get(`/repos/${repo}/compare/${before}...${after}`);
    if (compare.commits.length === 0 || compare.commits.length < compare.total_commits) {
        throw new GuardError(`Cannot list all ${compare.total_commits} commits between ${before} and ${after}.`);
    }
    const numbers = new Set();
    const mergedPerCommit = await Promise.all(
        compare.commits.map(async (commit) => ({
            sha: commit.sha,
            merged: (await api.get(`/repos/${repo}/commits/${commit.sha}/pulls`)).filter((pr) => pr.merged_at),
        })),
    );
    for (const { sha, merged } of mergedPerCommit) {
        if (merged.length === 0) {
            throw new GuardError(`Commit ${sha} in the push does not belong to a merged PR.`);
        }
        for (const pr of merged) numbers.add(pr.number);
    }
    return Promise.all([...numbers].sort((a, b) => a - b).map((n) => api.get(`/repos/${repo}/pulls/${n}`)));
}

export function blockedApprovers(prs) {
    const blocked = new Map();
    const block = (login, reason) => {
        if (!login || login.endsWith('[bot]')) return;
        const key = login.toLowerCase();
        if (!blocked.has(key)) blocked.set(key, { login, reasons: [] });
        blocked.get(key).reasons.push(reason);
    };
    for (const pr of prs) {
        block(pr.user?.login, `author of #${pr.number}`);
        block(pr.merged_by?.login, `queued #${pr.number}`);
    }
    return [...blocked.values()].sort((a, b) => a.login.toLowerCase().localeCompare(b.login.toLowerCase()));
}

const escapeMrkdwn = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function blockedMrkdwn(blocked) {
    return blocked
        .map(({ login, reasons }) => `<https://github.com/${login}|${login}> (${reasons.join(', ')})`)
        .join(', ');
}

// Slack caps a section field at 2000 characters.
export function prsMrkdwn(prs, maxLength = 1800) {
    const lines = [];
    let length = 0;
    for (const pr of prs) {
        const line = `<${pr.html_url}|#${pr.number}> ${escapeMrkdwn(pr.title ?? '')}`.trimEnd();
        if (length + line.length > maxLength) {
            lines.push(`…and ${prs.length - lines.length} more`);
            break;
        }
        lines.push(line);
        length += line.length + 1;
    }
    return lines.join('\n');
}

export function guardOutputs(prs, blocked, violations = []) {
    return {
        blocked_logins: blocked.map((b) => b.login).join(','),
        blocked_mrkdwn: blockedMrkdwn(blocked),
        violations_mrkdwn: blockedMrkdwn(violations),
        prs_mrkdwn: prsMrkdwn(prs),
        prs: prs.map((pr) => pr.number).join(','),
    };
}

const EMPTY_OUTPUTS = guardOutputs([], []);

export function writeOutputs(file, outputs) {
    if (!file) return;
    for (const [name, value] of Object.entries(outputs)) {
        const delimiter = `ghadelimiter_${randomUUID()}`;
        appendFileSync(file, `${name}<<${delimiter}\n${value}\n${delimiter}\n`);
    }
}

export async function checkReleaseApproval({ api, repo, runId, environment, before, after }) {
    const approvers = await approversFor(api, repo, runId, environment);
    if (approvers.length === 0) {
        throw new GuardError(
            `No approval for the "${environment}" environment in this run. ` +
                'Check the environment input and that the environment has required reviewers.',
        );
    }
    const pullRequests = await pushedPrs(api, repo, before, after);
    const blocked = blockedApprovers(pullRequests);
    const approverSet = new Set(approvers.map((login) => login.toLowerCase()));
    const violations = blocked.filter((b) => approverSet.has(b.login.toLowerCase()));
    return { approvers, pullRequests, blocked, violations };
}

function pushContext(env) {
    const { before, after } = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, 'utf8'));
    return { api: createApi({ token: env.GH_TOKEN, apiUrl: env.GITHUB_API_URL }), repo: env.GITHUB_REPOSITORY, before, after };
}

async function runReport(env) {
    try {
        const { api, repo, before, after } = pushContext(env);
        const prs = await pushedPrs(api, repo, before, after);
        const outputs = guardOutputs(prs, blockedApprovers(prs));
        writeOutputs(env.GITHUB_OUTPUT, outputs);
        console.log(`PRs in the push: ${outputs.prs}`);
        console.log(`Excluded from approving: ${outputs.blocked_logins || 'none'}`);
    } catch (e) {
        console.log(`::warning title=Release approval guard::${e.message}`);
        writeOutputs(env.GITHUB_OUTPUT, EMPTY_OUTPUTS);
    }
}

async function runEnforce(env) {
    const environment = env.ENVIRONMENT?.trim();
    if (!environment) throw new GuardError('The environment input is required.');
    const { api, repo, before, after } = pushContext(env);

    const result = await checkReleaseApproval({ api, repo, runId: env.GITHUB_RUN_ID, environment, before, after });
    writeOutputs(env.GITHUB_OUTPUT, guardOutputs(result.pullRequests, result.blocked, result.violations));

    console.log(`PRs in the push: ${result.pullRequests.map((p) => `#${p.number}`).join(', ')}`);
    console.log(`Approvers of "${environment}": ${result.approvers.join(', ')}`);

    if (result.violations.length > 0) {
        const lines = result.violations.map((v) => `${v.login} (${v.reasons.join(', ')})`);
        console.log(
            `::error title=Release approved by its author::${lines.join('; ')} approved a release ` +
                'triggered by their own PR. Re-run the failed jobs to request a new approval, ' +
                'and have a different approver approve it.',
        );
        if (env.GITHUB_STEP_SUMMARY) {
            appendFileSync(
                env.GITHUB_STEP_SUMMARY,
                `### Release blocked\n\nApproved by the author or enqueuer of a PR in this push:\n\n${lines
                    .map((l) => `- ${l}`)
                    .join('\n')}\n\nRe-run the failed jobs to request a new approval. A different approver must approve it.\n`,
            );
        }
        process.exit(1);
    }
    console.log('✓ No approver authored or queued a PR in this push.');
}

async function main() {
    const env = process.env;
    const mode = env.MODE?.trim() || 'enforce';
    if (mode !== 'enforce' && mode !== 'report') {
        throw new GuardError(`Unknown mode "${mode}". Use "enforce" or "report".`);
    }
    if (env.GITHUB_EVENT_NAME !== 'push') {
        console.log(
            `Not a push (${env.GITHUB_EVENT_NAME}), so GitHub's "Prevent self-review" already excludes whoever triggered the run.`,
        );
        writeOutputs(env.GITHUB_OUTPUT, EMPTY_OUTPUTS);
        return;
    }
    await (mode === 'report' ? runReport(env) : runEnforce(env));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    main().catch((e) => {
        console.log(`::error title=Release approval guard::${e.message}`);
        process.exit(1);
    });
}
