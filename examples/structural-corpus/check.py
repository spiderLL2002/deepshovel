#!/usr/bin/env python3
"""Check the 30 text-free production projections; no scientific quality claim."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / "manifest.json").read_text())
assert len(manifest["items"]) == 30
papers = set()
route_count = 0
for item in manifest["items"]:
    data = (root / item["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == item["sha256"]
    document = json.loads(data)
    assert document["paper_id"] not in papers
    papers.add(document["paper_id"])
    graph = document["graph"]
    ids = {node["id"] for group, nodes in graph.items() if group != "edges" for node in nodes}
    assert all(edge["source_id"] in ids and edge["target_id"] in ids for edge in graph["edges"])
    outgoing = {}
    for edge in graph["edges"]:
        outgoing.setdefault(edge["source_id"], []).append(edge["target_id"])
    claims = {node["id"] for node in graph["claims"]}
    grounded = {edge["target_id"] for edge in graph["edges"] if edge["relation"] == "grounds"}
    connected = False
    for observation in graph["observations"]:
        if observation["id"] not in grounded:
            continue
        queue = [observation["id"]]
        visited = set()
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            if node in claims:
                connected = True
            queue.extend(outgoing.get(node, []))
    route_count += connected
print(json.dumps({"papers": len(papers), "hashes_verified": 30, "papers_with_grounded_observation_to_claim_route": route_count, "scope": "structural examples, not a scientific-accuracy evaluation"}, indent=2))
