"""Behavioral checks on controlled payment patterns, plus input/output contracts."""

import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from backend.app.analytics import DataValidationError, run_pipeline, validate_frames
from backend.app.analytics.demo import generate_demo_frames, write_demo
from backend.app.analytics.pipeline import centralities, load_config, temporal_features


def make_data(directory: Path, nodes: list[tuple[int, int, bool]], transfers: list[tuple[int, int, str, float]]):
    directory.mkdir(parents=True, exist_ok=True)
    node_frame = pd.DataFrame(nodes, columns=["gid", "depth", "is_seed"])
    tx = pd.DataFrame(transfers, columns=["src", "dst", "date", "sum_kzt"])
    tx["date"] = pd.to_datetime(tx["date"])
    edges = tx.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
    depths = node_frame.set_index("gid")["depth"]
    edges["depth"] = edges["src"].map(depths).add(1).clip(1, 4)
    for name, frame in [("nodes", node_frame), ("edges", edges), ("transactions", tx)]:
        frame.to_parquet(directory / f"{name}.parquet", index=False)
    return node_frame, edges, tx


def analyze(tmp_path, nodes, transfers):
    data = tmp_path / "data"
    make_data(data, nodes, transfers)
    return run_pipeline(data, tmp_path / "outputs")


def profile(result, gid):
    return next(node for node in result.nodes if node["gid"] == gid)


def test_consolidator_receives_independent_sources(tmp_path):
    nodes = [(gid, 0, True) for gid in range(11, 18)] + [(90, 1, False)]
    result = analyze(tmp_path, nodes, [(gid, 90, "2026-07-02", 100000) for gid in range(11, 18)])
    node = profile(result, 90)
    assert node["role"] == "consolidator"
    assert node["metrics"]["fan_in"] == 7
    assert node["metrics"]["in_sum"] == 700000
    assert "7 отправителей" in node["evidence"] and "700 000" in node["evidence"]
    assert node["role_scores"]["consolidator"] > node["role_scores"]["terminal"]


def test_transit_requires_later_observed_payment(tmp_path):
    result = analyze(tmp_path, [(17, 0, True), (27, 1, False), (37, 2, False)],
                     [(17, 27, "2026-07-01", 100000), (27, 37, "2026-07-02", 95000)])
    node = profile(result, 27)
    assert node["role"] == "transit"
    assert node["metrics"]["rapid_transit_share"] == pytest.approx(0.95)
    assert "95%" in node["evidence"]
    assert node["metrics"]["retention_proxy"] == pytest.approx(0.05)


def test_distributor_sends_many_branches(tmp_path):
    nodes = [(100, 0, True), (200, 1, False)] + [(gid, 2, False) for gid in range(301, 308)]
    transfers = [(100, 200, "2026-07-01", 700000)] + [(200, gid, "2026-07-12", 100000) for gid in range(301, 308)]
    result = analyze(tmp_path, nodes, transfers)
    node = profile(result, 200)
    assert node["role"] == "distributor"
    assert node["metrics"]["out_degree"] == 7
    assert "7 получателей" in node["evidence"]


def test_boundary_is_not_terminal_and_isolates_survive(tmp_path):
    result = analyze(tmp_path, [(1, 0, True), (2, 4, False), (3, 0, True)], [(1, 2, "2026-07-01", 100000)])
    boundary, isolate = profile(result, 2), profile(result, 3)
    assert boundary["metrics"]["boundary_censored"] is True
    assert boundary["role"] != "terminal" and boundary["role_scores"]["terminal"] == 0
    assert "обрыв наблюдения" in boundary["evidence"]
    assert isolate["role"] == "peripheral"
    assert isolate["metrics"]["in_degree"] == isolate["metrics"]["out_degree"] == 0
    assert result.graph.has_node(3)
    assert len(pd.read_csv(tmp_path / "outputs" / "nodes_roles.csv")) == 3


def test_seed_balance_disabled_and_excess_outflow_supported(tmp_path):
    result = analyze(tmp_path, [(1, 0, True), (2, 1, False), (3, 2, False)],
                     [(1, 2, "2026-07-01", 10000), (2, 3, "2026-07-02", 100000), (3, 1, "2026-07-03", 5000)])
    seed, middle = profile(result, 1), profile(result, 2)
    assert seed["metrics"]["pass_through"] is None
    assert seed["metrics"]["retention_proxy"] is None
    assert seed["role_scores"]["transit"] == seed["role_scores"]["terminal"] == 0
    assert middle["metrics"]["pass_through"] == 10
    assert middle["metrics"]["retention_proxy"] == 0
    assert 0 <= middle["priority_score"] <= 1
    assert any("больше входящей" in value for value in middle["limitations"])


