"""Shard collection, independent environments and data-only publication regressions."""

import copy
import html
import json
import os
import stat
import zipfile

import pytest

from benchmarks.report import bundle as bundle_module
from benchmarks.report import cli
from benchmarks.report.bundle import (
    collect,
    compare_bundle,
    encode_bundle,
    load_bundle,
    validate_bundle,
    validate_manifest,
)
from benchmarks.report.github import read_archive
from benchmarks.report.render import MAX_REPORT_BYTES, render
from benchmarks.report.validate import MAX_DOCUMENT_BYTES, InvalidResult, bounded_json
from tests.test_benchmark_github import archive
from tests.test_benchmark_publish import BASE, HEAD, TARGET, FakeGitHub, bot_comment, publisher
from tests.test_benchmark_report import pair


def bundle_fixture(sdk="node", count=2):
    documents = pair(sdk)
    manifest = {"manifest_version": 1, "sdk": documents[0]["sdk"], "shards": []}
    shards = []
    for index in range(count):
        identity = f"shard-{index}"
        shard = {"id": identity}
        for role, original in zip(("base", "head"), documents):
            doc = copy.deepcopy(original)
            if count == 2:
                doc["benchmarks"] = doc["benchmarks"][index::count]
            else:
                doc["benchmarks"] = [doc["benchmarks"][0]]
                doc["benchmarks"][0]["id"] = f"workload-{index}"
            doc["execution"].update(shard_id=identity, pair_id=f"pair-{index}", cpu=f"CPU-{index}")
            shard[role] = doc
        manifest["shards"].append({"id": identity, "workloads": [w["id"] for w in shard["head"]["benchmarks"]]})
        shards.append(shard)
    return {"bundle_version": 1, "manifest": manifest, "shards": shards}


def collection_fixture(tmp_path, sdk="node"):
    bundle = bundle_fixture(sdk)
    root = tmp_path / "shards"
    root.mkdir()
    manifest = tmp_path / "shards.json"
    manifest.write_text(json.dumps(bundle["manifest"]))
    for shard in bundle["shards"]:
        directory = root / f"benchmark-shard-{shard['id']}"
        directory.mkdir()
        for role in ("base", "head"):
            (directory / f"{role}.json").write_text(json.dumps(shard[role]))
        (directory / "revisions.json").write_text(json.dumps({"base": BASE, "head": HEAD, "target": TARGET}))
    return manifest, root, bundle


@pytest.mark.parametrize("sdk", ["node", "python"])
def test_collect_preserves_documents_and_offline_bundle_render(tmp_path, monkeypatch, sdk):
    manifest, root, expected = collection_fixture(tmp_path, sdk)
    monkeypatch.setattr(cli, "GitHub", lambda *a: pytest.fail("Offline collection constructed GitHub client"))
    output = tmp_path / "out"
    args = [
        "--mode",
        "collect",
        "--shard-manifest",
        str(manifest),
        "--shard-directory",
        str(root),
        "--output-directory",
        str(output),
    ]
    assert cli.main(args, {"GH_TOKEN": "unused"}) == 0
    bundle_path = output / "bundle.json"
    assert load_bundle(bundle_path) == expected
    assert json.loads((output / "revisions.json").read_text()) == {"base": BASE, "head": HEAD, "target": TARGET}
    comparison = json.loads((output / "comparison.json").read_text())
    assert comparison["comparison_version"] == 2
    assert all(row["shard_id"] == comparison["workload_shards"][row["workload"]] for row in comparison["rows"])
    assert all(not shard["compatibility"] for shard in comparison["shards"].values())
    assert any("change_percent" in row for row in comparison["rows"])
    report = (output / "report.md").read_text()
    decoded = html.unescape(report).replace("\u200b", "")
    assert "CPU-0" in decoded and "CPU-1" in decoded and "Shard: shard-1" in decoded
    assert cli.main(["--bundle-results", str(bundle_path), "--output-directory", str(tmp_path / "render")], {}) == 0
    assert (tmp_path / "render/report.md").read_text() == report


