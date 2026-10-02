#!/usr/bin/env node
import { appendFileSync, readFileSync } from 'node:fs';
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

// Approvals aren't tied to a run attempt, so every approval in the run counts.
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
    for (const commit of compare.commits) {
        const merged = (await api.get(`/repos/${repo}/commits/${commit.sha}/pulls`)).filter((pr) => pr.merged_at);
        if (merged.length === 0) {
            throw new GuardError(`Commit ${commit.sha} in the push does not belong to a merged PR.`);
        }
        for (const pr of merged) numbers.add(pr.number);
    }
    return Promise.all([...numbers].sort((a, b) => a - b).map((n) => api.get(`/repos/${repo}/pulls/${n}`)));
}

export async function checkReleaseApproval({ api, repo, runId, environment, before, after }) {
    const approvers = await approversFor(api, repo, runId, environment);
    if (approvers.length === 0) {
        throw new GuardError(
            `No approval for the "${environment}" environment in this run. ` +
                'Check the environment input and that the environment has required reviewers.',
        );
    }
    const prs = await pushedPrs(api, repo, before, after);
    const blocked = new Map();
    const block = (login, reason) => {
        if (!login) return;
        const key = login.toLowerCase();
        if (!blocked.has(key)) blocked.set(key, []);
        blocked.get(key).push(reason);
    };
    for (const pr of prs) {
        block(pr.user?.login, `author of #${pr.number}`);
        block(pr.merged_by?.login, `merged #${pr.number}`);
    }
    const violations = approvers
        .filter((login) => blocked.has(login.toLowerCase()))
        .map((login) => ({ login, reasons: blocked.get(login.toLowerCase()) }));
    return { approvers, prs: prs.map((pr) => pr.number), violations };
}

async function main() {
    const env = process.env;
    if (env.GITHUB_EVENT_NAME !== 'push') {
        console.log(
            `Not a push (${env.GITHUB_EVENT_NAME}), so GitHub's "Prevent self-review" already excludes whoever triggered the run.`,
        );
        return;
    }
    const environment = env.ENVIRONMENT?.trim();
    if (!environment) throw new GuardError('The environment input is required.');
    const event = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, 'utf8'));

    const result = await checkReleaseApproval({
        api: createApi({ token: env.GH_TOKEN, apiUrl: env.GITHUB_API_URL }),
        repo: env.GITHUB_REPOSITORY,
        runId: env.GITHUB_RUN_ID,
        environment,
        before: event.before,
        after: event.after,
    });

    console.log(`PRs in the push: ${result.prs.map((n) => `#${n}`).join(', ')}`);
    console.log(`Approvers of "${environment}": ${result.approvers.join(', ')}`);

    if (result.violations.length > 0) {
        const lines = result.violations.map((v) => `${v.login} (${v.reasons.join(', ')})`);
        console.log(
            `::error title=Release approved by its author::${lines.join('; ')} approved a release ` +
                'triggered by their own PR. Another approver must approve a new run of this ' +
                'workflow. A re-run of this run can still see the earlier approval and fail again.',
        );
        if (env.GITHUB_STEP_SUMMARY) {
            appendFileSync(
                env.GITHUB_STEP_SUMMARY,
                `### Release blocked\n\nApproved by the author or merger of a PR in this push:\n\n${lines
                    .map((l) => `- ${l}`)
                    .join('\n')}\n`,
            );
        }
        process.exit(1);
    }
    console.log('✓ No approver authored or merged a PR in this push.');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    main().catch((e) => {
        console.log(`::error title=Release approval guard::${e.message}`);
        process.exit(1);
    });
}