def test_temporal_matching_never_reuses_incoming_money():
    tx = pd.DataFrame([(1, 2, "2026-07-01", 100000), (2, 3, "2026-07-02", 90000),
                       (2, 4, "2026-07-02", 90000)], columns=["src", "dst", "date", "sum_kzt"])
    tx["date"] = pd.to_datetime(tx["date"])
    metrics = temporal_features(tx, [1, 2, 3, 4], 2)[2]
    assert metrics["rapid_transit_amount"] == 100000
    assert metrics["rapid_transit_share"] == 1


def test_temporal_matching_consumes_old_money_and_rejects_same_day_order():
    tx = pd.DataFrame([(1, 2, "2026-07-01", 100000), (1, 2, "2026-07-10", 100000),
                       (2, 3, "2026-07-11", 100000), (2, 3, "2026-07-12", 100000),
                       (1, 4, "2026-07-02", 100000), (4, 3, "2026-07-02", 100000)], columns=["src", "dst", "date", "sum_kzt"])
    tx["date"] = pd.to_datetime(tx["date"])
    metrics = temporal_features(tx, [1, 2, 3, 4], 2)
    assert metrics[2]["rapid_transit_amount"] == 100000
    assert metrics[2]["rapid_transit_share"] == 0.5
    assert metrics[4]["rapid_transit_share"] == 0


def test_multiple_components_never_share_cluster(tmp_path):
    result = analyze(tmp_path, [(1, 0, True), (2, 1, False), (10, 0, True), (11, 1, False), (12, 0, True)],
                     [(1, 2, "2026-07-01", 10000), (10, 11, "2026-07-01", 10000)])
    assert result.stats["n_components"] == 3
    by_cluster = {}
    for node in result.nodes:
        by_cluster.setdefault(node["cluster_id"], set()).add(node["metrics"]["component_id"])
    assert all(len(components) == 1 for components in by_cluster.values())


