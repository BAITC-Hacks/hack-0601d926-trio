"""Observed graph → interpretable hypotheses; no trained model and no gid rules."""

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from .validation import DataValidationError, load_data

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(__file__).with_name("config.json")
LOGGER = logging.getLogger(__name__)
ROLE_LABELS = {
    "consolidator": "Консолидатор", "transit": "Транзит", "distributor": "Распределитель",
    "terminal": "Наблюдаемый получатель", "coordinator": "Кандидат в координаторы", "peripheral": "Периферия",
}
BASE_LIMITATIONS = [
    "Входящие суммы seed занижены конструкцией выборки: pass-through и удержание для них не рассчитываются и не участвуют в балансовых правилах ролей.",
    "Исходящий поток может превышать входящий за счёт неизвестного остатка и внешних поступлений; это не ошибка и не признак нарушения само по себе.",
    "Некоторые seed изолированы или видны только как получатели; все nodes сохраняются, компоненты не объединяются искусственно.",
    "Нет ФИО, ИИН, доходов, остатков, назначения платежа и подтверждённых ролей; используются только наблюдаемые связи и прозрачные эвристики.",
    "Совпадение суммы и даты не доказывает дубль без ID операции. Идентичные транзакции сохраняются при согласованности с агрегированными рёбрами.",
    "При датах без времени операции одного дня не сопоставляются: порядок неизвестен. Конец периода может скрывать последующее движение средств.",
    "Роли, кластеры и приоритет — аналитические гипотезы для проверки, не доказательство виновности. role_score — сила и однозначность признаков, не вероятность.",
]


def data_limitations(config: dict) -> list[str]:
    return [
        f"Граф получен только по исходящим переводам от seed до {config['max_depth']} колен; это не полный финансовый граф клиентов.",
        f"Для depth={config['max_depth']} отсутствие исходящих переводов может означать обрыв наблюдения; само по себе оно не доказывает конечного получателя.",
        f"Переводы менее {config['min_transaction_kzt']:,.0f} KZT и операции за пределами банка отсутствуют; полный баланс и источники средств неизвестны.".replace(",", " "),
        f"Временной транзит — FIFO-сопоставление доступных поступлений с более поздними списаниями до {config['rapid_window_days']} суток, без повторного использования суммы. Это совместимость потоков, а не трассировка конкретных денег.",
        *BASE_LIMITATIONS,
    ]


def role_descriptions(config: dict) -> dict[str, str]:
    t = config["role_thresholds"]
    return {
        "consolidator": f"Не менее {t['consolidator_min_in']} отправителей; сочетание fan-in, входящего оборота, структурной агрегации и наблюдаемого удержания.",
        "transit": f"Двусторонний поток и не менее {t['transit_min_rapid_share']:.0%} поступлений, сопоставленных с более поздним списанием в течение {config['rapid_window_days']} суток; у seed эта балансовая эвристика отключена.",
        "distributor": f"Не менее {t['distributor_min_out']} получателей; fan-out, исходящий оборот, распределение по контрагентам и HITS hub.",
        "terminal": f"Исходящие / входящие не выше {t['terminal_max_pass_through']:.0%}; запрещено для seed и depth={config['max_depth']}.",
        "coordinator": f"Не менее {t['coordinator_min_branches']} связей, {t['coordinator_min_seed_groups']} связанных seed, центральность ≥{t['coordinator_min_centrality']:.2f}, межкластерные связи и входящий+исходящий поток; гипотеза о позиции в сети.",
        "peripheral": "Недостаточно выраженных признаков остальных ролей; включая узлы с обрывом наблюдения.",
    }