@pytest.mark.parametrize("field", ["cpu", "runtime_version", "runner_image", "measurement_tools"])
def test_environment_mismatch_only_suppresses_affected_pair(field):
    bundle = bundle_fixture()
    bundle["shards"][1]["head"]["execution"][field] = (
        {"tool": "different"} if field == "measurement_tools" else "different"
    )
    result = compare_bundle(bundle)
    assert not result["shards"]["shard-0"]["compatibility"]
    assert result["shards"]["shard-1"]["compatibility"]
    assert any("change_percent" in row for row in result["rows"] if row["shard_id"] == "shard-0")
    assert all("change_percent" not in row for row in result["rows"] if row["shard_id"] == "shard-1")


def test_shard_build_provenance_is_retained_not_compared_across_runners():
    bundle = bundle_fixture()
    for index, shard in enumerate(bundle["shards"]):
        for role in ("base", "head"):
            shard[role]["execution"]["build_digest"] = str(index + (1 if role == "base" else 3)) * 64
    result = compare_bundle(bundle)
    for shard in bundle["shards"]:
        assert not result["shards"][shard["id"]]["compatibility"]
        for role in ("base", "head"):
            assert result["shards"][shard["id"]]["context"][role]["execution"] == shard[role]["execution"]


@pytest.mark.parametrize(
    "status", ["failure", "cancelled", "timed_out", "action_required", "neutral", "skipped", "stale", "startup_failure"]
)
def test_collect_failure_preserves_raw_data_and_suppresses_every_shard(tmp_path, status):
    manifest, root, expected = collection_fixture(tmp_path)
    output = tmp_path / "out"
    assert (
        cli.main(
            [
                "--mode",
                "collect",
                "--shard-manifest",
                str(manifest),
                "--shard-directory",
                str(root),
                "--source-status",
                status,
                "--output-directory",
                str(output),
            ],
            {},
        )
        == 1
    )
    assert load_bundle(output / "bundle.json") == expected
    result = json.loads((output / "comparison.json").read_text())
    assert result["execution_failed"] and result["source_execution_status"] == status
    assert all("change_percent" not in row and "base_median" not in row for row in result["rows"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(manifest_version=True),
        lambda m: m.update(manifest_version=2),
        lambda m: m.update(extra="unexpected"),
        lambda m: m.update(sdk=""),
        lambda m: m.update(shards=[]),
        lambda m: m.update(shards=m["shards"] * 9),
        lambda m: m["shards"].append(copy.deepcopy(m["shards"][0])),
        lambda m: m["shards"][0].update(id="../escape"),
        lambda m: m["shards"][0].update(id="Uppercase"),
        lambda m: m["shards"][0].update(id="a" * 65),
        lambda m: m["shards"][0].update(id="name\n"),
        lambda m: m["shards"][0].update(id=[]),
        lambda m: m["shards"][0].update(workloads=[]),
        lambda m: m["shards"][0]["workloads"].append(m["shards"][0]["workloads"][0]),
        lambda m: m["shards"][1]["workloads"].append(m["shards"][0]["workloads"][0]),
        lambda m: m["shards"][0]["workloads"].append({}),
    ],
)
def test_manifest_rejects_invalid_partition(mutation):
    manifest = bundle_fixture()["manifest"]
    mutation(manifest)
    with pytest.raises(InvalidResult):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.update(bundle_version=True),
        lambda b: b.update(extra="unexpected"),
        lambda b: b["shards"].pop(),
        lambda b: b["shards"].append(copy.deepcopy(b["shards"][0])),
        lambda b: b["shards"][1].update(id=b["shards"][0]["id"]),
        lambda b: b["shards"][1].update(id="undeclared"),
        lambda b: b["shards"][1].update(extra={}),
        lambda b: b["shards"][1]["base"]["benchmarks"].pop(),
        lambda b: b["shards"][1]["head"]["benchmarks"].pop(),
        lambda b: b["shards"][1]["head"].update(sdk="other"),
        lambda b: b["shards"][1]["head"]["revision"].update(role="base"),
        lambda b: b["shards"][1]["head"]["revision"].update(commit="d" * 40),
        lambda b: b["shards"][1]["base"]["revision"].update(dirty=True),
        lambda b: b["shards"][1]["base"]["suite"].update(profile="other"),
        lambda b: b["shards"][1]["head"]["suite"].update(source_commit="d" * 40),
        lambda b: b["shards"][1]["head"]["suite"].update(source_digest="d" * 64),
        lambda b: b["shards"][1]["head"]["execution"].update(shard_id="other"),
        lambda b: b["shards"][1]["head"]["execution"].update(pair_id="other"),
        lambda b: [b["shards"][1][r]["execution"].update(pair_id="pair-0") for r in ("base", "head")],
    ],
)
def test_bundle_checks_every_shard(mutation):
    bundle = bundle_fixture()
    mutation(bundle)
    with pytest.raises(InvalidResult):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "change",
    [
        "missing-directory",
        "extra-directory",
        "duplicate-directory",
        "extra-file",
        "missing-file",
        "file-symlink",
        "directory-symlink",
        "root-symlink",
        "manifest-symlink",
        "fifo",
        "directory-member",
    ],
)
def test_collect_rejects_unsafe_or_incomplete_tree(tmp_path, change):
    manifest, root, _ = collection_fixture(tmp_path)
    directory = root / "benchmark-shard-shard-1"
    member = directory / "head.json"
    if change == "missing-directory":
        directory.rename(tmp_path / "removed")
    elif change in ("extra-directory", "duplicate-directory"):
        (root / ("undeclared" if change == "extra-directory" else "benchmark-shard-shard-1-copy")).mkdir()
    elif change == "extra-file":
        (directory / "script.py").write_text("raise Exception('must not run')")
    elif change == "missing-file":
        member.unlink()
    elif change in ("file-symlink", "directory-symlink", "root-symlink", "manifest-symlink"):
        path = {
            "file-symlink": member,
            "directory-symlink": directory,
            "root-symlink": root,
            "manifest-symlink": manifest,
        }[change]
        saved = tmp_path / "saved"
        path.rename(saved)
        path.symlink_to(saved)
    else:
        member.unlink()
        if change == "fifo":
            os.mkfifo(member)
        else:
            member.mkdir()
    with pytest.raises(InvalidResult):
        collect(str(manifest), str(root))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(target="d" * 40),
        lambda r: r.update(head="d" * 40),
        lambda r: r.update(base="d" * 40),
        lambda r: r.update(extra="unexpected"),
        lambda r: r.update(target="not-sha"),
    ],
)
def test_collect_requires_identical_exact_revisions(tmp_path, mutation):
    manifest, root, _ = collection_fixture(tmp_path)
    path = root / "benchmark-shard-shard-1/revisions.json"
    doc = json.loads(path.read_text())
    mutation(doc)
    path.write_text(json.dumps(doc))
    with pytest.raises(InvalidResult):
        collect(str(manifest), str(root))


