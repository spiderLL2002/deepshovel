"""Verify one exported observation branch against its recorded full graph."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def read(name):
    return json.loads((ROOT/name).read_text())
def main():
    full=read("full-graph.json");graph=full["graph"];result=read("result.json")["result"]
    branch=result["observation_evidence_path"]
    nodes={n["id"]:n for key in ["anchors","observations","reasoning","claims","assumptions","artifacts","artifact_versions"] for n in graph[key]}
    edges={e["id"]:e for e in graph["edges"]}
    assert len({n["id"] for n in branch["nodes"]})==len(branch["nodes"])
    assert all(n==nodes[n["id"]] for n in branch["nodes"]), "Node fields differ from stored graph"
    assert all(e==edges[e["id"]] for e in branch["edges"]), "Edge fields differ from stored graph"
    ids=[n["id"] for n in branch["nodes"]]
    assert ids[0]=="anchor.text.f2.stability" and ids[-1]==result["claim_id"]
    assert nodes[ids[0]]["kind"]=="source_anchor"
    kinds=[nodes[i]["kind"] for i in ids]
    assert kinds[1]=="observation" and all(k=="reasoning" for k in kinds[2:-1])
    assert len(branch["edges"])==len(ids)-1
    for left,right,edge in zip(ids,ids[1:],branch["edges"]):
        assert (edge["source_id"],edge["target_id"])==(left,right), "Not an ordered directed path"
    assert ids[0] in nodes[ids[1]]["anchor_ids"]
    assert full["snapshot"]["id"]==result["snapshot_id"]
    assert graph["paper_id"]==result["paper_id"]
    artifact=next(v for v in graph["artifact_versions"] if v["id"]==full["snapshot"]["rootArtifactVersionId"])
    digest=hashlib.sha256((ROOT/"source.pdf").read_bytes()).hexdigest()
    assert digest==artifact["sha256"]==result["source"]["sha256"]
    assert artifact["id"]==result["source"]["artifact_version_id"]=="artifact-version:sha256:"+digest
    assert nodes[ids[0]]["artifact_version_id"]==artifact["id"]
    shortest=result["shortest_evidence_path"]
    assert shortest==full["recorded_retrieval_shortest_path"], "Original shortest path changed"
    assert not any(n["kind"]=="observation" for n in shortest["nodes"])
    assert set(ids)!={n["id"] for n in shortest["nodes"]}
    report={"verified":True,"scientific_node_equality":"exact JSON field values","verified_branch_nodes":len(ids),"verified_branch_edges":len(branch["edges"]),"directed_path":ids,"source_pdf_sha256":digest,"snapshot_id":result["snapshot_id"],"original_shortest_path_unchanged":True,"original_shortest_path_has_observation":False,"branch_is_original_shortest_path":False,"scope":"One paper; structural reproduction only, not independent scientific correctness","new_provenance_edges_created":0,"new_snapshots_created":0}
    (ROOT/"path-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":
    main()
