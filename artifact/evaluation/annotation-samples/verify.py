import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
qrels=json.loads((ROOT.parent/"qrels.json").read_text())
pools=json.loads((ROOT.parent/"pool_ids.json").read_text())
manifest=json.loads((ROOT/"manifest.json").read_text())
count=0
for file in manifest["files"]:
    packet=json.loads((ROOT/file["file"]).read_text());qid=packet["query_id"]
    assert len(packet["candidates"])==file["candidate_count"]
    assert {c["document_id"] for c in packet["candidates"]}==set(pools[qid])
    for c in packet["candidates"]:
        label=c["ai_judgment"]; expected=qrels[qid][c["document_id"]]
        assert label["relevance"]==expected["grade"]
        assert label["scope_error"]==expected["scope_error"]
        assert sum(len(a["excerpt"]) for a in c["anchors"])<=800
        count+=1
print(f"Verified {count} unchanged judgments in six complete fixed-ID pools.")