@pytest.mark.parametrize("field", ["base", "head"])
def test_collect_matches_documents_against_revision_sidecar(tmp_path, field):
    manifest, root, _ = collection_fixture(tmp_path)
    for path in root.glob("*/revisions.json"):
        revisions = json.loads(path.read_text())
        revisions[field] = "d" * 40
        path.write_text(json.dumps(revisions))
    with pytest.raises(InvalidResult, match="differs"):
        collect(str(manifest), str(root))


def test_collect_checks_source_identity(tmp_path):
    manifest, root, _ = collection_fixture(tmp_path)
    for path in [*root.glob("*/base.json"), *root.glob("*/head.json")]:
        doc = json.loads(path.read_text())
        doc["suite"]["source_commit"] = "d" * 40
        path.write_text(json.dumps(doc))
    with pytest.raises(InvalidResult, match="source differs"):
        collect(str(manifest), str(root))


def test_aggregate_budget_is_checked_before_result_reads(tmp_path, monkeypatch):
    manifest, root, _ = collection_fixture(tmp_path)
    for path in root.glob("*/head.json"):
        with path.open("wb") as stream:
            stream.truncate(MAX_DOCUMENT_BYTES // 2)
    reads = []
    original = bundle_module.read_regular
    monkeypatch.setattr(bundle_module, "read_regular", lambda path, *a: reads.append(path) or original(path, *a))
    with pytest.raises(InvalidResult, match="Aggregate shard inputs"):
        collect(str(manifest), str(root))
    assert reads == [manifest]


def test_bundle_byte_bounds_and_duplicate_json_keys(tmp_path):
    path = tmp_path / "bundle.json"
    for data in (b" " * (MAX_DOCUMENT_BYTES + 1), b'{"bundle_version":1,"bundle_version":1}'):
        path.write_bytes(data)
        with pytest.raises(InvalidResult):
            load_bundle(path)
    with pytest.raises(InvalidResult, match="Bundle exceeds byte limit"):
        encode_bundle({"data": "." * MAX_DOCUMENT_BYTES})


def test_sixteen_shard_report_is_bounded_and_escaped():
    bundle = bundle_fixture(count=16)
    for shard in bundle["shards"]:
        for role in ("base", "head"):
            doc = shard[role]
            for field in ("runtime", "runtime_version", "os", "arch", "cpu", "runner_class"):
                doc["execution"][field] = "<script>@all." * 19
            doc["execution"]["resource_limits"]["description"] = "." * 4096
            doc["benchmarks"][0]["runs"][0]["stderr"] = "PRIVATE RAW LOG"
    result = compare_bundle(bundle)
    result["rows"] *= 100
    report = render(result)
    assert len(report.encode()) < MAX_REPORT_BYTES
    assert report.count("### Shard:") == 16
    assert "<script>" not in report and "@all" not in report and "PRIVATE RAW LOG" not in report


@pytest.mark.parametrize(
    "args",
    [
        ["--bundle-results", "bundle", "--base-results", "base"],
        ["--bundle-results", "bundle", "--head-results", "head"],
        ["--bundle-results", "bundle", "--shard-manifest", "manifest"],
        ["--shard-directory", "shards"],
        ["--mode", "collect", "--base-results", "base"],
        ["--mode", "collect", "--bundle-results", "bundle"],
        ["--mode", "collect", "--shard-manifest", "manifest"],
        ["--mode", "publish", "--bundle-results", "bundle"],
    ],
)
def test_cli_rejects_ambiguous_inputs(tmp_path, args):
    assert cli.main(args + ["--output-directory", str(tmp_path)], {}) == 1
    assert "error" in json.loads((tmp_path / "comparison.json").read_text())


class BundleGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.bundle = bundle_fixture()

    def download(self, path):
        self.calls.append((path, "DOWNLOAD"))
        return archive(
            [("bundle.json", encode_bundle(self.bundle)), ("revisions.json", json.dumps(self.revisions).encode())]
        )


def test_publisher_accepts_two_file_bundle_and_uses_verified_conclusion():
    api = BundleGitHub()
    api.run["conclusion"] = "failure"
    result, report = publisher(api).publish(api.event)
    assert result["publication"]["status"] == "created" and api.writes
    assert result["comparison_version"] == 2 and result["execution_failed"]
    assert all("change_percent" not in row for row in result["rows"])
    assert "Benchmark execution failed" in report


@pytest.mark.parametrize("role", ["base", "head"])
@pytest.mark.parametrize("field", ["sdk", "commit", "dirty", "source"])
@pytest.mark.parametrize("all_shards", [False, True])
def test_publisher_verifies_every_bundle_identity(role, field, all_shards):
    api = BundleGitHub()
    for shard in api.bundle["shards"] if all_shards else api.bundle["shards"][-1:]:
        doc = shard[role]
        if field == "sdk":
            doc["sdk"] = "other"
        elif field == "commit":
            doc["revision"]["commit"] = "d" * 40
        elif field == "dirty":
            doc["revision"]["dirty"] = True
        else:
            # Keep both suites internally consistent when mutating every shard, to exercise trusted verification.
            for side in ("base", "head"):
                shard[side]["suite"]["source_commit"] = "d" * 40
    if field == "sdk" and all_shards:
        api.bundle["manifest"]["sdk"] = "other"
        for shard in api.bundle["shards"]:
            for side in ("base", "head"):
                shard[side]["sdk"] = "other"
    with pytest.raises(InvalidResult):
        publisher(api).publish(api.event)
    assert not api.writes


@pytest.mark.parametrize(
    "change", ["attempt", "artifact-attempt", "stale", "fork", "revision-target", "revision-head", "revision-base"]
)
def test_bundle_preserves_publisher_trust_guards(change):
    api = BundleGitHub()
    if change == "attempt":
        api.run["run_attempt"] = 2
    elif change == "artifact-attempt":
        api.artifacts[0]["created_at"] = "2026-10-26T10:00:00Z"
    elif change == "stale":
        api.comments = [bot_comment(21, 11, 1)]
    elif change == "fork":
        api.artifacts[0]["workflow_run"]["head_repository_id"] = 2
    else:
        api.revisions[change.removeprefix("revision-")] = "d" * 40
    if change in ("fork", "revision-head", "revision-base"):
        with pytest.raises(InvalidResult):
            publisher(api).publish(api.event)
    else:
        assert publisher(api).publish(api.event)[0]["publication"]["status"] == "skipped"
    assert not api.writes


@pytest.mark.parametrize(
    "names",
    [
        ["bundle.json", "revisions.json", "base.json"],
        ["bundle.json", "revisions.json", "head.json", "base.json"],
        ["bundle.json", "bundle.json", "revisions.json"],
        ["bundle.json", "revisions.json", "report.md"],
        ["bundle.json"],
        ["../bundle.json", "revisions.json"],
        ["/bundle.json", "revisions.json"],
        ["nested/bundle.json", "revisions.json"],
    ],
)
def test_bundle_archive_rejects_mixed_extra_duplicate_or_unsafe_members(names):
    with pytest.raises(InvalidResult):
        read_archive(archive([(name, b"{}") for name in names]))


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO])
def test_bundle_archive_rejects_nonregular_members(mode):
    info = zipfile.ZipInfo("bundle.json")
    info.create_system = 3
    info.external_attr = (mode | 0o644) << 16
    with pytest.raises(InvalidResult):
        read_archive(archive([(info, b"{}"), ("revisions.json", b"{}")]))