@dataclass
class AnalysisResult:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    clusters: list[dict[str, Any]]
    stats: dict[str, Any]
    warnings: list[str]
    methodology: dict[str, Any]
    duration_seconds: float
    source: str
    graph: nx.DiGraph
    timeline: list[dict[str, Any]] = field(default_factory=list)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _clip(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def percentile(values: dict[int, float]) -> dict[int, float]:
    """Zeros stay zero; tied positive observations share their midrank."""
    if not values:
        return {}
    series = pd.Series(values, dtype=float)
    positive = series > 0
    result = pd.Series(0.0, index=series.index)
    if positive.any():
        result.loc[positive] = series.loc[positive].rank(method="average", pct=True)
    return {int(gid): float(value) for gid, value in result.items()}


def build_graph(nodes: pd.DataFrame, edges: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    for row in nodes.itertuples(index=False):
        graph.add_node(int(row.gid), depth=int(row.depth), is_seed=bool(row.is_seed))
    for row in edges.itertuples(index=False):
        graph.add_edge(int(row.src), int(row.dst), sum_kzt=float(row.sum_kzt), n_tx=int(row.n_tx), depth=int(row.depth))
    return graph


def centralities(graph: nx.DiGraph, config: dict[str, Any]) -> tuple[dict, dict, dict, dict]:
    """Deterministic sparse PageRank/HITS power iteration; no scipy requirement."""
    gids = list(graph)
    n = len(gids)
    lookup = {gid: i for i, gid in enumerate(gids)}
    src = np.array([lookup[a] for a, _ in graph.edges], dtype=int)
    dst = np.array([lookup[b] for _, b in graph.edges], dtype=int)
    amounts = np.array([d["sum_kzt"] for _, _, d in graph.edges(data=True)], dtype=float)
    cfg = config["centrality"]
    out_sums = np.bincount(src, weights=amounts, minlength=n)
    probabilities = amounts / out_sums[src] if len(src) else amounts
    rank = np.full(n, 1 / n)
    alpha = cfg["pagerank_alpha"]
    for _ in range(cfg["iterations"]):
        updated = np.full(n, (1 - alpha + alpha * rank[out_sums == 0].sum()) / n)
        np.add.at(updated, dst, alpha * rank[src] * probabilities)
        delta = np.abs(updated - rank).sum()
        rank = updated
        if delta < cfg["tolerance"]:
            break
    weights = np.log1p(amounts)
    hub = np.full(n, 1 / np.sqrt(n))
    authority = np.zeros(n)
    for _ in range(cfg["iterations"]):
        authority = np.bincount(dst, weights=weights * hub[src], minlength=n)
        norm = np.linalg.norm(authority)
        authority = authority / norm if norm else authority
        updated_hub = np.bincount(src, weights=weights * authority[dst], minlength=n)
        norm = np.linalg.norm(updated_hub)
        updated_hub = updated_hub / norm if norm else updated_hub
        delta = np.abs(updated_hub - hub).sum()
        hub = updated_hub
        if delta < cfg["tolerance"]:
            break
    hub[hub < cfg["hits_epsilon"]] = 0.0
    authority[authority < cfg["hits_epsilon"]] = 0.0
    hub = hub / hub.sum() if hub.sum() else hub
    authority = authority / authority.sum() if authority.sum() else authority
    sample = cfg["betweenness_sample"]
    between = nx.betweenness_centrality(graph, k=sample if n > sample else None, normalized=True,
                                      weight=None, seed=config["random_seed"])
    return (dict(zip(gids, map(float, rank))), between, dict(zip(gids, map(float, hub))), dict(zip(gids, map(float, authority))))


def cluster_graph(graph: nx.DiGraph, config: dict[str, Any]) -> tuple[dict[int, int], dict[int, int]]:
    cfg = config["clustering"]
    component_map: dict[int, int] = {}
    communities = []
    for component_id, members in enumerate(sorted(nx.weakly_connected_components(graph), key=lambda c: min(c))):
        for gid in members:
            component_map[gid] = component_id
        if len(members) < cfg["min_louvain_size"]:
            communities.append(set(members))
            continue
        projected = nx.Graph()
        projected.add_nodes_from(sorted(members))
        for src, dst, data in graph.subgraph(sorted(members)).edges(data=True):
            weight = float(np.log1p(data["sum_kzt"]) + cfg["transaction_weight"] * np.log1p(data["n_tx"]))
            existing = projected.get_edge_data(src, dst, {}).get("weight", 0.0)
            projected.add_edge(src, dst, weight=existing + weight)
        communities.extend(nx.community.louvain_communities(projected, weight="weight", resolution=cfg["resolution"], seed=config["random_seed"]))
    cluster_map = {gid: cid for cid, members in enumerate(sorted(communities, key=lambda c: min(c))) for gid in sorted(members)}
    return cluster_map, component_map


def temporal_features(transactions: pd.DataFrame, gids: list[int], window_days: int) -> dict[int, dict]:
    incoming: dict[int, list] = defaultdict(list)
    outgoing: dict[int, list] = defaultdict(list)
    amounts: dict[int, list] = defaultdict(list)
    active: dict[int, set] = defaultdict(set)
    for row in transactions.itertuples(index=False):
        src, dst, date, amount = int(row.src), int(row.dst), row.date, float(row.sum_kzt)
        amounts[src].append(amount)
        active[src].add(date.date())
        if dst != src:
            amounts[dst].append(amount)
            active[dst].add(date.date())
            incoming[dst].append((date, amount))
            outgoing[src].append((date, amount))
    result = {}
    for gid in gids:
        # Outgoing events sort before incoming at the same timestamp, avoiding an invented intra-day order.
        events = sorted([(date, 1, amount) for date, amount in incoming[gid]] + [(date, 0, amount) for date, amount in outgoing[gid]])
        queue: deque[list] = deque()
        matched = 0.0
        matched_any_age = 0.0
        for date, kind, amount in events:
            if kind == 1:
                queue.append([date, amount])
                continue
            remaining = amount
            while remaining > 0 and queue:
                received_at, balance = queue[0]
                allocated = min(remaining, balance)
                age = (date - received_at).total_seconds() / 86400
                if 0 < age <= window_days:
                    matched += allocated
                matched_any_age += allocated
                remaining -= allocated
                queue[0][1] -= allocated
                if queue[0][1] <= 0:
                    queue.popleft()
        total_in = sum(amount for _, amount in incoming[gid])
        result[gid] = {
            "avg_amount": float(np.mean(amounts[gid])) if amounts[gid] else 0.0,
            "median_amount": float(np.median(amounts[gid])) if amounts[gid] else 0.0,
            "active_days": len(active[gid]),
            "rapid_transit_share": _clip(matched / total_in) if total_in else 0.0,
            "rapid_transit_amount": matched,
            "temporal_matched_share": _clip(matched_any_age / total_in) if total_in else 0.0,
        }
    return result


def compute_metrics(graph: nx.DiGraph, nodes: pd.DataFrame, tx: pd.DataFrame, clusters: dict, components: dict, config: dict) -> dict[int, dict]:
    gids = list(graph)
    pagerank, between, hubs, authorities = centralities(graph, config)
    temporal = temporal_features(tx, gids, config["rapid_window_days"])
    seeds = {int(row.gid) for row in nodes.itertuples(index=False) if row.is_seed}
    incoming_seeds: dict[int, set[int]] = {gid: set() for gid in gids}
    downstream_seeds: dict[int, set[int]] = {gid: set() for gid in gids}
    distances: dict[int, int] = {}
    reverse = graph.reverse(copy=False)
    for seed in sorted(seeds):
        for gid, distance in nx.single_source_shortest_path_length(graph, seed).items():
            distances[gid] = min(distance, distances.get(gid, distance))
            if gid != seed:
                incoming_seeds[gid].add(seed)
        for gid in nx.single_source_shortest_path_length(reverse, seed):
            if gid != seed:
                downstream_seeds[gid].add(seed)
    cycle_nodes: set[int] = set()
    for members in nx.strongly_connected_components(graph):
        if len(members) > 1:
            cycle_nodes.update(members)
        else:
            gid = next(iter(members))
            if graph.has_edge(gid, gid):
                cycle_nodes.add(gid)
    result = {}
    for row in nodes.itertuples(index=False):
        gid = int(row.gid)
        in_edges = list(graph.in_edges(gid, data=True))
        out_edges = list(graph.out_edges(gid, data=True))
        in_sum = float(sum(d["sum_kzt"] for _, _, d in in_edges))
        out_sum = float(sum(d["sum_kzt"] for _, _, d in out_edges))
        in_tx = int(sum(d["n_tx"] for _, _, d in in_edges))
        out_tx = int(sum(d["n_tx"] for _, _, d in out_edges))
        in_degree, out_degree = int(graph.in_degree(gid)), int(graph.out_degree(gid))
        observed_ratio = out_sum / in_sum if in_sum > 0 and not row.is_seed else None
        counterparties: dict[int, float] = defaultdict(float)
        for src, _, data in in_edges:
            counterparties[src] += data["sum_kzt"]
        for _, dst, data in out_edges:
            counterparties[dst] += data["sum_kzt"]
        turnover = in_sum + out_sum
        concentration = sum((value / turnover) ** 2 for value in counterparties.values()) if turnover else 0.0
        outgoing_concentration = sum((data["sum_kzt"] / out_sum) ** 2 for _, _, data in out_edges) if out_sum else 0.0
        bridge_edges = sum(1 for src, dst, _ in in_edges + out_edges if clusters[src] != clusters[dst])
        bridge = bridge_edges / (in_degree + out_degree) if in_degree + out_degree else 0.0
        links = incoming_seeds[gid] | downstream_seeds[gid] | (set(graph.predecessors(gid)) & seeds) | (set(graph.successors(gid)) & seeds)
        links.discard(gid)
        repeated = sum(data["n_tx"] for _, _, data in in_edges + out_edges if data["n_tx"] > 1)
        reciprocal = sum(data["sum_kzt"] for src, dst, data in out_edges if graph.has_edge(dst, src))
        boundary = int(row.depth) == config["max_depth"]
        quality_config = config["observation_quality"]
        quality = quality_config["isolated_factor"] if not turnover else quality_config["sparse_factor"] if in_tx + out_tx < quality_config["sparse_tx_threshold"] else 1.0
        quality *= quality_config["seed_factor"] if row.is_seed else 1.0
        quality *= quality_config["boundary_factor"] if boundary else 1.0
        result[gid] = {
            "in_degree": in_degree, "out_degree": out_degree, "in_sum": in_sum, "out_sum": out_sum,
            "in_tx": in_tx, "out_tx": out_tx,
            "fan_in": len(set(graph.predecessors(gid)) - {gid}),
            "fan_out": len(set(graph.successors(gid)) - {gid}),
            "pass_through": observed_ratio,
            "retention_proxy": _clip(1 - observed_ratio) if observed_ratio is not None else None,
            "pagerank": pagerank[gid], "betweenness": float(between[gid]), "hub_score": hubs[gid], "authority_score": authorities[gid],
            "reachable_seed_count": len(downstream_seeds[gid]), "incoming_seed_count": len(incoming_seeds[gid]),
            "downstream_seed_count": len(downstream_seeds[gid]), "seed_distance": distances.get(gid), "linked_seed_count": len(links),
            "component_id": components[gid], "intercluster_bridge": bridge, "counterparty_concentration": concentration,
            "outgoing_concentration": outgoing_concentration, "in_cycle": gid in cycle_nodes,
            "boundary_censored": boundary, "repeated_route_share": repeated / (in_tx + out_tx) if in_tx + out_tx else 0.0,
            "return_flow_share": reciprocal / out_sum if out_sum else 0.0, "observation_quality": quality,
            **temporal[gid],
        }
    return result


def normalized_metrics(metrics: dict[int, dict]) -> dict[str, dict[int, float]]:
    keys = ["in_sum", "out_sum", "pagerank", "betweenness", "hub_score", "authority_score", "linked_seed_count"]
    normalized = {key: percentile({gid: m[key] for gid, m in metrics.items()}) for key in keys}
    normalized["turnover"] = percentile({gid: m["in_sum"] + m["out_sum"] for gid, m in metrics.items()})
    normalized["branching"] = percentile({gid: m["fan_in"] + m["fan_out"] for gid, m in metrics.items()})
    normalized["centrality"] = {
        gid: (normalized["pagerank"][gid] + normalized["betweenness"][gid] + max(normalized["hub_score"][gid], normalized["authority_score"][gid])) / 3
        if m["in_degree"] + m["out_degree"] else 0.0 for gid, m in metrics.items()
    }
    return normalized


def score_roles(gid: int, m: dict, is_seed: bool, norms: dict, config: dict) -> tuple[str, float, dict[str, float]]:
    thresholds = config["role_thresholds"]
    weights = config["role_weights"]
    scores = {role: 0.0 for role in ROLE_LABELS}
    fan_in = min(m["fan_in"] / thresholds["fan_saturation"], 1.0)
    fan_out = min(m["fan_out"] / thresholds["fan_saturation"], 1.0)
    retention = m["retention_proxy"] if m["retention_proxy"] is not None else 0.0
    aggregation = m["fan_in"] / max(1, m["fan_in"] + m["fan_out"])
    features = {
        "consolidator": {"fan_in": fan_in, "in_volume": norms["in_sum"][gid], "aggregation": aggregation, "retention": retention},
        "transit": {"balance": min(m["in_sum"], m["out_sum"]) / max(m["in_sum"], m["out_sum"], 1), "rapid": m["rapid_transit_share"], "low_retention": 1-retention, "two_sided": float(m["in_degree"] > 0 and m["out_degree"] > 0)},
        "distributor": {"fan_out": fan_out, "out_volume": norms["out_sum"][gid], "dispersion": 1-m["outgoing_concentration"], "hub": norms["hub_score"][gid]},
        "terminal": {"retention": retention, "in_volume": norms["in_sum"][gid], "narrow_in": 1-fan_in},
        "coordinator": {"centrality": norms["centrality"][gid], "bridge": m["intercluster_bridge"], "seed_links": norms["linked_seed_count"][gid], "branching": min(fan_in, fan_out)},
    }
    eligible = {
        "consolidator": m["fan_in"] >= thresholds["consolidator_min_in"],
        "transit": not is_seed and m["in_degree"] > 0 and m["out_degree"] > 0 and m["rapid_transit_share"] >= thresholds["transit_min_rapid_share"],
        "distributor": m["fan_out"] >= thresholds["distributor_min_out"],
        "terminal": not is_seed and not m["boundary_censored"] and m["in_sum"] > 0 and m["pass_through"] is not None and m["pass_through"] <= thresholds["terminal_max_pass_through"],
        "coordinator": m["fan_in"] > 0 and m["fan_out"] > 0 and m["fan_in"] + m["fan_out"] >= thresholds["coordinator_min_branches"] and m["linked_seed_count"] >= thresholds["coordinator_min_seed_groups"] and norms["centrality"][gid] >= thresholds["coordinator_min_centrality"] and m["intercluster_bridge"] > 0,
    }
    for role, values in features.items():
        if eligible[role]:
            scores[role] = _clip(sum(weights[role][key] * value for key, value in values.items()))
    strongest = max(scores.values())
    scores["peripheral"] = max(thresholds["peripheral_baseline"], 1 - strongest)
    role = max(config["role_tie_order"], key=lambda candidate: scores[candidate])
    ordered = sorted(scores.values(), reverse=True)
    cfg = config["role_confidence"]
    confidence = (cfg["base_strength"] * ordered[0] + cfg["margin_weight"] * (ordered[0] - ordered[1])) * m["observation_quality"]
    return role, _clip(confidence), scores


def priority(gid: int, role: str, role_strength: float, m: dict, norms: dict, config: dict) -> tuple[float, list[dict]]:
    values = {
        "role": config["priority_role_values"][role] * role_strength,
        "seed_links": norms["linked_seed_count"][gid], "centrality": norms["centrality"][gid],
        "turnover": norms["turnover"][gid], "branching": norms["branching"][gid],
        "bridge": m["intercluster_bridge"], "cycles": max(float(m["in_cycle"]), m["repeated_route_share"]),
        "temporal": m["rapid_transit_share"], "quality": m["observation_quality"],
    }
    labels = {"role": "Выраженность роли", "seed_links": "Связи с seed", "centrality": "Структурная центральность", "turnover": "Наблюдаемый оборот", "branching": "Число веток", "bridge": "Межкластерные связи", "cycles": "Циклы и повторные маршруты", "temporal": "Быстрый транзит", "quality": "Полнота наблюдения"}
    factors = []
    for key, weight in config["priority_weights"].items():
        value = _clip(values[key])
        factors.append({"key": key, "label": labels[key], "value": value, "weight": weight,
                        "contribution": value * weight * m["observation_quality"]})
    return _clip(sum(factor["contribution"] for factor in factors)), factors


def explanations(role: str, m: dict, is_seed: bool, config: dict) -> tuple[str, list[str], list[str], str]:
    incoming, outgoing = m["in_degree"], m["out_degree"]
    rapid = round(100 * m["rapid_transit_share"])
    if role == "consolidator":
        evidence = f"Признаки консолидации: {incoming} отправителей, {m['in_sum']:,.0f} KZT входящих, {outgoing} получателей."
    elif role == "transit":
        evidence = f"Признаки транзита: {incoming} отправителей → {outgoing} получателей; {rapid}% входящих сопоставлены с более поздними исходящими за ≤{config['rapid_window_days']} суток."
    elif role == "distributor":
        evidence = f"Признаки распределения: {outgoing} получателей, {m['out_sum']:,.0f} KZT исходящих; {m['out_tx']} переводов."
    elif role == "terminal":
        evidence = f"Наблюдаемый получатель: вход {m['in_sum']:,.0f} KZT от {incoming} отправителей; исходящий поток {m['out_sum']:,.0f} KZT. Полный баланс неизвестен."
    elif role == "coordinator":
        evidence = f"Кандидат в координаторы: {incoming + outgoing} связей, {m['linked_seed_count']} связанных seed; {m['intercluster_bridge']:.0%} связей между кластерами."
    else:
        evidence = f"Недостаточно признаков: {incoming} отправителей, {outgoing} получателей; {m['in_tx'] + m['out_tx']} наблюдаемых участий в переводах."
    if m["boundary_censored"]:
        evidence += f" Depth={config['max_depth']}: обрыв наблюдения."
    elif is_seed:
        evidence += " Seed: вход неполон."
    evidence = evidence.replace(",", " ")
    if len(evidence) > 200:
        evidence = evidence[:197].rstrip() + "…"
    detailed = [
        evidence,
        f"Входящие: {m['in_sum']:,.2f} KZT, {m['in_tx']} операций от {incoming} отправителей. Исходящие: {m['out_sum']:,.2f} KZT, {m['out_tx']} операций к {outgoing} получателям.",
        f"FIFO-сопоставление без повторного учёта суммы: {m['rapid_transit_amount']:,.2f} KZT ({rapid}% наблюдаемых поступлений) совместимы с более поздним списанием в течение {config['rapid_window_days']} суток. Активных дней: {m['active_days']}.",
        f"Связанных seed по направленным путям: {m['linked_seed_count']}; seed достигают узла: {m['incoming_seed_count']}; seed ниже по потоку: {m['downstream_seed_count']}.",
        f"PageRank {m['pagerank']:.6f}; посредничество {m['betweenness']:.6f}; доля связей между кластерами {m['intercluster_bridge']:.1%}; концентрация контрагентов HHI {m['counterparty_concentration']:.3f}.",
        "Роль выбрана по максимальному прозрачному score; разница двух лучших score и полнота наблюдения уменьшают уверенность. Это гипотеза для проверки.",
    ]
    limitations = [f"Видны только внутрибанковские переводы ≥{config['min_transaction_kzt']:,.0f} KZT в выбранном окне; полный баланс неизвестен.".replace(",", " ")]
    if m["boundary_censored"]:
        limitations.append(f"Граница depth={config['max_depth']}: дальнейшие переводы могут отсутствовать из-за глубины выгрузки; terminal отключён.")
    if is_seed:
        limitations.append("У seed входящие неполны: pass-through, удержание и балансовые роли transit/terminal отключены.")
    if m["out_sum"] > m["in_sum"]:
        limitations.append("Исходящая сумма больше входящей; это допустимо при неизвестном начальном остатке и внешних поступлениях.")
    if not incoming and not outgoing:
        limitations.append("Узел отсутствует в рёбрах; данных недостаточно для структурной роли.")
    if m["fan_in"] < incoming or m["fan_out"] < outgoing:
        limitations.append("Перевод на тот же gid сохранён в суммах и степенях, но не считается независимым источником, получателем или временным транзитом.")
    limitations.append("Временное сопоставление не доказывает происхождение средств; порядок операций одного дня неизвестен.")
    next_step = "Запросить полный входящий и исходящий оборот, остатки, время и назначение операций; проверить контрагентов и источник средств."
    if m["boundary_censored"]:
        next_step = f"Расширить выгрузку за колено {config['max_depth']} и за конец периода, затем проверить дальнейших получателей."
    elif is_seed:
        next_step = "Запросить все входящие переводы и начальный остаток seed, включая внешние источники, до балансовой интерпретации."
    elif role == "transit":
        next_step = "Запросить точное время и назначение сопоставленных входящих и исходящих операций; проверить совпадение источника и цели средств."
    return evidence, detailed, limitations, next_step


def summarize_clusters(nodes: list[dict], edges: list[dict]) -> list[dict]:
    grouped: dict[int, list] = defaultdict(list)
    mapping = {node["gid"]: node["cluster_id"] for node in nodes}
    totals = defaultdict(lambda: {"sum_kzt_internal": 0.0, "inflow_external": 0.0, "outflow_external": 0.0})
    for node in nodes:
        grouped[node["cluster_id"]].append(node)
    for edge in edges:
        src, dst, amount = mapping[edge["src"]], mapping[edge["dst"]], edge["sum_kzt"]
        if src == dst:
            totals[src]["sum_kzt_internal"] += amount
        else:
            totals[src]["outflow_external"] += amount
            totals[dst]["inflow_external"] += amount
    descriptions = {"consolidator": "Признаки контура консолидации", "transit": "Признаки транзитной цепочки", "distributor": "Признаки распределительного контура", "coordinator": "Связующий контур: кандидат на межгрупповое взаимодействие", "terminal": "Контур наблюдаемых получателей", "peripheral": "Периферийная ветка; недостаточно данных для устойчивой гипотезы"}
    result = []
    for cluster_id, members in sorted(grouped.items()):
        roles = Counter(node["role"] for node in members)
        ranked = sorted(members, key=lambda node: (-node["priority_score"], node["gid"]))
        key_nodes = [node for node in ranked if node["role"] != "peripheral"]
        hypothesis_role = key_nodes[0]["role"] if key_nodes else "peripheral"
        result.append({"cluster_id": cluster_id, "n_nodes": len(members), "n_seed": sum(node["is_seed"] for node in members),
                       **totals[cluster_id], "top_gids": [node["gid"] for node in ranked[:5]], "roles": dict(sorted(roles.items())),
                       "hypothesis": descriptions[hypothesis_role] + "; требует проверки."})
    return result


def methodology(config: dict) -> dict:
    descriptions = role_descriptions(config)
    clustering = config["clustering"]
    centrality = config["centrality"]
    confidence = config["role_confidence"]
    return {
        "limitations": data_limitations(config),
        "roles": [{"role": role, "label": label, "description": descriptions[role]} for role, label in ROLE_LABELS.items()],
        "priority_weights": config["priority_weights"],
        "priority_formula": "priority = качество наблюдения × Σ(вес × нормализованный фактор). Вклады факторов уже включают поправку качества и суммируются в итоговый score.",
        "role_formula": f"Основная роль = argmax(role_scores). Уверенность = ({confidence['base_strength']:.2f} × лучший score + {confidence['margin_weight']:.2f} × отрыв от второго) × качество. Это не вероятность.",
        "role_tie_order": config["role_tie_order"], "configuration": config,
        "clustering": f"Слабосвязные компоненты отдельно; при ≥{clustering['min_louvain_size']} узлах Louvain на неориентированной проекции, вес log1p(sum_kzt)+{clustering['transaction_weight']}×log1p(n_tx), встречные рёбра суммируются, resolution={clustering['resolution']}, seed={config['random_seed']}. ID стабилизированы по минимальному gid.",
        "normalization": f"Средний ранг положительных значений / число положительных значений; нулевые значения остаются нулём. Fan-in/out насыщается на {config['role_thresholds']['fan_saturation']}. Центральность — среднее с равными весами percentiles PageRank, betweenness и max(HITS hub,authority).",
        "centralities": f"PageRank по сумме, alpha={centrality['pagerank_alpha']}; HITS по log1p суммы через детерминированную степенную итерацию (≤{centrality['iterations']} итераций, tolerance={centrality['tolerance']}); значения HITS <{centrality['hits_epsilon']} обнуляются. Betweenness по числу направленных переходов, точный до {centrality['betweenness_sample']} узлов, иначе {centrality['betweenness_sample']} исходных вершин с seed={config['random_seed']}; сумма не интерпретируется как расстояние.",
        "temporal_matching": f"FIFO: списание погашает доступные более ранние поступления; каждая входящая и исходящая сумма используется один раз. Даже старые поступления потребляются, но в rapid учитываются лишь более поздние списания ≤{config['rapid_window_days']} суток. Одновременные операции не сопоставляются.",
        "cluster_hypothesis": "Гипотеза определяется ролью самого приоритетного непериферийного узла кластера; при отсутствии такого узла — периферийная ветка.",
        "glossary": {
            "seed": "Исходный клиент ранее выявленной сети; это контекст выборки, не новая оценка виновности.",
            "boundary-censored": f"Узел на колене {config['max_depth']} с ограниченной видимостью дальнейшего движения.",
            "pass_through": "Наблюдаемые исходящие / входящие; может быть >1, для seed и нулевого входа не определён.",
            "retention_proxy": "max(0,1−pass_through), ограничено 0–1; это не остаток на счёте.",
            "reachable_seed_count": "Число других seed, достижимых от узла по направлению денег.",
            "linked_seed_count": "Число различных других seed выше или ниже узла по направленным путям; каждый учитывается один раз.",
            "seed_distance": "Минимальное направленное расстояние от любого seed; null означает недостижимость.",
            "HHI": "Сумма квадратов долей контрагентов в наблюдаемом входящем+исходящем обороте; ближе к 1 — выше концентрация.",
            "priority_score": "Порядок проверки относительно текущей выборки, а не вероятность преступления.",
            "in_cycle": "Принадлежность направленному циклу по сильносвязной компоненте либо петле.",
            "fan_in / fan_out": "Число различных внешних отправителей / получателей; сам узел исключён. Обычные in_degree/out_degree сохраняют петлю как ребро.",
            "repeated_route_share": "Доля наблюдаемых участий в переводах по направленным парам с более чем одной операцией.",
        },
    }


def validate_outputs(result: AnalysisResult, tables: dict[str, pd.DataFrame]) -> None:
    schemas = {"nodes_roles": ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"],
               "clusters": ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"],
               "top_nodes": ["rank", "gid", "role", "priority_score", "why"]}
    for name, columns in schemas.items():
        frame = tables[name]
        if not set(columns).issubset(frame.columns) or frame[columns].isna().any().any():
            raise DataValidationError(f"Ошибка схемы выгрузки {name}.")
        for column in columns:
            if frame[column].astype(str).str.strip().eq("").any():
                raise DataValidationError(f"Пустое обязательное значение {name}.{column}.")
        for score in ("role_score", "priority_score"):
            if score in frame and not frame[score].between(0, 1).all():
                raise DataValidationError(f"{name}.{score} вне диапазона 0–1.")
    if len(tables["nodes_roles"]) != result.stats["n_nodes"] or tables["nodes_roles"]["gid"].duplicated().any():
        raise DataValidationError("Выгрузка потеряла или продублировала узлы.")
    if len(tables["top_nodes"]) < min(20, len(result.nodes)):
        raise DataValidationError("Выгрузка top_nodes содержит менее 20 доступных узлов.")
    if tables["nodes_roles"]["evidence"].str.len().gt(200).any():
        raise DataValidationError("Evidence длиннее 200 символов.")


def write_outputs(result: AnalysisResult, output_dir: Path, config: dict, data_dir: Path) -> None:
    required = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence", "depth", "is_seed"]
    roles = pd.DataFrame([{**{key: node[key] for key in required}, "source": result.source,
                           "in_degree": node["metrics"]["in_degree"], "out_degree": node["metrics"]["out_degree"],
                           "in_sum": node["metrics"]["in_sum"], "out_sum": node["metrics"]["out_sum"],
                           "boundary_censored": node["metrics"]["boundary_censored"]} for node in result.nodes])
    clusters = pd.DataFrame([{**cluster, "top_gids": "|".join(map(str, cluster["top_gids"])), "roles": json.dumps(cluster["roles"], ensure_ascii=False, sort_keys=True), "source": result.source} for cluster in result.clusters])
    ranked = sorted(result.nodes, key=lambda node: (-node["priority_score"], node["gid"]))[:config["exports"]["top_count"]]
    top = pd.DataFrame([{"rank": i, "gid": node["gid"], "role": node["role"], "priority_score": node["priority_score"], "why": node["evidence"], "source": result.source} for i, node in enumerate(ranked, 1)])
    tables = {"nodes_roles": roles, "clusters": clusters, "top_nodes": top}
    validate_outputs(result, tables)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        target = output_dir / f"{name}.csv"
        temporary = output_dir / f"{name}.csv.tmp"
        frame.to_csv(temporary, index=False, encoding="utf-8-sig", float_format=f"%.{config['exports']['float_decimals']}f", lineterminator="\n")
        temporary.replace(target)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(data_dir.glob("*.parquet")) if path.name in {"nodes.parquet", "edges.parquet", "transactions.parquet"}}
    metadata = {"source": result.source, "duration_seconds": result.duration_seconds, "stats": result.stats,
                "warnings": result.warnings, "methodology": result.methodology, "input_sha256": hashes,
                "config_sha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
                "versions": {"networkx": nx.__version__, "pandas": pd.__version__, "numpy": np.__version__}}
    temp = output_dir / "metadata.json.tmp"
    temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(output_dir / "metadata.json")


def run_pipeline(data_dir: Path = ROOT / "data", output_dir: Path = ROOT / "outputs") -> AnalysisResult:
    started = time.perf_counter()
    config = load_config()
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    LOGGER.info("Загрузка и валидация %s", data_dir)
    nodes, edges, tx, warnings, source = load_data(data_dir, config)
    graph = build_graph(nodes, edges)
    LOGGER.info("Граф: %s узлов, %s рёбер; кластеризация", len(graph), graph.number_of_edges())
    clusters, components = cluster_graph(graph, config)
    LOGGER.info("Графовые и временные признаки")
    metrics = compute_metrics(graph, nodes, tx, clusters, components, config)
    normalized = normalized_metrics(metrics)
    LOGGER.info("Роли, приоритет, объяснения")
    profiles = []
    for row in nodes.itertuples(index=False):
        gid, is_seed = int(row.gid), bool(row.is_seed)
        m = metrics[gid]
        role, confidence, scores = score_roles(gid, m, is_seed, normalized, config)
        score, factors = priority(gid, role, scores[role], m, normalized, config)
        evidence, detailed, limitations, next_step = explanations(role, m, is_seed, config)
        profiles.append({"gid": gid, "depth": int(row.depth), "is_seed": is_seed, "role": role,
                         "role_score": confidence, "role_scores": scores, "cluster_id": clusters[gid],
                         "priority_score": score, "evidence": evidence, "detailed_evidence": detailed,
                         "metrics": m, "priority_factors": factors, "limitations": limitations, "next_step": next_step})
    edge_list = [{"src": int(row.src), "dst": int(row.dst), "sum_kzt": float(row.sum_kzt), "n_tx": int(row.n_tx), "depth": int(row.depth)} for row in edges.itertuples(index=False)]
    summaries = summarize_clusters(profiles, edge_list)
    isolated = sum(m["in_degree"] + m["out_degree"] == 0 for m in metrics.values())
    boundary = sum(m["boundary_censored"] for m in metrics.values())
    warnings.extend([f"{isolated} изолированных узлов сохранены; недостаточно наблюдений для роли.",
                     f"{boundary} узлов на depth={config['max_depth']}: граничная цензура; роль terminal отключена.",
                     f"{int(nodes['is_seed'].sum())} seed: балансовые отношения не используются."])
    stats = {"n_nodes": len(nodes), "n_edges": len(edges), "n_transactions": len(tx), "total_volume": float(tx["sum_kzt"].sum()),
             "n_seed": int(nodes["is_seed"].sum()), "n_components": len(set(components.values())), "n_clusters": len(summaries),
             "n_boundary": boundary, "n_isolated": isolated,
             "period_start": tx["date"].min().date().isoformat() if not tx.empty else None,
             "period_end": tx["date"].max().date().isoformat() if not tx.empty else None,
             "roles": dict(Counter(node["role"] for node in profiles))}
    timeline = []
    if not tx.empty:
        daily = tx.assign(day=tx["date"].dt.date).groupby("day", as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
        timeline = [{"date": row.day.isoformat(), "sum_kzt": float(row.sum_kzt), "n_tx": int(row.n_tx)} for row in daily.itertuples(index=False)]
    result = AnalysisResult(profiles, edge_list, summaries, stats, warnings, methodology(config), time.perf_counter() - started, source, graph, timeline)
    # JSON serialization here catches any accidental NaN/Infinity before API cache or exports are replaced.
    json.dumps({"nodes": profiles, "edges": edge_list, "clusters": summaries, "stats": stats}, allow_nan=False)
    write_outputs(result, output_dir, config, data_dir)
    result.duration_seconds = time.perf_counter() - started
    metadata_path = output_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["duration_seconds"] = result.duration_seconds
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    LOGGER.info("Готово: %s, %.3f с; CSV: %s", source, result.duration_seconds, output_dir)
    return result
