#!/usr/bin/env python3
"""Restore two third-party supporting inputs from their original providers.

The unchanged source manifest supplies identity, URL and expected SHA-256.
Existing files must already match. Downloading never changes frozen bindings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "reproduction/payload/ree/round8"
ROLES = {"author_training_ree", "training_catalog"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_url(source_url):
    prefix = "https://github.com/PingChunLin/REE_pH/blob/"
    if source_url.startswith(prefix):
        return "https://raw.githubusercontent.com/PingChunLin/REE_pH/" + source_url[len(prefix):]
    return source_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Verify local copies without network access")
    args = parser.parse_args()
    manifest = json.loads((SOURCE_ROOT / "transfer-source-input.json").read_text())
    selected = [s for s in manifest["sources"] if s["role"] in ROLES]
    if {s["role"] for s in selected} != ROLES or len(selected) != len(ROLES):
        raise RuntimeError("Missing or repeated external source roles")
    results = []
    for source in selected:
        destination = SOURCE_ROOT / source["path"]
        action = "verified_existing"
        if not destination.exists():
            if args.verify_only:
                raise RuntimeError("Missing input; run without --verify-only: " + source["path"])
            url = download_url(source["url"])
            with urlopen(url, timeout=45) as response:
                content = response.read(64 * 1024 * 1024 + 1)
            if len(content) > 64 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != source["sha256"]:
                raise RuntimeError("Provider bytes differ from the frozen input: " + source["role"])
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            temporary_path.replace(destination)
            action = "downloaded_and_verified"
        if digest(destination) != source["sha256"]:
            raise RuntimeError("Existing input hash mismatch; file left unchanged: " + source["path"])
        results.append({"role": source["role"], "sha256": source["sha256"], "action": action})
    print(json.dumps({"status": "PASS", "inputs": results}, indent=2))


if __name__ == "__main__":
    main()