def test_bundle_archive_retains_member_and_compression_bounds():
    for data, compression in [
        (b"x" * (MAX_DOCUMENT_BYTES + 1), zipfile.ZIP_STORED),
        (b"x" * 100000, zipfile.ZIP_DEFLATED),
    ]:
        with pytest.raises(InvalidResult):
            read_archive(archive([("bundle.json", data), ("revisions.json", b"{}")], compression))
    bundle = bundle_fixture()
    files = read_archive(archive([("bundle.json", encode_bundle(bundle)), ("revisions.json", b"{}")]))
    assert validate_bundle(bounded_json(files["bundle.json"])) == bundle


def test_failed_worker_in_last_shard_remains_a_failure_with_successful_source():
    bundle = bundle_fixture()
    bundle["shards"][-1]["head"]["benchmarks"][0].update(status="timeout")
    comparison = compare_bundle(bundle, source_status="success")
    assert comparison["execution_failed"]
    assert comparison["shards"]["shard-1"]["execution_failed"]
    assert comparison["rows"][0]["execution_failed"]
    assert comparison["rows"][0]["shard_id"] == "shard-1"
    assert "base_median" not in comparison["rows"][0]
    assert "Benchmark execution failed" in render(comparison)


@pytest.mark.parametrize("malformed", [False, True])
def test_failed_collection_writes_failure_report_not_reduced_bundle(tmp_path, malformed):
    manifest, root, _ = collection_fixture(tmp_path)
    path = root / "benchmark-shard-shard-1/head.json"
    if malformed:
        path.write_text('{"private": "NEVER PRINT THIS",')
    else:
        path.unlink()
    output = tmp_path / "out"
    assert (
        cli.main(
            [
                "--mode",
                "collect",
                "--shard-manifest",
                str(manifest),
                "--shard-directory",
                str(root),
                "--output-directory",
                str(output),
            ],
            {},
        )
        == 1
    )
    assert not (output / "bundle.json").exists()
    assert (root / "benchmark-shard-shard-0/base.json").is_file()
    report = (output / "report.md").read_text()
    assert "Reporting failed" in report and "NEVER PRINT THIS" not in report
