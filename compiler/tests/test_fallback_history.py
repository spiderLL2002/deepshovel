from concurrent.futures import ThreadPoolExecutor

from compiler_service.fallback_history import FallbackHistory


def test_history_is_atomic_concurrent_and_survives_restart(tmp_path):
    import json

    path = tmp_path / "model-fallback.json"
    history = FallbackHistory(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: history.record(), range(20)))
    FallbackHistory(path).record()
    state = json.loads(path.read_text())
    assert state["count"] == 21
    assert state["lastActivatedAt"].endswith("+00:00")
    assert not path.with_suffix(".tmp").exists()
