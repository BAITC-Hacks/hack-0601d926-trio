"""Acceptance checks on the supplied parquet, independent of individual roles."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.analytics import run_pipeline

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def real_result(tmp_path_factory):
    if not all((ROOT / 'data' / f'{name}.parquet').is_file() for name in ('nodes', 'edges', 'transactions')):
        pytest.skip('The supplied real dataset is not available')
    output = tmp_path_factory.mktemp('real-acceptance')
    return run_pipeline(ROOT / 'data', output), output


def test_all_real_clients_in_exports_and_isolates_preserved(real_result):
    result, output = real_result
    source_nodes = pd.read_parquet(ROOT / 'data/nodes.parquet')
    source_edges = pd.read_parquet(ROOT / 'data/edges.parquet')
    exported = pd.read_csv(output / 'nodes_roles.csv')
    assert set(exported.gid) == set(source_nodes.gid)
    assert len(exported) == len(source_nodes)
    isolated = set(source_nodes.gid) - set(source_edges.src) - set(source_edges.dst)
    by_gid = {node['gid']: node for node in result.nodes}
    for gid in isolated:
        assert by_gid[gid]['role'] == 'peripheral'
        assert by_gid[gid]['metrics']['in_degree'] == by_gid[gid]['metrics']['out_degree'] == 0


def test_scores_follow_the_exposed_evidence_and_factor_contract(real_result):
    result, _ = real_result
    for node in result.nodes:
        assert node['role_scores'][node['role']] == max(node['role_scores'].values())
        assert 0 <= node['role_score'] <= 1
        assert 0 <= node['priority_score'] <= 1
        assert len(node['evidence']) <= 200
        assert any(character.isdigit() for character in node['evidence'])
        assert np.isclose(sum(f['contribution'] for f in node['priority_factors']), node['priority_score'])
        if node['depth'] == 4 and node['metrics']['out_degree'] == 0:
            assert node['role'] != 'terminal'
            assert node['metrics']['boundary_censored']
        if node['is_seed']:
            assert node['metrics']['pass_through'] is None
            assert node['metrics']['retention_proxy'] is None


def test_clusters_and_top_list_account_for_observed_graph(real_result):
    result, output = real_result
    assert sum(c['n_nodes'] for c in result.clusters) == len(result.nodes)
    assert sum(c['n_seed'] for c in result.clusters) == result.stats['n_seed']
    by_gid = {node['gid']: node for node in result.nodes}
    by_cluster = {cluster['cluster_id']: cluster for cluster in result.clusters}
    for cid, cluster in by_cluster.items():
        members = [n for n in result.nodes if n['cluster_id'] == cid]
        assert len({n['metrics']['component_id'] for n in members}) == 1
        assert all(by_gid[gid]['cluster_id'] == cid for gid in cluster['top_gids'])
    expected_internal = sum(edge['sum_kzt'] for edge in result.edges
                            if by_gid[edge['src']]['cluster_id'] == by_gid[edge['dst']]['cluster_id'])
    assert np.isclose(sum(c['sum_kzt_internal'] for c in result.clusters), expected_internal)
    top = pd.read_csv(output / 'top_nodes.csv')
    assert len(top) >= min(20, len(result.nodes))
    assert top.priority_score.is_monotonic_decreasing
    assert top['rank'].tolist() == list(range(1, len(top) + 1))
    assert set(top.gid).issubset(by_gid)
    assert result.duration_seconds < 300
