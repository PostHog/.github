"""Bounded shard collection and independent paired comparisons; inputs are data only."""

import json
import os
import re
import stat
from pathlib import Path

from .compare import compare
from .validate import MAX_DOCUMENT_BYTES, bounded_json, check_bounds, require, validate

MAX_SHARDS = 16
SHARD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def validate_manifest(manifest: dict) -> dict:
    check_bounds(manifest)
    require(
        isinstance(manifest, dict) and set(manifest) == {"manifest_version", "sdk", "shards"},
        "Invalid shard manifest fields",
    )
    require(type(manifest["manifest_version"]) is int and manifest["manifest_version"] == 1, "Invalid manifest version")
    require(isinstance(manifest["sdk"], str) and 0 < len(manifest["sdk"]) <= 256, "Invalid manifest SDK")
    shards = manifest["shards"]
    require(isinstance(shards, list) and 0 < len(shards) <= MAX_SHARDS, "Invalid manifest shard count")
    ids, workloads = set(), set()
    for shard in shards:
        require(isinstance(shard, dict) and set(shard) == {"id", "workloads"}, "Invalid manifest shard fields")
        identity = shard["id"]
        require(isinstance(identity, str) and SHARD_ID.fullmatch(identity), "Unsafe shard ID")
        require(identity not in ids, "Duplicate manifest shard")
        ids.add(identity)
        members = shard["workloads"]
        require(isinstance(members, list) and 0 < len(members) <= 128, "Invalid shard workload count")
        for member in members:
            require(isinstance(member, str) and 0 < len(member) <= 256, "Invalid manifest workload ID")
            require(member not in workloads, "Duplicate manifest workload")
            workloads.add(member)
    return manifest


def validate_revisions(revisions: dict) -> dict:
    require(isinstance(revisions, dict) and set(revisions) == {"target", "head", "base"}, "Invalid revisions document")
    require(
        all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) for value in revisions.values()),
        "Invalid revision SHA",
    )
    return revisions


def validate_bundle(bundle: dict) -> dict:
    check_bounds(bundle)
    require(
        isinstance(bundle, dict) and set(bundle) == {"bundle_version", "manifest", "shards"}, "Invalid bundle fields"
    )
    require(type(bundle["bundle_version"]) is int and bundle["bundle_version"] == 1, "Invalid bundle version")
    manifest = validate_manifest(bundle["manifest"])
    expected = {shard["id"]: set(shard["workloads"]) for shard in manifest["shards"]}
    shards = bundle["shards"]
    require(isinstance(shards, list) and len(shards) == len(expected), "Bundle shard count differs")
    seen, pairs, revisions = set(), set(), {}
    suite = None
    for shard in shards:
        require(isinstance(shard, dict) and set(shard) == {"id", "base", "head"}, "Invalid bundle shard fields")
        identity = shard["id"]
        require(
            isinstance(identity, str) and identity in expected and identity not in seen,
            "Unexpected or duplicate bundle shard",
        )
        seen.add(identity)
        for role in ("base", "head"):
            doc = validate(shard[role])
            require(doc["sdk"] == manifest["sdk"], "Shard SDK differs")
            require(doc["revision"]["role"] == role, "Wrong shard revision role")
            require(doc["execution"].get("shard_id") == identity, "Result shard ID differs")
            require({w["id"] for w in doc["benchmarks"]} == expected[identity], "Shard workload coverage differs")
            if suite is None:
                suite = doc["suite"]
            require(doc["suite"] == suite, "Shard suite/source/profile differs")
            revisions.setdefault(role, doc["revision"])
            require(doc["revision"] == revisions[role], "Shard revision identity differs")
        pair_id = shard["base"]["execution"]["pair_id"]
        require(pair_id == shard["head"]["execution"]["pair_id"], "Shard pair IDs differ")
        require(pair_id not in pairs, "Pair ID reused across shards")
        pairs.add(pair_id)
    return bundle


