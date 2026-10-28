#!/usr/bin/env python3
"""Verify every file listed in the published SHA-256 manifest."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "repro/files.json").read_text())
for rel, expected in manifest["files"].items():
    path = root / rel
    assert path.is_file(), f"Missing file: {rel}"
    data = path.read_bytes()
    assert len(data) == expected["bytes"] and hashlib.sha256(data).hexdigest() == expected["sha256"], f"Changed file: {rel}"
print(json.dumps({"passed": True, "files_verified": len(manifest["files"]), "hash": "SHA-256"}))
