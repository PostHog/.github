# Pinned Semgrep registry snapshots

Vendored copies of the Semgrep registry packs used by CI across the org,
fetched anonymously from `https://semgrep.dev/c/<id>`. Scan workflows run
against these files instead of live `p/...` configs so that registry-side
rule changes can never break CI until a snapshot update is reviewed and
merged here.

- `sources.json` maps each snapshot file to the registry config(s) it pins.
- Every `*.yaml` file is generated — do not edit by hand. Refresh with
  `python3 .github/scripts/semgrep_registry.py sync`, run inside the pinned
  semgrep container image (which ships the script's ruamel.yaml dependency
  and keeps the output byte-stable across environments):

  ```bash
  docker run --rm -v "$PWD:/src" -w /src \
    "$(grep -om1 'semgrep/semgrep:[^ ]*' .github/workflows/semgrep-tests.yml)" \
    python3 .github/scripts/semgrep_registry.py sync
  ```

  The `semgrep-registry-update` workflow does this on a schedule and opens a
  PR with a diff summary and a dry run against critical repos.

The rules remain the property of their upstream authors (Semgrep, Trail of
Bits, and other registry contributors) under their respective licenses; each
rule's `metadata` carries its `source` / `license` fields where upstream
provides them.
