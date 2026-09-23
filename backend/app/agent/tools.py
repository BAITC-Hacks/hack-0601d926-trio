"""Explicit allowlist of tools with bounded, Pydantic-validated parameters."""

from pydantic import BaseModel, ConfigDict, Field

from ..api.models import Direction, Role
from ..api.store import Snapshot


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NodeParameters(Parameters):
    gid: int


class NeighborsParameters(NodeParameters):
    direction: Direction = "both"
    limit: int = Field(default=20, ge=1, le=50)


class FlowParameters(NodeParameters):
    max_depth: int = Field(default=3, ge=1, le=4)
    limit_paths: int = Field(default=12, ge=1, le=30)


class GroupParameters(Parameters):
    gids: list[int] = Field(min_length=1, max_length=30)
    limit: int = Field(default=10, ge=1, le=30)


class ClusterParameters(Parameters):
    cluster_id: int


class TopParameters(Parameters):
    role: Role | None = None
    limit: int = Field(default=10, ge=1, le=30)
    bridge_only: bool = False
    cluster_ids: list[int] = Field(default_factory=list, max_length=2)


class RequestParameters(Parameters):
    gid: int | None = None


class ToolInputError(ValueError):
    """Known entity not found or unsupported tool requested."""


class LocalTools:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot

    def node(self, gid: int) -> dict:
        node = self.snapshot.by_gid.get(gid)
        if node is None:
            raise ToolInputError(f"Узел gid {gid} не найден в текущем наборе данных.")
        return node

    def compact(self, node: dict) -> dict:
        return {key: node[key] for key in ("gid", "role", "priority_score", "cluster_id", "evidence", "metrics")}

    def invoke(self, name: str, parameters: dict) -> dict:
        # Function names are never evaluated; no SQL, shell, Python or network.
        registry = {
            "get_node_profile": (NodeParameters, self.get_node_profile),
            "get_neighbors": (NeighborsParameters, self.get_neighbors),
            "trace_money_flow": (FlowParameters, self.trace_money_flow),
            "find_common_recipients": (GroupParameters, self.find_common_recipients),
            "find_shared_senders": (GroupParameters, self.find_shared_senders),
            "get_cluster_profile": (ClusterParameters, self.get_cluster_profile),
            "compare_nodes": (GroupParameters, self.compare_nodes),
            "get_top_nodes": (TopParameters, self.get_top_nodes),
            "explain_priority": (NodeParameters, self.explain_priority),
            "suggest_next_data_request": (RequestParameters, self.suggest_next_data_request),
        }
        if name not in registry:
            raise ToolInputError("Неизвестный инструмент. Доступны только инструменты анализа графа.")
        model, function = registry[name]
        return function(model.model_validate(parameters))

    def get_node_profile(self, p: NodeParameters) -> dict:
        return self.node(p.gid)

    def get_neighbors(self, p: NeighborsParameters) -> dict:
        self.node(p.gid)
        edges = [e for e in self.snapshot.edges if
                 (p.direction in ("in", "both") and e["dst"] == p.gid) or
                 (p.direction in ("out", "both") and e["src"] == p.gid)]
        edges.sort(key=lambda e: (-e["sum_kzt"], e["src"], e["dst"]))
        selected = edges[:p.limit]
        gids = sorted({e["src"] if e["dst"] == p.gid else e["dst"] for e in selected})
        return {"gid": p.gid, "direction": p.direction, "edges": selected,
                "neighbors": [self.compact(self.node(gid)) for gid in gids],
                "total_edges": len(edges), "truncated": len(edges) > p.limit}

    def trace_money_flow(self, p: FlowParameters) -> dict:
        self.node(p.gid)
        adjacency: dict[int, list[dict]] = {}
        for edge in self.snapshot.edges:
            adjacency.setdefault(edge["src"], []).append(edge)
        for edges in adjacency.values():
            edges.sort(key=lambda e: (-e["sum_kzt"], e["dst"]))
        paths: list[dict] = []
        # Bounded breadth first expansion prevents high-fan-out graphs from
        # producing exponential route enumeration.
        queue = [([p.gid], [], False)]
        expanded = 0
        pruned = False
        while queue and len(paths) < p.limit_paths and expanded < 300:
            gids, route, closed = queue.pop(0)
            expanded += 1
            if route:
                paths.append({"gids": gids, "edges": route,
                              "bottleneck_kzt": min(e["sum_kzt"] for e in route), "cycle": closed})
            if len(route) < p.max_depth and not closed:
                outgoing = adjacency.get(gids[-1], [])
                if len(outgoing) > p.limit_paths:
                    pruned = True
                for edge in outgoing[:p.limit_paths]:
                    if len(queue) < 300:
                        queue.append((gids + [edge["dst"]], route + [edge], edge["dst"] in gids))
                    else:
                        pruned = True
        return {"gid": p.gid, "paths": paths, "truncated": bool(queue) or pruned,
                "note": "Маршруты показывают наблюдаемые связи. Суммы на рёбрах не доказывают движение одних и тех же средств."}

    def _shared(self, p: GroupParameters, recipients: bool) -> dict:
        gids = sorted(set(p.gids))
        for gid in gids:
            self.node(gid)
        source_key, target_key = ("src", "dst") if recipients else ("dst", "src")
        grouped: dict[int, dict] = {}
        for edge in self.snapshot.edges:
            if edge[source_key] in gids:
                other = edge[target_key]
                record = grouped.setdefault(other, {"gid": other, "gids": set(), "sum_kzt": 0.0, "n_tx": 0})
                record["gids"].add(edge[source_key])
                record["sum_kzt"] += edge["sum_kzt"]
                record["n_tx"] += edge["n_tx"]
        minimum = 2 if len(gids) > 1 else 1
        records = [{**r, "gids": sorted(r["gids"]), "n_shared": len(r["gids"])}
                   for r in grouped.values() if len(r["gids"]) >= minimum]
        records.sort(key=lambda r: (-r["n_shared"], -r["sum_kzt"], r["gid"]))
        return {"gids": gids, "matches": records[:p.limit], "total": len(records),
                "criterion": f"Связь минимум с {minimum} выбранными узлами"}

    def find_common_recipients(self, p: GroupParameters) -> dict:
        return self._shared(p, recipients=True)

    def find_shared_senders(self, p: GroupParameters) -> dict:
        return self._shared(p, recipients=False)

    def get_cluster_profile(self, p: ClusterParameters) -> dict:
        cluster = self.snapshot.by_cluster.get(p.cluster_id)
        if cluster is None:
            raise ToolInputError(f"Кластер {p.cluster_id} не найден.")
        return cluster

    def compare_nodes(self, p: GroupParameters) -> dict:
        return {"nodes": [self.compact(self.node(gid)) for gid in dict.fromkeys(p.gids)]}

    def get_top_nodes(self, p: TopParameters) -> dict:
        nodes = self.snapshot.nodes
        if p.role:
            nodes = [n for n in nodes if n["role"] == p.role]
        if p.bridge_only:
            nodes = [n for n in nodes if n["metrics"]["intercluster_bridge"] > 0]
        if p.cluster_ids:
            unknown = [cid for cid in p.cluster_ids if cid not in self.snapshot.by_cluster]
            if unknown:
                raise ToolInputError(f"Кластер {unknown[0]} не найден.")
            clusters = set(p.cluster_ids)
            linked = set()
            for edge in self.snapshot.edges:
                a = self.node(edge["src"])["cluster_id"]
                b = self.node(edge["dst"])["cluster_id"]
                if a != b and (a in clusters and b in clusters if len(clusters) == 2 else a in clusters or b in clusters):
                    linked.update((edge["src"], edge["dst"]))
            nodes = [n for n in nodes if n["gid"] in linked]
        return {"nodes": [self.compact(n) for n in nodes[:p.limit]], "total": len(nodes)}

    def explain_priority(self, p: NodeParameters) -> dict:
        node = self.node(p.gid)
        return {key: node[key] for key in ("gid", "priority_score", "role", "role_score", "priority_factors", "evidence", "detailed_evidence", "limitations")}

    def suggest_next_data_request(self, p: RequestParameters) -> dict:
        requests = ["Полная выписка входящих и исходящих операций, включая суммы менее 5 000 KZT и межбанковские переводы.",
                    "Более длинный период наблюдения и точное время операций для проверки повторяемости и транзита.",
                    "Назначения платежей и подтверждающие документы для проверки экономического смысла связей."]
        if p.gid is not None:
            node = self.node(p.gid)
            requests.insert(0, node["next_step"])
            if node["metrics"]["boundary_censored"]:
                requests.insert(0, f"Расширение выборки после четвёртого колена для gid {p.gid}: отсутствие исходящих связей может быть обрывом наблюдения.")
            if node["is_seed"]:
                requests.insert(0, f"Полный входящий поток gid {p.gid}: входящие суммы seed в этой выборке неполны.")
        return {"gid": p.gid, "requests": requests}
