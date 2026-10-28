#!/usr/bin/env python3
"""Explicit temporary MinerU selection; never starts or restarts services."""

import argparse
import json
import os
import re
import shlex
import tempfile
import urllib.request
from pathlib import Path

LAB_URL = "https://mineru.acemap.cn/file_parse"
AUTODL_URL = "http://127.0.0.1:18006/file_parse"
# The operator's public jobs API (no token): submit, poll, fetch.
PARSE_URL = "https://parse.iiot-shxt.xyz/jobs"
KEYS = (
    "COMPILER_MINERU_URL",
    "COMPILER_MINERU_API_TOKEN",
    "COMPILER_MINERU_CONCURRENCY",
    "COMPILER_MINERU_TIMEOUT_SECONDS",
)
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(" + "|".join(KEYS) + r")\s*=")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def health(token):
    request = urllib.request.Request(
        AUTODL_URL.removesuffix("/file_parse") + "/health",
        headers={"Authorization": "Bearer " + token},
    )
    # Ignore proxy environment variables for this fixed loopback request.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=5) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("MinerU health response is too large")
    return json.loads(raw)


def parse_health():
    request = urllib.request.Request(PARSE_URL.removesuffix("/jobs") + "/health")
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("MinerU health response is too large")
    return json.loads(raw)


def profile(mode, runtime_root, credential_file):
    env_path = Path(runtime_root) / "secrets/compiler.env"
    original = env_path.read_text()
    if mode == "status":
        values = {}
        for line in original.splitlines():
            match = ASSIGNMENT.match(line)
            if match:
                words = shlex.split(line[match.end():], comments=True)
                values[match[1]] = words[0] if words else ""
        url = values.get(KEYS[0]) or LAB_URL
        # Only display known endpoints; arbitrary values can contain credentials.
        return {
            "url": url if url in (LAB_URL, AUTODL_URL, PARSE_URL) else "unrecognized endpoint",
            "concurrency": (values.get(KEYS[2]) or "1")
            if (values.get(KEYS[2]) or "1") in ("1", "2", "3", "4") else "invalid",
            "token_present": bool(values.get(KEYS[1])),
        }

    updates = {}
    readiness = {}
    if mode == "autodl":
        credentials = json.loads(Path(credential_file).read_text())
        if credentials.get("api_url") != AUTODL_URL:
            raise ValueError("AutoDL API URL must be the approved loopback endpoint")
        token = credentials.get("api_token")
        if not isinstance(token, str) or not 1 <= len(token) <= 512 or any(
            not 33 <= ord(char) <= 126 for char in token
        ):
            raise ValueError("AutoDL API token is invalid")
        state = health(token)
        ready = state.get("workers_ready")
        if (
            state.get("status") != "healthy"
            or type(state.get("protocol_version")) is not int
            or state["protocol_version"] != 1
            or type(ready) is not int
            or ready < 1
        ):
            raise ValueError("AutoDL requires protocol 1 and at least one ready worker")
        updates = dict(zip(KEYS, (AUTODL_URL, token, "4")))
        readiness = {"workers_ready": ready, "workers_total": state.get("workers_total")}
    elif mode == "parse":
        if parse_health().get("status") != "ok":
            raise ValueError("The MinerU jobs API is not healthy")
        # 30 min covers a queued job plus the service's own 15 min parse limit.
        updates = {KEYS[0]: PARSE_URL, KEYS[2]: "4", KEYS[3]: "1800"}
    elif mode != "lab":
        raise ValueError("Unknown MinerU profile")

    content = "".join(
        line for line in original.splitlines(keepends=True) if not ASSIGNMENT.match(line)
    )
    if updates:
        if content and not content.endswith("\n"):
            content += "\n"
        content += "".join(f"{key}={shlex.quote(value)}\n" for key, value in updates.items())
    fd, temporary = tempfile.mkstemp(prefix=".compiler.env-", dir=env_path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, env_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"profile": mode, **readiness, "restart": "Restart Compiler manually to apply."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("lab", "autodl", "parse", "status"))
    parser.add_argument("--runtime-root", type=Path,
                        default=Path.home() / "Library/Application Support/DeepShovel")
    parser.add_argument("--credential-file", type=Path,
                        default=Path.home() / ".config/mineru-autodl/6d2fbb8d.json")
    args = parser.parse_args()
    try:
        result = profile(args.mode, args.runtime_root, args.credential_file)
    except (OSError, ValueError, TypeError, AttributeError):
        # Exception text may contain environment values or server-supplied secrets.
        parser.exit(1, "MinerU profile operation failed; verify configuration and health.\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