def compare_bundle(bundle: dict, *, source_status: str | None = None) -> dict:
    validate_bundle(bundle)
    shards = {}
    rows, workloads, workload_shards = [], {}, {}
    for shard in bundle["shards"]:
        identity = shard["id"]
        comparison = compare(shard["base"], shard["head"], source_status=source_status)
        shards[identity] = {key: comparison[key] for key in ("context", "compatibility", "execution_failed")}
        rows.extend(dict(row, shard_id=identity) for row in comparison["rows"])
        workloads.update(comparison["workloads"])
        workload_shards.update({workload: identity for workload in comparison["workloads"]})
    rows.sort(
        key=lambda r: (not r.get("execution_failed", False), r["status"] == "inconclusive", r["workload"], r["metric"])
    )
    return {
        "comparison_version": 2,
        "sdk": bundle["manifest"]["sdk"],
        "manifest": bundle["manifest"],
        "source_execution_status": source_status,
        "execution_failed": any(shard["execution_failed"] for shard in shards.values()),
        "shards": shards,
        "workload_shards": workload_shards,
        "workloads": workloads,
        "rows": rows,
    }


def read_regular(path: Path, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    require(stat.S_ISREG(path.lstat().st_mode), "Input must be a regular file")
    with path.open("rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "Input must be a regular file")
        require(os.fstat(stream.fileno()).st_size <= limit, "Input exceeds byte limit")
        data = stream.read(limit + 1)
    require(len(data) <= limit, "Input exceeds byte limit")
    return data


def load_bundle(path: str | Path) -> dict:
    return validate_bundle(bounded_json(read_regular(Path(path))))


def directory_members(path: Path, maximum: int) -> set[str]:
    require(stat.S_ISDIR(path.lstat().st_mode), "Shard input must be a regular directory")
    names = set()
    with os.scandir(path) as entries:
        for entry in entries:
            names.add(entry.name)
            require(len(names) <= maximum, "Too many shard directory members")
    return names


def encode_bundle(bundle: dict) -> bytes:
    # Compact encoding keeps the canonical document within the same input byte budget.
    data = json.dumps(bundle, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    require(len(data) <= MAX_DOCUMENT_BYTES, "Bundle exceeds byte limit")
    return data


def collect(manifest_path: str, shard_directory: str) -> tuple[dict, dict]:
    manifest_bytes = read_regular(Path(manifest_path))
    manifest = validate_manifest(bounded_json(manifest_bytes))
    root = Path(shard_directory)
    expected = {f"benchmark-shard-{shard['id']}" for shard in manifest["shards"]}
    require(directory_members(root, MAX_SHARDS) == expected, "Missing or unexpected shard directory")
    paths = []
    total = len(manifest_bytes)
    # Preflight all sizes before retaining any result collection.
    for shard in manifest["shards"]:
        directory = root / f"benchmark-shard-{shard['id']}"
        require(
            directory_members(directory, 3) == {"base.json", "head.json", "revisions.json"},
            "Unexpected or missing shard member",
        )
        for name in ("base.json", "head.json", "revisions.json"):
            path = directory / name
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode), "Shard member must be a regular file")
            limit = 8192 if name == "revisions.json" else MAX_DOCUMENT_BYTES
            require(info.st_size <= limit, "Shard member exceeds byte limit")
            total += info.st_size
            require(total <= MAX_DOCUMENT_BYTES, "Aggregate shard inputs exceed byte limit")
            paths.append((shard["id"], name, path, limit))
    shards = {shard["id"]: {"id": shard["id"]} for shard in manifest["shards"]}
    revisions = None
    consumed = len(manifest_bytes)
    for identity, name, path, limit in paths:
        data = read_regular(path, min(limit, MAX_DOCUMENT_BYTES - consumed))
        consumed += len(data)
        doc = bounded_json(data, limit=limit)
        if name == "revisions.json":
            validate_revisions(doc)
            if revisions is None:
                revisions = doc
            require(doc == revisions, "Shard revisions documents differ")
        else:
            shards[identity][name.removesuffix(".json")] = doc
    bundle = validate_bundle({"bundle_version": 1, "manifest": manifest, "shards": list(shards.values())})
    for shard in bundle["shards"]:
        for role in ("base", "head"):
            require(
                shard[role]["revision"]["commit"] == revisions[role], "Shard revision differs from revisions document"
            )
            require(
                shard[role]["suite"]["source_commit"] == revisions["head"], "Shard source differs from head revision"
            )
    encode_bundle(bundle)
    return bundle, revisions
