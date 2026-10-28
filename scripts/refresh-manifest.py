#!/usr/bin/env python3
"""Maintainer utility: refresh publication hashes for the tracked source files."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
source_path = root / "docs/source-snapshot.json"
source = json.loads(source_path.read_text())
for rel, entry in source["source_file_correspondence"].items():
    path = root / rel
    if path.is_file():
        entry["published_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        entry["publication_modified"] = entry["published_sha256"] != entry["sha256"]
source_path.write_text(json.dumps(source, indent=2) + "\n")
tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
files = {}
for rel in filter(None, tracked):
    if rel == "repro/files.json":
        continue
    data = (root / rel).read_bytes()
    files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
(root / "repro/files.json").write_text(json.dumps({"algorithm": "SHA-256", "excluded": "this manifest itself and generated local environments/runs", "files": files}, indent=2) + "\n")
print(f"Recorded {len(files)} tracked file hashes.")
