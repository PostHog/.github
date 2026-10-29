"""Offline renderer and trusted workflow_run publisher entry point."""

import argparse
import os
import sys
from pathlib import Path

# Support absolute-path invocation from a composite action without installing the project.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.report.bundle import collect, compare_bundle, encode_bundle, load_bundle
from benchmarks.report.compare import SOURCE_STATUSES, compare
from benchmarks.report.github import GitHub
from benchmarks.report.publish import Publisher
from benchmarks.report.render import json_text, render, write_outputs
from benchmarks.report.validate import MAX_DOCUMENT_BYTES, InvalidResult, bounded_json, load_result, require


def main(argv=None, environ=None) -> int:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("render", "collect", "publish"), default="render")
    parser.add_argument("--source-status", choices=sorted(SOURCE_STATUSES | {""}), default="")
    parser.add_argument("--base-results", default="")
    parser.add_argument("--head-results", default="")
    parser.add_argument("--bundle-results", default="")
    parser.add_argument("--shard-manifest", default="")
    parser.add_argument("--shard-directory", default="")
    parser.add_argument("--output-directory", default="benchmark-report")
    parser.add_argument("--workflow-path", default="")
    parser.add_argument("--artifact-name", default="")
    parser.add_argument("--report-key", default="")
    parser.add_argument("--sdk", default="")
    args = parser.parse_args(argv)
    try:
        if args.mode == "collect":
            require(not (args.base_results or args.head_results or args.bundle_results), "Ambiguous collection inputs")
            require(args.shard_manifest and args.shard_directory, "Manifest and shard directory are required")
            bundle, revisions = collect(args.shard_manifest, args.shard_directory)
            comparison = compare_bundle(bundle, source_status=args.source_status or None)
            report = render(comparison, repository=env.get("GITHUB_REPOSITORY"), run_id=env.get("GITHUB_RUN_ID"))
            directory = Path(args.output_directory)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "bundle.json").write_bytes(encode_bundle(bundle))
            (directory / "revisions.json").write_text(json_text(revisions), encoding="utf-8")
        elif args.mode == "render":
            require(not (args.shard_manifest or args.shard_directory), "Shard inputs require collect mode")
            if args.bundle_results:
                require(not (args.base_results or args.head_results), "Ambiguous rendering inputs")
                comparison = compare_bundle(load_bundle(args.bundle_results), source_status=args.source_status or None)
            else:
                require(args.base_results and args.head_results, "Both result paths are required")
                comparison = compare(
                    load_result(args.base_results),
                    load_result(args.head_results),
                    source_status=args.source_status or None,
                )
            report = render(comparison, repository=env.get("GITHUB_REPOSITORY"), run_id=env.get("GITHUB_RUN_ID"))
        else:
            require(
                not (
                    args.base_results
                    or args.head_results
                    or args.bundle_results
                    or args.shard_manifest
                    or args.shard_directory
                ),
                "Publish accepts only verified artifact inputs",
            )
            require(env.get("GITHUB_EVENT_NAME") == "workflow_run", "Publish requires a workflow_run event")
            require(bool(env.get("GITHUB_EVENT_PATH")), "Missing GitHub event file")
            with Path(env["GITHUB_EVENT_PATH"]).open("rb") as stream:
                event = bounded_json(stream.read(MAX_DOCUMENT_BYTES + 1), text_limit=65536)
            publisher = Publisher(
                GitHub(env.get("GH_TOKEN", "")),
                env.get("GITHUB_REPOSITORY", ""),
                args.workflow_path,
                args.artifact_name,
                args.report_key,
                args.sdk,
            )
            comparison, report = publisher.publish(event)
        write_outputs(args.output_directory, comparison, report, env)
        return 1 if comparison.get("execution_failed") else 0
    except (InvalidResult, OSError, ValueError) as exc:
        # Never print paths, API responses, raw logs or arbitrary exception text.
        reason = str(exc) if isinstance(exc, InvalidResult) else "Report input or output could not be processed"
        report = f"## SDK performance\n\n**Reporting failed:** {reason}.\n"
        try:
            write_outputs(args.output_directory, {"comparison_version": 1, "error": reason}, report, env)
        except (OSError, ValueError):
            pass
        print(reason, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
