#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { appendFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// GitHub's committer for web UI commits and squash merges.
const IGNORED_LOGINS = new Set(['web-flow']);

export class GuardError extends Error {}

export function listChangesets(dirs, cwd = process.cwd()) {
    const files = [];
    for (const dir of dirs) {
        if (!existsSync(join(cwd, dir))) continue;
        for (const name of readdirSync(join(cwd, dir))) {
            if (name.endsWith('.md') && name.toLowerCase() !== 'readme.md') {
                files.push(`${dir.replace(/\/+$/, '')}/${name}`);
            }
        }
    }
    return files.sort();
}

export function createApi({ token, apiUrl = 'https://api.github.com', fetchImpl = fetch }) {
    async function request(url) {
        const res = await fetchImpl(url, {
            headers: {
                Accept: 'application/vnd.github+json',
                Authorization: `Bearer ${token}`,
                'X-GitHub-Api-Version': '2022-11-28',
            },
        });
        if (!res.ok) {
            throw new GuardError(`GitHub API ${res.status} for ${url}: ${await res.text()}`);
        }
        return res;
    }

    return {
        async get(path) {
            return (await request(`${apiUrl}${path}`)).json();
        },
        async list(path) {
            const items = [];
            let url = `${apiUrl}${path}`;
            while (url) {
                const res = await request(url);
                items.push(...(await res.json()));
                url = res.headers.get('link')?.match(/<([^>]+)>;\s*rel="next"/)?.[1];
            }
            return items;
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

export async function contributorsFor(api, repo, ref, files) {
    const contributors = new Map();
    const add = (login, reason) => {
        if (!login || IGNORED_LOGINS.has(login)) return;
        const key = login.toLowerCase();
        if (!contributors.has(key)) contributors.set(key, { login, reasons: new Set() });
        contributors.get(key).reasons.add(reason);
    };

    const seenPrs = new Set();
    for (const file of files) {
        const commits = await api.list(
            `/repos/${repo}/commits?sha=${encodeURIComponent(ref)}&path=${encodeURIComponent(file)}&per_page=100`,
        );
        if (commits.length === 0) {
            throw new GuardError(`${file} has no commits at ${ref}. Is it committed?`);
        }
        for (const commit of commits) {
            const prs = (await api.get(`/repos/${repo}/commits/${commit.sha}/pulls`)).filter(
                (pr) => pr.merged_at,
            );
            if (prs.length === 0) {
                throw new GuardError(
                    `Commit ${commit.sha} changed ${file} but does not belong to a merged PR.`,
                );
            }
            for (const pr of prs) {
                if (seenPrs.has(pr.number)) continue;
                seenPrs.add(pr.number);
                add(pr.user?.login, `author of #${pr.number}`);
                for (const c of await api.list(`/repos/${repo}/pulls/${pr.number}/commits?per_page=100`)) {
                    add(c.author?.login, `commit in #${pr.number}`);
                    add(c.committer?.login, `commit in #${pr.number}`);
                }
            }
        }
    }
    return contributors;
}

export async function checkReleaseApproval({ api, repo, runId, environment, ref, files }) {
    if (files.length === 0) {
        throw new GuardError('No changesets found. Check the changeset-dirs input.');
    }
    const approvers = await approversFor(api, repo, runId, environment);
    if (approvers.length === 0) {
        throw new GuardError(
            `No approval for the "${environment}" environment in this run. ` +
                'Check the environment input and that the environment has required reviewers.',
        );
    }
    const contributors = await contributorsFor(api, repo, ref, files);
    const violations = approvers
        .filter((login) => contributors.has(login.toLowerCase()))
        .map((login) => ({ login, reasons: [...contributors.get(login.toLowerCase()).reasons] }));
    return { approvers, contributors: [...contributors.values()].map((c) => c.login).sort(), violations };
}

async function main() {
    const env = process.env;
    const environment = env.ENVIRONMENT?.trim();
    if (!environment) throw new GuardError('The environment input is required.');
    const dirs = (env.CHANGESET_DIRS || '.changeset')
        .split('\n')
        .map((d) => d.trim())
        .filter(Boolean);
    const ref = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const files = listChangesets(dirs);

    const result = await checkReleaseApproval({
        api: createApi({ token: env.GH_TOKEN, apiUrl: env.GITHUB_API_URL }),
        repo: env.GITHUB_REPOSITORY,
        runId: env.GITHUB_RUN_ID,
        environment,
        ref,
        files,
    });

    console.log(`Changesets at ${ref}:\n  ${files.join('\n  ')}`);
    console.log(`Approvers of "${environment}": ${result.approvers.join(', ')}`);
    console.log(`Contributors to released PRs: ${result.contributors.join(', ')}`);

    if (result.violations.length > 0) {
        const lines = result.violations.map((v) => `${v.login} (${v.reasons.join(', ')})`);
        console.log(
            `::error title=Release approved by a contributor::${lines.join('; ')} approved a release ` +
                'that contains their own changes. Another approver must approve a new run of this ' +
                'workflow. A re-run of this run can still see the earlier approval and fail again.',
        );
        if (env.GITHUB_STEP_SUMMARY) {
            appendFileSync(
                env.GITHUB_STEP_SUMMARY,
                `### Release blocked\n\nApproved by a contributor to this release:\n\n${lines
                    .map((l) => `- ${l}`)
                    .join('\n')}\n`,
            );
        }
        process.exit(1);
    }
    console.log('✓ No approver contributed to the released changes.');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    main().catch((e) => {
        console.log(`::error title=Release approval guard::${e.message}`);
        process.exit(1);
    });
}
