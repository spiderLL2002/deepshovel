#!/usr/bin/env python3
"""Send one authorized artifact through the production remote parser boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from compiler_service.mineru import MineruRemoteBackend
from compiler_service.models import ArtifactContext
from compiler_service.normalize import normalize_mineru


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Upload an authorized document to the allowlisted laboratory MinerU "
            "service and print non-content parse metrics"
        )
    )
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    path = args.input.resolve(strict=True)
    input_bytes = path.stat().st_size
    digest = _sha256(path)
    artifact = ArtifactContext(
        paper_id="paper:remote-smoke",
        artifact_id="artifact:remote-smoke",
        version_id=f"artifact-version:sha256:{digest}",
        artifact_sha256=digest,
        media_type=_media_type(path),
        locator=f"smoke://{path.name}",
        file_name=path.name,
    )
    output = MineruRemoteBackend().parse(path)
    parsed = normalize_mineru(output, artifact)
    kinds = Counter(
        block.kind for page in parsed.document.pages for block in page.blocks
    )
    document_bytes = len(
        parsed.document.model_dump_json(exclude_none=False).encode("utf-8")
    )
    print(
        json.dumps(
            {
                "input_sha256": digest,
                "input_bytes": input_bytes,
                "mineru_version": output.version,
                "method": output.method,
                "duration_ms": output.duration_ms,
                "page_count": parsed.document.page_count,
                "block_count": sum(kinds.values()),
                "block_kinds": dict(sorted(kinds.items())),
                "document_ir_bytes": document_bytes,
                "warning_count": len(parsed.document.warnings),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    media = {
        ".pdf": "application/pdf",
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(suffix)
    if media is None:
        raise SystemExit("unsupported input extension")
    return media


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
