"""Bounded projections from the current successful analysis snapshot."""

from collections import defaultdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..agent.copilot import respond
from .models import (
    Cluster, ClusterGraph, CopilotRequest, CopilotResponse, Direction,
    GraphResponse, Health, Methodology, Node, NodePage, Overview, RecalculateStatus, Role,
)
from .store import AnalysisStore, EXPORT_NAMES, Snapshot

router = APIRouter(prefix="/api")


def get_store(request: Request) -> AnalysisStore:
    return request.app.state.store


def get_snapshot(store: Annotated[AnalysisStore, Depends(get_store)]) -> Snapshot:
    if store.snapshot is None:
        raise HTTPException(503, detail=store.error or "Первоначальный расчёт ещё не готов")
    return store.snapshot


Data = Annotated[Snapshot, Depends(get_snapshot)]
Store = Annotated[AnalysisStore, Depends(get_store)]


def lookup_node(snapshot: Snapshot, gid: int) -> dict:
    if gid not in snapshot.by_gid:
        raise HTTPException(404, detail=f"Узел gid {gid} не найден")
    return snapshot.by_gid[gid]


def select_nodes(snapshot: Snapshot, q: str | None = None, role: str | None = None,
                 cluster_id: int | None = None, depth: int | None = None,
                 is_seed: bool | None = None) -> list[dict]:
    nodes = snapshot.nodes
    if q and q.strip():
        search = q.lower().replace("gid", "").strip()
        nodes = [n for n in nodes if search in str(n["gid"])]
    if role:
        nodes = [n for n in nodes if n["role"] == role]
    if cluster_id is not None:
        nodes = [n for n in nodes if n["cluster_id"] == cluster_id]
    if depth is not None:
        nodes = [n for n in nodes if n["depth"] == depth]
    if is_seed is not None:
        nodes = [n for n in nodes if n["is_seed"] == is_seed]
    return nodes


@router.get("/health", response_model=Health, tags=["system"])
def health(store: Store, response: Response):
    if store.snapshot is None:
        response.status_code = 503
    return Health(status="ok" if store.snapshot else "error" if store.error else "starting",
                  ready=store.snapshot is not None,
                  source=store.snapshot.result.source if store.snapshot else None,
                  error=store.error)


@router.get("/overview", response_model=Overview, tags=["analysis"])
def overview(snapshot: Data):
    return snapshot.overview


@router.post("/recalculate", response_model=RecalculateStatus, status_code=202, tags=["system"])
def recalculate(store: Store):
    return store.recalculate()


@router.get("/recalculate/status", response_model=RecalculateStatus, tags=["system"])
def recalculate_status(store: Store):
    return store.status()


@router.get("/top-nodes", response_model=list[Node], tags=["nodes"])
def top_nodes(snapshot: Data, limit: int = Query(20, ge=1, le=200)):
    return snapshot.nodes[:limit]


@router.get("/nodes", response_model=NodePage, tags=["nodes"])
def nodes(snapshot: Data, q: str | None = Query(None, max_length=100), role: Role | None = None,
          cluster_id: int | None = None, depth: int | None = Query(None, ge=0, le=4),
          is_seed: bool | None = None, page: int = Query(1, ge=1),
          page_size: int = Query(25, ge=1, le=100),
          sort_by: Literal["priority_score", "gid", "role_score", "cluster_id", "depth", "in_sum", "out_sum", "in_degree", "out_degree", "linked_seed_count", "pagerank", "betweenness"] = "priority_score",
          sort_dir: Literal["asc", "desc"] = "desc"):
    selected = select_nodes(snapshot, q, role, cluster_id, depth, is_seed)
    # Initial gid sort makes ties deterministic in both sort directions.
    selected = sorted(selected, key=lambda n: n["gid"])
    selected.sort(key=lambda n: n.get(sort_by, n["metrics"].get(sort_by, 0)), reverse=sort_dir == "desc")
    start = (page - 1) * page_size
    return {"items": selected[start:start + page_size], "total": len(selected), "page": page, "page_size": page_size}


@router.get("/nodes/{gid}", response_model=Node, tags=["nodes"])
def node_profile(gid: int, snapshot: Data):
    return lookup_node(snapshot, gid)


