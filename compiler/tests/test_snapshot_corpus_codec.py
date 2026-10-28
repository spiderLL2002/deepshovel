"""Bounded lossless TypeScript→Python corpus transport, without models or data."""

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from compiler_service.retrieval_contract import (
    MAX_ACTIVE_SNAPSHOTS,
    MAX_CORPUS_SNAPSHOTS,
    MAX_RETRIEVAL_BODY_BYTES,
    SearchFilters,
)

ROOT = Path(__file__).resolve().parents[2]


def filters(value):
    return {
        "corpus": "cross_paper",
        "paper_ids": [],
        "paper_family_ids": [],
        "active_snapshot_ids": value,
        "limit": 12,
    }


def encoded(data):
    return "sha256-v1:" + base64.urlsafe_b64encode(data).decode().rstrip("=")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
@pytest.mark.parametrize("count", [5000, 100000])
def test_typescript_codec_roundtrip_preserves_scoped_and_historical_order(count):
    script = r"""
import {createHash} from 'node:crypto';
import {parseEvidenceSearchRequest,toEvidenceRetrievalSearchRequestDto}
  from './lib/evidence-retrieval/contract.ts';
const count = Number(process.argv[1]);
const hash = value => createHash('sha256').update(value).digest('hex');
const id = value => `snapshot:sha256:${hash(value)}`;
const ids = Array.from({length:count}, (_,i)=>id(String(count-1-i)));
ids[count-1] = id('historical');
const request = parseEvidenceSearchRequest({query:'Mercury evidence',stage:'preview',
  query_claim:{snapshot_id:ids[count-1],claim_id:`claim:sha256:${'a'.repeat(64)}`},
  paper_family_ids:['artifact:paper'],openalex_scope:{level:'topic',id:'https://openalex.org/T123'},limit:12});
request.searchProtocol = 'evidence-retrieval/2.3';
const dto = toEvidenceRetrievalSearchRequestDto('project-1', {
  request, queryText:request.query, queryDocument:null,
  manifests:ids.map(snapshotId=>({snapshotId})),
});
process.stdout.write(JSON.stringify(dto));
"""
    result = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--no-warnings",
            "--input-type=module",
            "-e",
            script,
            str(count),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    dto = json.loads(result.stdout)
    assert isinstance(dto["filters"]["active_snapshot_ids"], str)
    decoded = SearchFilters.model_validate(dto["filters"], strict=True)
    expected = [
        "snapshot:sha256:" + hashlib.sha256(str(i).encode()).hexdigest()
        for i in reversed(range(count))
    ]
    expected[-1] = "snapshot:sha256:" + hashlib.sha256(b"historical").hexdigest()
    assert decoded.active_snapshot_ids == expected
    assert dto["query_claim"]["snapshot_id"] == expected[-1]
    assert decoded.paper_family_ids == ["artifact:paper"]
    assert decoded.openalex_scope.id == "https://openalex.org/T123"
    assert len(result.stdout.encode()) < MAX_RETRIEVAL_BODY_BYTES
    assert MAX_ACTIVE_SNAPSHOTS == 4096 and MAX_CORPUS_SNAPSHOTS == 131072
    if count == 100000:
        assert len(result.stdout.encode()) < 4_270_000


@pytest.mark.parametrize(
    "bad",
    [
        "sha256-v2:AAAA",
        "sha256-v1:",
        "sha256-v1:A",
        "sha256-v1:AA==",
        "sha256-v1:AA+_",
        "sha256-v1:中文",
        encoded(b"x" * 31),
        encoded(b"x" * 33),
        encoded(b"x" * 64),
        # Nonzero unused base64 bits must not admit alternate encodings.
        encoded(bytes(32))[:-1] + "B",
    ],
)
def test_codec_rejects_malformed_noncanonical_or_duplicate_hashes(bad):
    with pytest.raises(ValidationError):
        SearchFilters.model_validate(filters(bad), strict=True)


def test_codec_and_list_bounds_remain_finite():
    with pytest.raises(ValidationError):
        SearchFilters.model_validate(
            filters(encoded(bytes(32 * (MAX_CORPUS_SNAPSHOTS + 1)))), strict=True
        )
    with pytest.raises(ValidationError):
        SearchFilters.model_validate(
            filters(["snapshot-a"] * (MAX_CORPUS_SNAPSHOTS + 1)), strict=True
        )
    with pytest.raises(ValidationError):
        SearchFilters.model_validate(filters(["snapshot-a", "snapshot-a"]), strict=True)
    ids = [f"snapshot-{index}" for index in range(5000)]
    assert (
        SearchFilters.model_validate(filters(ids), strict=True).active_snapshot_ids
        == ids
    )
    assert (
        SearchFilters.model_validate(filters(None), strict=True).active_snapshot_ids
        is None
    )