def test_csv_contracts_determinism_and_role_scores(tmp_path):
    data = tmp_path / "data"
    write_demo(data)
    first = run_pipeline(data, tmp_path / "first")
    # Row ordering has no influence on graph iteration, clustering or ties.
    for name in ("nodes", "edges", "transactions"):
        frame = pd.read_parquet(data / f"{name}.parquet").sample(frac=1, random_state=7)
        frame.to_parquet(data / f"{name}.parquet", index=False)
    second = run_pipeline(data, tmp_path / "second")
    assert first.nodes == second.nodes
    assert first.clusters == second.clusters
    required = {
        "nodes_roles": ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"],
        "clusters": ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"],
        "top_nodes": ["rank", "gid", "role", "priority_score", "why"],
    }
    for name, columns in required.items():
        a, b = tmp_path / "first" / f"{name}.csv", tmp_path / "second" / f"{name}.csv"
        assert a.read_bytes() == b.read_bytes()
        frame = pd.read_csv(a)
        assert set(columns).issubset(frame.columns)
        assert not frame[columns].isna().any().any()
        assert (frame["source"] == "synthetic").all()
    assert len(pd.read_csv(tmp_path / "first" / "top_nodes.csv")) >= 20
    for node in first.nodes:
        assert len(node["evidence"]) <= 200
        assert any(char.isdigit() for char in node["evidence"])
        assert 0 <= node["role_score"] <= 1 and 0 <= node["priority_score"] <= 1
        assert len(node["role_scores"]) == 6
        assert all(0 <= value <= 1 for value in node["role_scores"].values())
        assert node["role_scores"][node["role"]] == max(node["role_scores"].values())
        assert sum(factor["contribution"] for factor in node["priority_factors"]) == pytest.approx(node["priority_score"])
    metadata = json.loads((tmp_path / "first" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["source"] == "synthetic"
    assert len(metadata["input_sha256"]) == 3


@pytest.mark.parametrize("bad_value", [-1, np.nan, np.inf, "oops"])
def test_rejects_invalid_money(bad_value):
    nodes, edges, tx = generate_demo_frames()
    tx["sum_kzt"] = tx["sum_kzt"].astype(object)
    tx.loc[0, "sum_kzt"] = bad_value
    with pytest.raises(DataValidationError):
        validate_frames(nodes, edges, tx, load_config())


@pytest.mark.parametrize("issue", ["missing_column", "duplicate_node", "duplicate_edge", "unknown_node", "bad_date", "wrong_count", "wrong_sum", "bad_boolean", "fractional_id"])
def test_rejects_broken_schema_and_inconsistent_aggregates(issue):
    nodes, edges, tx = generate_demo_frames()
    if issue == "missing_column":
        nodes = nodes.drop(columns="is_seed")
    elif issue == "duplicate_node":
        nodes = pd.concat([nodes, nodes.iloc[[0]]])
    elif issue == "duplicate_edge":
        edges = pd.concat([edges, edges.iloc[[0]]])
    elif issue == "unknown_node":
        tx.loc[0, "dst"] = 999999
    elif issue == "bad_date":
        tx["date"] = tx["date"].astype(object)
        tx.loc[0, "date"] = "not-a-date"
    elif issue == "wrong_count":
        edges.loc[0, "n_tx"] += 1
    elif issue == "wrong_sum":
        edges.loc[0, "sum_kzt"] += 2
    elif issue == "bad_boolean":
        nodes["is_seed"] = nodes["is_seed"].astype(object)
        nodes.loc[0, "is_seed"] = "nope"
    elif issue == "fractional_id":
        nodes["gid"] = nodes["gid"].astype(float)
        nodes.loc[0, "gid"] = 1.5
    with pytest.raises(DataValidationError):
        validate_frames(nodes, edges, tx, load_config())


def test_identical_transaction_tuples_preserved_when_aggregates_match(tmp_path):
    result = analyze(tmp_path, [(1, 0, True), (2, 1, False)],
                     [(1, 2, "2026-07-01", 10000), (1, 2, "2026-07-01", 10000)])
    assert result.stats["n_transactions"] == 2
    assert result.stats["total_volume"] == 20000
    assert any("повторных комбинаций" in warning for warning in result.warnings)


def test_missing_real_files_fallback_but_partial_and_corruption_fail(tmp_path):
    result = run_pipeline(tmp_path / "missing", tmp_path / "outputs")
    assert result.source == "synthetic"
    assert any("СИНТЕТИЧЕСКИЕ" in warning for warning in result.warnings)
    partial = tmp_path / "partial"
    partial.mkdir()
    generate_demo_frames()[0].to_parquet(partial / "nodes.parquet")
    with pytest.raises(DataValidationError, match="Неполный набор"):
        run_pipeline(partial, tmp_path / "broken-output")
    for name in ("edges", "transactions"):
        (partial / f"{name}.parquet").write_text("broken", encoding="utf-8")
    with pytest.raises(DataValidationError, match="Не удалось прочитать"):
        run_pipeline(partial, tmp_path / "broken-output")


def test_empty_transactions_preserve_all_nodes(tmp_path):
    result = analyze(tmp_path, [(1, 0, True), (2, 1, False)], [])
    assert len(result.nodes) == 2
    assert result.stats["n_transactions"] == 0
    assert result.stats["n_components"] == 2
    assert result.timeline == []


def test_generated_demo_marking_and_mixed_files_rejected(tmp_path):
    data = tmp_path / "demo"
    write_demo(data)
    result = run_pipeline(data, tmp_path / "out")
    assert result.source == "synthetic"
    frame = pd.read_parquet(data / "nodes.parquet").drop(columns="__synthetic__")
    frame.to_parquet(data / "nodes.parquet", index=False)
    with pytest.raises(DataValidationError, match="Смешаны"):
        run_pipeline(data, tmp_path / "out")
    with pytest.raises(FileExistsError):
        write_demo(data)


def test_centrality_power_iterations_match_networkx_reference():
    graph = nx.DiGraph()
    graph.add_nodes_from([1, 2, 3, 4, 5])
    for src, dst, amount in [(1, 2, 5000), (1, 3, 10000), (2, 3, 8000), (3, 4, 10000), (4, 2, 9000)]:
        graph.add_edge(src, dst, sum_kzt=float(amount), weight=float(np.log1p(amount)))
    rank, _, hub, authority = centralities(graph, load_config())
    expected_rank = nx.pagerank(graph, alpha=0.85, weight="sum_kzt", max_iter=1000, tol=1e-12)
    expected_hub, expected_authority = nx.hits(graph, max_iter=1000, tol=1e-12, normalized=True)
    assert rank == pytest.approx(expected_rank, abs=1e-8)
    assert hub == pytest.approx(expected_hub, abs=1e-7)
    assert authority == pytest.approx(expected_authority, abs=1e-7)
    assert hub[5] == authority[5] == 0


def test_large_identifiers_are_preserved_in_pipeline_and_csv(tmp_path):
    first, second = 9007199254740993, 9007199254740997
    result = analyze(tmp_path, [(first, 0, True), (second, 1, False)], [(first, second, "2026-07-01", 10000)])
    assert [node["gid"] for node in result.nodes] == [first, second]
    assert result.edges[0]["src"] == first
    csv = pd.read_csv(tmp_path / "outputs" / "nodes_roles.csv", dtype={"gid": "int64"})
    assert csv["gid"].tolist() == [first, second]


def test_float_identifiers_beyond_exact_range_are_rejected():
    nodes, edges, tx = generate_demo_frames()
    nodes["gid"] = nodes["gid"].astype(float)
    nodes.loc[0, "gid"] = float(9007199254740993)
    with pytest.raises(DataValidationError, match="точность могла быть потеряна"):
        validate_frames(nodes, edges, tx, load_config())


def test_exact_decimal_string_identifiers_are_supported(tmp_path):
    data = tmp_path / "data"
    first, second = 9007199254740993, 9007199254740997
    make_data(data, [(first, 0, True), (second, 1, False)], [(first, second, "2026-07-01", 10000)])
    for name, columns in [("nodes", ["gid"]), ("edges", ["src", "dst"]), ("transactions", ["src", "dst"])]:
        frame = pd.read_parquet(data / f"{name}.parquet")
        for column in columns:
            frame[column] = frame[column].astype(str)
        frame.to_parquet(data / f"{name}.parquet", index=False)
    result = run_pipeline(data, tmp_path / "outputs")
    assert [node["gid"] for node in result.nodes] == [first, second]


def test_numeric_dates_are_not_silently_treated_as_nanoseconds():
    nodes, edges, tx = generate_demo_frames()
    tx["date"] = np.arange(len(tx))
    with pytest.raises(DataValidationError, match="числовые даты неоднозначны"):
        validate_frames(nodes, edges, tx, load_config())


def test_methodology_and_temporal_evidence_follow_config(tmp_path, monkeypatch):
    from backend.app.analytics import pipeline
    config = load_config()
    config["rapid_window_days"] = 5
    config["role_thresholds"]["consolidator_min_in"] = 7
    config["role_thresholds"]["distributor_min_out"] = 8
    config["clustering"]["resolution"] = 1.25
    config["centrality"]["betweenness_sample"] = 128
    config["role_confidence"]["base_strength"] = 0.7
    config["role_confidence"]["margin_weight"] = 0.3
    monkeypatch.setattr(pipeline, "load_config", lambda: config)
    result = analyze(tmp_path, [(1, 0, True), (2, 1, False), (3, 2, False)],
                     [(1, 2, "2026-07-01", 100000), (2, 3, "2026-07-05", 100000)])
    node = profile(result, 2)
    assert node["role"] == "transit" and node["metrics"]["rapid_transit_share"] == 1
    assert "≤5 суток" in node["evidence"]
    assert any("5 суток" in text for text in node["detailed_evidence"])
    method = result.methodology
    descriptions = {role["role"]: role["description"] for role in method["roles"]}
    assert "7 отправителей" in descriptions["consolidator"]
    assert "8 получателей" in descriptions["distributor"]
    assert "5 суток" in descriptions["transit"]
    assert "resolution=1.25" in method["clustering"]
    assert "128 узлов" in method["centralities"]
    assert "0.70 × лучший" in method["role_formula"]


def test_self_loops_are_preserved_without_fake_temporal_transit(tmp_path):
    result = analyze(tmp_path, [(1, 0, True), (2, 1, False)],
                     [(1, 2, "2026-07-01", 100000), (2, 2, "2026-07-02", 100000)])
    node = profile(result, 2)
    assert result.graph.has_edge(2, 2)
    assert result.stats["n_transactions"] == 2
    assert result.stats["total_volume"] == 200000
    assert node["metrics"]["in_cycle"] is True
    assert node["metrics"]["rapid_transit_amount"] == 0
    assert node["role"] != "transit"
    assert sum(cluster["sum_kzt_internal"] for cluster in result.clusters) == 200000


def test_self_loop_cannot_supply_an_independent_source_or_recipient(tmp_path):
    result = analyze(tmp_path, [(1, 0, True), (2, 0, True), (3, 1, False), (4, 2, False), (5, 2, False)],
                     [(1, 3, "2026-07-01", 100000), (2, 3, "2026-07-01", 100000),
                      (3, 3, "2026-07-01", 200000), (3, 4, "2026-07-01", 50000), (3, 5, "2026-07-01", 50000)])
    node = profile(result, 3)
    assert node["metrics"]["in_degree"] == node["metrics"]["out_degree"] == 3
    assert node["metrics"]["fan_in"] == node["metrics"]["fan_out"] == 2
    assert node["role_scores"]["consolidator"] == node["role_scores"]["distributor"] == 0
    assert any("не считается независимым" in text for text in node["limitations"])
