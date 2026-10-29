"""Recompute the manuscript table using Python standard library only; no network."""
import json
import math
from pathlib import Path
from statistics import mean
ROOT = Path(__file__).resolve().parent
def read(name):
    return json.loads((ROOT / name).read_text())
def dcg(grades):
    return sum((2**g-1)/math.log2(i+2) for i,g in enumerate(grades[:10]))
def main():
    queries, pools, labels, runs = (read(n) for n in ["queries.json", "pool_ids.json", "qrels.json", "runs.json"])
    qids = {q["query_id"] for q in queries}
    assert len(qids) == 114 and qids == set(pools) == set(labels)
    result = {}
    for name, ranking in runs.items():
        assert set(ranking) == qids
        rows = []
        for q in sorted(qids):
            ids = ranking[q]
            assert len(ids) == len(set(ids)) == 12
            assert set(ids) <= set(pools[q]) == set(labels[q])
            assert all(type(v["grade"]) is int and v["grade"] in range(4) and type(v["scope_error"]) is bool for v in labels[q].values())
            ideal = dcg(sorted([v["grade"] for v in labels[q].values()], reverse=True))
            assert ideal > 0
            grades = [labels[q][cid]["grade"] for cid in ids]
            rows.append({"ndcg_at_10_pool":dcg(grades)/ideal,
                "precision_ge2_at_10":sum(g>=2 for g in grades[:10])/10,
                "hit_ge2_at_3":float(any(g>=2 for g in grades[:3])),
                "scope_error_at_10":sum(labels[q][cid]["scope_error"] for cid in ids[:10])/10})
        result[name] = {k:mean(r[k] for r in rows) for k in rows[0]}
    expected = read("expected_metrics.json")
    for name in result:
        for key, value in result[name].items():
            assert math.isclose(value, expected[name][key], rel_tol=0, abs_tol=1e-12), (name,key,value,expected[name][key])
    full_answer = {name: {"hits": sum(any(labels[q][cid]["grade"] == 3 for cid in ids[:3]) for q, ids in ranking.items()), "queries": len(qids)} for name, ranking in runs.items()}
    print(json.dumps({"queries":len(qids), "pooled_judgments":sum(map(len,pools.values())), "metrics":result, "full_answer_top3": full_answer, "matches_expected":True},indent=2))
if __name__ == "__main__":
    main()
