"""Contract, failure-safety and full local investigation smoke tests."""

import csv
from dataclasses import replace
from io import StringIO
from threading import Event

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.app.agent.tools import LocalTools, ToolInputError
from backend.app.main import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("api")
    app = create_app(root / "missing-data", root / "outputs")
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").status_code == 200, app.state.store.error
        yield test_client


def test_overview_exports_and_all_documented_endpoints(client):
    health = client.get("/api/health").json()
    assert health["ready"] and health["source"] == "synthetic"
    overview = client.get("/api/overview").json()
    assert overview["n_nodes"] >= 20
    assert overview["n_isolated"] > 0
    assert overview["source"] == "synthetic"
    assert overview["timeline"]
    assert sum(overview["roles"].values()) == overview["n_nodes"]
    for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
        response = client.get(f"/api/exports/{filename}")
        assert response.status_code == 200
        assert response.headers["x-data-source"] == "synthetic"
        rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
        assert rows
        if filename == "nodes_roles.csv":
            assert len(rows) == overview["n_nodes"]
        if filename == "top_nodes.csv":
            assert len(rows) >= 20
    assert client.get("/api/methodology").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/api/exports/metadata.json").status_code == 404
    assert client.get("/api/unknown").status_code == 404


def test_investigation_end_to_end(client):
    response = client.post("/api/recalculate")
    assert response.status_code == 202
    assert response.json()["status"] in ("running", "completed")
    client.app.state.store.worker.join(timeout=30)
    status = client.get("/api/recalculate/status").json()
    assert status["status"] == "completed", status
    top = client.get("/api/top-nodes?limit=20").json()
    assert len(top) == 20
    gid = top[0]["gid"]
    node = client.get(f"/api/nodes/{gid}").json()
    assert node["gid"] == gid and node["evidence"]
    graph = client.get(f"/api/nodes/{gid}/ego?depth=2&direction=both").json()
    assert gid in {item["gid"] for item in graph["nodes"]}
    assert all(edge["src"] in {n["gid"] for n in graph["nodes"]} for edge in graph["edges"])
    answer = client.post("/api/copilot", json={"message": f"Почему gid {gid} находится в топе?"}).json()
    assert answer["mode"] == "local"
    assert answer["tools_used"][0]["name"] == "explain_priority"
    assert answer["tools_used"][0]["parameters"] == {"gid": gid}
    assert f"{node['priority_score']:.3f}" in " ".join(answer["observations"])
    assert answer["hypotheses"] and answer["limitations"]
    assert {"type": "node", "id": gid, "label": f"gid {gid}"} in answer["links"]


def test_search_filters_sorting_pagination_and_invalid_input(client):
    top = client.get("/api/top-nodes?limit=1").json()[0]
    page = client.get("/api/nodes", params={"q": str(top["gid"]), "role": top["role"],
                                            "is_seed": top["is_seed"], "cluster_id": top["cluster_id"],
                                            "depth": top["depth"]}).json()
    assert top["gid"] in [n["gid"] for n in page["items"]]
    first = client.get("/api/nodes?page_size=5&page=1&sort_by=gid&sort_dir=asc").json()
    second = client.get("/api/nodes?page_size=5&page=2&sort_by=gid&sort_dir=asc").json()
    assert len(first["items"]) == len(second["items"]) == 5
    assert max(int(n["gid"]) for n in first["items"]) < min(int(n["gid"]) for n in second["items"])
    empty = client.get("/api/nodes?q=99999999999999999999").json()
    assert empty["items"] == [] and empty["total"] == 0
    for url in ("/api/nodes?page=0", "/api/nodes?role=criminal", "/api/nodes?sort_by=__class__",
                "/api/nodes?page_size=100000", "/api/graph?min_amount=100&max_amount=10",
                "/api/graph?min_amount=nan", f"/api/nodes/{top['gid']}/ego?depth=100"):
        assert client.get(url).status_code == 422, url
    assert client.get("/api/nodes/99999999999999").status_code == 404
    assert client.post("/api/copilot", json={"message": "   "}).status_code == 422
    assert client.post("/api/copilot", json={"message": "топ", "arbitrary_code": "exec(1)"}).status_code == 422