@router.get("/graph", response_model=GraphResponse, tags=["graph"])
def graph(snapshot: Data, mode: Literal["top", "all", "seed"] = "top",
          limit: int = Query(60, ge=1, le=500), role: Role | None = None,
          cluster_id: int | None = None, depth: int | None = Query(None, ge=0, le=4),
          is_seed: bool | None = None, min_amount: float | None = Query(None, ge=0, allow_inf_nan=False),
          max_amount: float | None = Query(None, ge=0, allow_inf_nan=False)):
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(422, detail="Минимальная сумма не может превышать максимальную")
    candidates = select_nodes(snapshot, role=role, cluster_id=cluster_id, depth=depth, is_seed=is_seed)
    if mode == "seed":
        candidates = [n for n in candidates if n["is_seed"]]
    edges = snapshot.edges
    if min_amount is not None or max_amount is not None:
        edges = [e for e in edges if (min_amount is None or e["sum_kzt"] >= min_amount)
                 and (max_amount is None or e["sum_kzt"] <= max_amount)]
        touched = {e[key] for e in edges for key in ("src", "dst")}
        candidates = [n for n in candidates if n["gid"] in touched]
    selected = candidates[:limit]
    gids = {n["gid"] for n in selected}
    return {"nodes": selected, "edges": [e for e in edges if e["src"] in gids and e["dst"] in gids],
            "truncated": len(candidates) > limit}


@router.get("/nodes/{gid}/ego", response_model=GraphResponse, tags=["graph"])
def ego(gid: int, snapshot: Data, depth: int = Query(1, ge=1, le=4), direction: Direction = "both"):
    lookup_node(snapshot, gid)
    neighbors: dict[int, set[int]] = defaultdict(set)
    for edge in snapshot.edges:
        if direction in ("out", "both"):
            neighbors[edge["src"]].add(edge["dst"])
        if direction in ("in", "both"):
            neighbors[edge["dst"]].add(edge["src"])
    visited, frontier = {gid}, {gid}
    truncated = False
    for _ in range(depth):
        next_nodes = set().union(*(neighbors[n] for n in frontier)) - visited if frontier else set()
        ranked = sorted(next_nodes, key=lambda n: (-snapshot.by_gid[n]["priority_score"], n))
        remaining = 300 - len(visited)
        if len(ranked) > remaining:
            ranked = ranked[:remaining]
            truncated = True
        frontier = set(ranked)
        visited.update(frontier)
        if not frontier or truncated:
            break
    selected = [snapshot.by_gid[gid]] + [n for n in snapshot.nodes if n["gid"] in visited and n["gid"] != gid]
    return {"nodes": selected, "edges": [e for e in snapshot.edges if e["src"] in visited and e["dst"] in visited], "truncated": truncated}


@router.get("/clusters", response_model=list[Cluster], tags=["clusters"])
def clusters(snapshot: Data):
    return snapshot.clusters


@router.get("/clusters/graph", response_model=ClusterGraph, tags=["clusters"])
def cluster_graph(snapshot: Data):
    aggregated: dict[tuple[int, int], dict] = {}
    for edge in snapshot.edges:
        src = snapshot.by_gid[edge["src"]]["cluster_id"]
        dst = snapshot.by_gid[edge["dst"]]["cluster_id"]
        if src == dst:
            continue
        total = aggregated.setdefault((src, dst), {"src": src, "dst": dst, "sum_kzt": 0.0, "n_tx": 0})
        total["sum_kzt"] += edge["sum_kzt"]
        total["n_tx"] += edge["n_tx"]
    return {"nodes": snapshot.clusters, "edges": [aggregated[key] for key in sorted(aggregated)]}


@router.get("/clusters/{cluster_id}", response_model=Cluster, tags=["clusters"])
def cluster_profile(cluster_id: int, snapshot: Data):
    cluster = snapshot.by_cluster.get(cluster_id)
    if cluster is None:
        raise HTTPException(404, detail=f"Кластер {cluster_id} не найден")
    return cluster


@router.get("/methodology", response_model=Methodology, tags=["analysis"])
def methodology(snapshot: Data):
    return snapshot.result.methodology


@router.post("/copilot", response_model=CopilotResponse, tags=["copilot"])
def copilot(payload: CopilotRequest, snapshot: Data):
    if not payload.message.strip():
        raise HTTPException(422, detail="Сообщение не может состоять только из пробелов")
    return respond(snapshot, payload)


@router.get("/exports/{filename}", tags=["exports"], response_class=Response)
def export(filename: str, snapshot: Data):
    if filename not in EXPORT_NAMES:
        raise HTTPException(404, detail="Выгрузка не найдена")
    return Response(snapshot.exports[filename], media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Data-Source": snapshot.result.source})
