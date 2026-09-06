import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const script = fileURLToPath(new URL('./check-changeset-coverage.mjs', import.meta.url));
const packages = {
    'packages/browser': 'posthog-js',
    'packages/rrweb/rrweb': '@posthog/rrweb',
    'packages/rrweb/types': '@posthog/rrweb-types',
    'packages/rrweb-extra': 'rrweb-extra',
    'packages/node': 'posthog-node',
};
const mapping = { releasePackagePaths: { 'packages/rrweb/': 'posthog-js' } };

function report(t, { files = [], declared = [], config } = {}) {
    const cwd = realpathSync(mkdtempSync(join(tmpdir(), 'changeset-hygiene-')));
    t.after(() => rmSync(cwd, { recursive: true, force: true }));
    function write(path, content) {
        mkdirSync(dirname(join(cwd, path)), { recursive: true });
        writeFileSync(join(cwd, path), content);
    }
    const git = (...args) => execFileSync('git', args, { cwd, stdio: 'pipe' });
    git('init', '-b', 'main');
    git('config', 'user.email', 'test@example.com');
    git('config', 'user.name', 'Test');
    git('config', 'commit.gpgsign', 'false');
    git('config', 'core.hooksPath', '/dev/null');
    // Stub only workspace discovery. Exercise the real script, git diff and frontmatter parser.
    write(
        'bin/pnpm',
        `#!/bin/sh\nprintf '%s\\n' '${JSON.stringify(
            Object.entries(packages).map(([path, name]) => ({ path: join(cwd, path), name })),
        )}'\n`,
    );
    execFileSync('chmod', ['+x', join(cwd, 'bin/pnpm')]);
    if (config) write('.changeset/hygiene.json', JSON.stringify(config));
    git('add', '.');
    git('commit', '-m', 'base');
    git('update-ref', 'refs/remotes/origin/main', 'HEAD');
    for (const file of files) write(file, 'changed\n');
    if (declared.length) {
        write(
            '.changeset/change.md',
            `---\n${declared.map((n) => `'${n}': patch`).join('\n')}\n---\n\nChange\n`,
        );
    }
    git('add', '.');
    git('commit', '--allow-empty', '-m', 'change');
    return execFileSync(process.execPath, [script], {
        cwd,
        encoding: 'utf8',
        env: { ...process.env, BASE_REF: 'main', PATH: `${cwd}/bin:${process.env.PATH}` },
    });
}

test('rrweb-only changes are covered by a browser changeset', (t) => {
    assert.equal(
        report(t, {
            files: ['packages/rrweb/rrweb/src/index.ts', 'packages/rrweb/types/src/index.ts'],
            declared: ['posthog-js'],
            config: mapping,
        }),
        'body=\n',
    );
});

test('rrweb changes without a changeset request only the browser package', (t) => {
    const body = report(t, { files: ['packages/rrweb/rrweb/src/index.ts'], config: mapping });
    assert.match(body, /`posthog-js` is modified but this PR has no changeset/);
    assert.doesNotMatch(body, /@posthog\/rrweb/);
});

test('an rrweb changeset does not satisfy the browser release requirement', (t) => {
    const body = report(t, {
        files: ['packages/rrweb/rrweb/src/index.ts'],
        declared: ['@posthog/rrweb'],
        config: mapping,
    });
    assert.match(body, /"posthog-js": patch/);
    assert.match(
        body,
        /\*\*Declared in a changeset but no source files modified:\*\*\n- `@posthog\/rrweb`/,
    );
});

test('unmapped packages still need their own changesets', (t) => {
    const body = report(t, {
        files: ['packages/rrweb/rrweb/src/index.ts', 'packages/node/src/index.ts'],
        declared: ['posthog-js'],
        config: mapping,
    });
    assert.match(body, /`posthog-node` is modified but not declared/);
    assert.doesNotMatch(body, /@posthog\/rrweb/);
});

test('path matching respects directory boundaries', (t) => {
    const body = report(t, { files: ['packages/rrweb-extra/src/index.ts'], config: mapping });
    assert.match(body, /`rrweb-extra` is modified/);
    assert.doesNotMatch(body, /posthog-js/);
});

test('the most specific mapping wins, with or without trailing slashes', (t) => {
    assert.equal(
        report(t, {
            files: ['packages/rrweb/rrweb/src/index.ts', 'packages/rrweb/types/src/index.ts'],
            declared: ['posthog-js', 'posthog-node'],
            config: {
                releasePackagePaths: {
                    'packages/rrweb': 'posthog-js',
                    'packages/rrweb/types/': 'posthog-node',
                },
            },
        }),
        'body=\n',
    );
});

test('changelog and manifest changes remain ignored under mapped paths', (t) => {
    assert.equal(
        report(t, {
            files: ['packages/rrweb/rrweb/CHANGELOG.md', 'packages/rrweb/types/package.json'],
            config: mapping,
        }),
        'body=\n',
    );
});

test('no config preserves normal workspace coverage', (t) => {
    assert.equal(
        report(t, {
            files: ['packages/rrweb/rrweb/src/index.ts'],
            declared: ['@posthog/rrweb'],
        }),
        'body=\n',
    );
});

test('transitive re-export config still works', (t) => {
    assert.equal(
        report(t, {
            files: ['packages/node/src/index.ts'],
            declared: ['posthog-node', 'posthog-js'],
            config: { transitiveReExports: { 'posthog-node': ['posthog-js'] } },
        }),
        'body=\n',
    );
});

test('browser and bundled source changes share one changeset', (t) => {
    assert.equal(
        report(t, {
            files: ['packages/browser/src/index.ts', 'packages/rrweb/rrweb/src/index.ts'],
            declared: ['posthog-js'],
            config: mapping,
        }),
        'body=\n',
    );
});

for (const [path, target] of [
    ['packages/rrweb/', 'typo'],
    ['', 'posthog-js'],
    ['../packages/rrweb', 'posthog-js'],
]) {
    test(`invalid mapping ${path} -> ${target} keeps workspace coverage`, (t) => {
        assert.equal(
            report(t, {
                files: ['packages/rrweb/rrweb/src/index.ts'],
                declared: ['@posthog/rrweb'],
                config: { releasePackagePaths: { [path]: target } },
            }),
            'body=\n',
        );
    });
}

test('browser changesets without source changes are still reported as extra', (t) => {
    assert.match(
        report(t, { declared: ['posthog-js'], config: mapping }),
        /Changeset declares `posthog-js` but no source files in that package changed/,
    );
});