def test_graph_cluster_aggregation_and_bounded_filtering(client):
    graph = client.get("/api/graph?limit=5").json()
    assert len(graph["nodes"]) == 5 and graph["truncated"]
    empty = client.get("/api/graph?min_amount=99999999999999").json()
    assert empty["nodes"] == [] and empty["edges"] == []
    clusters = client.get("/api/clusters").json()
    aggregate = client.get("/api/clusters/graph").json()
    assert aggregate["nodes"] == clusters
    for edge in aggregate["edges"]:
        assert edge["src"] != edge["dst"] and edge["sum_kzt"] > 0
    for cluster in clusters:
        cid = cluster["cluster_id"]
        assert client.get(f"/api/clusters/{cid}").json() == cluster
        assert all(n["cluster_id"] == cid for n in client.get(f"/api/graph?cluster_id={cid}").json()["nodes"])
    assert client.get("/api/clusters/99999999").status_code == 404


def test_cached_requests_and_failed_recalculation_preserve_snapshot(client, monkeypatch):
    import backend.app.analytics as analytics

    before = client.get("/api/top-nodes?limit=1").json()
    export_before = client.get("/api/exports/nodes_roles.csv").content

    def fail(*args, **kwargs):
        raise ValueError("Контролируемая ошибка повреждённых входных данных")

    monkeypatch.setattr(analytics, "run_pipeline", fail)
    # Reads must not touch the pipeline.
    assert client.get("/api/overview").status_code == 200
    assert client.get("/api/top-nodes?limit=1").json() == before
    assert client.post("/api/recalculate").status_code == 202
    client.app.state.store.worker.join(timeout=10)
    assert client.get("/api/recalculate/status").json()["status"] == "failed"
    assert client.get("/api/top-nodes?limit=1").json() == before
    assert client.get("/api/exports/nodes_roles.csv").content == export_before


def test_invalid_initial_data_keeps_diagnostics_available(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "nodes.parquet").write_bytes(b"invalid parquet")
    with TestClient(create_app(directory, tmp_path / "outputs")) as invalid:
        health = invalid.get("/api/health")
        assert health.status_code == 503 and not health.json()["ready"]
        assert health.json()["error"]
        assert invalid.get("/api/overview").status_code == 503
        assert invalid.get("/api/recalculate/status").json()["status"] == "failed"


def test_copilot_id_lists_context_and_all_safe_tools(client):
    snapshot = client.app.state.store.snapshot
    tools = LocalTools(snapshot)
    gids = [n["gid"] for n in snapshot.nodes[:2]]
    gid, second_gid = gids
    compare = client.post("/api/copilot", json={"message": f"Сравни gid {gid} и {second_gid}"}).json()
    assert compare["tools_used"][0]["name"] == "compare_nodes"
    assert compare["tools_used"][0]["parameters"]["gids"] == [str(g) for g in gids]
    assert len(compare["observations"]) == 2
    common = client.post("/api/copilot", json={"message": f"Кто собирает деньги с gid {gid},{second_gid}?"}).json()
    assert common["tools_used"][0]["name"] == "find_common_recipients"
    assert common["tools_used"][0]["parameters"]["gids"] == [str(g) for g in gids]
    contextual = client.post("/api/copilot", json={"message": "Почему он в топе?", "context_gid": gid}).json()
    assert contextual["tools_used"][0]["parameters"] == {"gid": str(gid)}
    top = client.post("/api/copilot", json={"message": "Покажи топ 20"}).json()
    assert top["tools_used"][0]["name"] == "get_top_nodes"
    cases = {
        "get_node_profile": {"gid": gid}, "get_neighbors": {"gid": gid, "direction": "in"},
        "trace_money_flow": {"gid": gid}, "find_common_recipients": {"gids": gids},
        "find_shared_senders": {"gids": gids}, "compare_nodes": {"gids": gids},
        "get_cluster_profile": {"cluster_id": snapshot.nodes[0]["cluster_id"]},
        "get_top_nodes": {"bridge_only": True}, "explain_priority": {"gid": gid},
        "suggest_next_data_request": {"gid": gid},
    }
    for name, parameters in cases.items():
        assert isinstance(tools.invoke(name, parameters), dict)
    with pytest.raises(ToolInputError):
        tools.invoke("execute_python", {"code": "1+1"})
    with pytest.raises(ValidationError):
        tools.invoke("trace_money_flow", {"gid": gid, "max_depth": 100})
    with pytest.raises(ValidationError):
        tools.invoke("get_node_profile", {"gid": str(gid)})
    unknown = client.post("/api/copilot", json={"message": "Покажи связи gid 9999999999999"}).json()
    assert "не найден" in unknown["answer"] and unknown["links"] == []


