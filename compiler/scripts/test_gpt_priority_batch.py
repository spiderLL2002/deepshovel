"""Isolated ten-paper regression using cached IR; never modifies production data."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time

from compiler_service.compiler import EvidenceCompiler
from compiler_service.model_client import ModelJsonClient
from compiler_service.models import DocumentIR

PAPERS = ("039", "054", "080", "043", "049", "038", "007", "009", "050", "064")


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class TrackedClient(ModelJsonClient):
    def __init__(self, *args, records, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = records

    def with_job_control(self, control):
        return TrackedClient(self._client, fallback_client=self._fallback_client,
                             job_control=control, records=self.records)

    def complete(self, **kwargs):
        start = time.monotonic()
        record = {"schema": kwargs["schema"].__name__,
                  "input_id": hashlib.sha256(kwargs["user_content"].encode()).hexdigest()[:16]}
        try:
            result = super().complete(**kwargs)
            record.update(status="completed", model=result.model,
                          diagnostics=[x.model_dump(mode="json") for x in result.diagnostics])
            return result
        except Exception as exc:
            record.update(status="failed", error_type=type(exc).__name__,
                          error_code=getattr(exc, "code", None),
                          diagnostics=[x.model_dump(mode="json") for x in getattr(exc, "diagnostics", ())])
            raise
        finally:
            record["elapsed_s"] = round(time.monotonic() - start, 2)
            self.records.append(record)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    if not os.environ.get("GPT_API_KEY"):
        raise SystemExit("GPT_API_KEY is required")
    args.output.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.WARNING, filename=args.output / "attempts.log",
                        format="%(asctime)s %(levelname)s %(message)s")
    with sqlite3.connect((args.data / 'jobs.sqlite3').resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT artifact_json,result_path FROM compile_jobs WHERE status='completed' ORDER BY created_ms DESC").fetchall()
    selected = {}
    for artifact, result_path in rows:
        name = json.loads(artifact)["file_name"]
        prefix = name[:3]
        if prefix in PAPERS and prefix not in selected:
            selected[prefix] = (name, Path(result_path))
    if set(selected) != set(PAPERS):
        raise SystemExit("Missing completed source papers")
    save(args.output / "manifest.json", {"created": datetime.now(timezone.utc).isoformat(),
         "papers": PAPERS, "workers": args.workers, "verify": True, "mineru": False,
         "openalex": False, "production_writes": False,
         "selection": "5 previous DeepSeek extractions + 5 previous whole-job failures; not a random quality sample"})

    def run(prefix):
        name, old_path = selected[prefix]
        records = []
        start = time.monotonic()
        result = {"paper": prefix, "file_name": name, "status": "running", "stages": records}
        destination = args.output / f"{prefix}.json"
        save(destination, result)
        client = ModelJsonClient.from_api_key(os.environ["GPT_API_KEY"],
                    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY") or None)
        tracked = TrackedClient(client._client, fallback_client=client._fallback_client, records=records)
        try:
            old = json.loads(old_path.read_text())
            result["baseline"] = {"extraction_model": old["extraction"]["model"],
                                  "claims": len(old["graph"]["claims"])}
            cached = json.loads((old_path.parent / "document-ir.json").read_text())
            document = DocumentIR.model_validate(cached["document"])
            outcome = EvidenceCompiler(tracked).compile(document, verify=True)
            save(args.output / f"{prefix}-result.json", outcome.envelope.model_dump(mode="json"))
            result.update(status="completed", claims=len(outcome.raw_graph.claims),
                          extraction_attempts=outcome.extraction_attempts,
                          verification_attempts=outcome.verification_attempts)
        except Exception as exc:
            result.update(status="failed", error_type=type(exc).__name__, error_code=getattr(exc, "code", None))
        finally:
            result["elapsed_s"] = round(time.monotonic() - start, 2)
            save(destination, result)
            client._client.sdk.close()
            if client._fallback_client:
                client._fallback_client.sdk.close()
        return result

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(run, prefix) for prefix in PAPERS]):
            result = future.result()
            results.append(result)
            save(args.output / "progress.json", {"completed": len(results), "total": 10, "papers": results})
            print(f"{result['paper']} {result['status']} {len(results)}/10", flush=True)
    save(args.output / "summary.json", {"papers": results, "total": 10,
         "completed": sum(x["status"] == "completed" for x in results)})


if __name__ == "__main__":
    main()