def test_copilot_boundary_warning_and_cors(client):
    boundary = next(n for n in client.app.state.store.snapshot.nodes if n["metrics"]["boundary_censored"])
    answer = client.post("/api/copilot", json={"message": f"Куда дальше уходят деньги от gid {boundary['gid']}?"}).json()
    assert answer["limitations"]
    assert any("4" in line or "четв" in line or "границ" in line.lower() for line in answer["limitations"])
    allowed = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.get("/api/health", headers={"Origin": "https://example.org"})
    assert "access-control-allow-origin" not in denied.headers


def test_large_client_ids_roundtrip_without_javascript_precision_loss(tmp_path):
    from backend.app.analytics.demo import generate_demo_frames

    directory = tmp_path / "large-identifiers"
    directory.mkdir()
    node_frame, edge_frame, tx_frame = generate_demo_frames()
    offset = 100000004400305100
    node_frame["gid"] = node_frame["gid"] + offset
    for frame in (edge_frame, tx_frame):
        frame["src"] = frame["src"] + offset
        frame["dst"] = frame["dst"] + offset
    for name, frame in (("nodes", node_frame), ("edges", edge_frame), ("transactions", tx_frame)):
        frame.to_parquet(directory / f"{name}.parquet", index=False)
    (directory / "SYNTHETIC_DEMO.txt").write_text("Test fixture", encoding="utf-8")
    with TestClient(create_app(directory, tmp_path / "outputs")) as precise:
        assert precise.get("/api/health").status_code == 200
        expected = {str(value) for value in node_frame["gid"]}
        graph = precise.get("/api/graph?limit=100").json()
        assert {n["gid"] for n in graph["nodes"]} == expected
        assert all(isinstance(e["src"], str) and isinstance(e["dst"], str) for e in graph["edges"])
        gid = str(offset + 1)
        assert precise.get(f"/api/nodes/{gid}").json()["gid"] == gid
        assert precise.get("/api/nodes", params={"q": gid}).json()["items"][0]["gid"] == gid
        answer = precise.post("/api/copilot", json={"message": "Покажи связи", "context_gid": gid}).json()
        assert answer["tools_used"][0]["parameters"]["gid"] == gid
        assert all(isinstance(link["id"], str) and link["id"] in expected for link in answer["links"])
        for cluster in precise.get("/api/clusters").json():
            assert all(isinstance(value, str) and value in expected for value in cluster["top_gids"])
        assert all(isinstance(value, int) for value in precise.app.state.store.snapshot.by_gid)


def test_flow_tool_preserves_self_loops_and_reports_pruned_branches(client):
    snapshot = client.app.state.store.snapshot
    gids = list(snapshot.by_gid)[:4]
    root = gids[0]
    loop = {"src": root, "dst": root, "sum_kzt": 50000.0, "n_tx": 1, "depth": 1}
    loop_tools = LocalTools(replace(snapshot, edges=[loop]))
    result = loop_tools.invoke("trace_money_flow", {"gid": root})
    assert result["paths"][0]["gids"] == [root, root]
    assert result["paths"][0]["cycle"] and not result["truncated"]
    branches = [{**loop, "dst": gid} for gid in gids[1:]]
    branch_tools = LocalTools(replace(snapshot, edges=branches))
    result = branch_tools.invoke("trace_money_flow", {"gid": root, "limit_paths": 1})
    assert len(result["paths"]) == 1 and result["truncated"]


def test_duplicate_recalculation_uses_single_job_and_reads_stay_available(client, monkeypatch):
    import backend.app.analytics as analytics

    original_pipeline = analytics.run_pipeline
    entered, release = Event(), Event()
    calls = []
    before = client.get("/api/top-nodes?limit=1").json()

    def delayed(data_dir, output_dir):
        calls.append(True)
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("Test failed to release calculation")
        return original_pipeline(data_dir, output_dir)

    monkeypatch.setattr(analytics, "run_pipeline", delayed)
    try:
        assert client.post("/api/recalculate").status_code == 202
        assert entered.wait(timeout=5)
        assert client.post("/api/recalculate").json()["status"] == "running"
        assert client.get("/api/top-nodes?limit=1").json() == before
        assert client.get("/api/overview").status_code == 200
        assert client.get("/api/exports/nodes_roles.csv").status_code == 200
        assert len(calls) == 1
    finally:
        release.set()
        client.app.state.store.worker.join(timeout=20)
    assert client.get("/api/recalculate/status").json()["status"] == "completed"
