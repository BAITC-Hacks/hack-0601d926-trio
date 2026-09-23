"""Public response contracts. No raw data frames or graph objects cross the API."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

Role = Literal["consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"]
Direction = Literal["in", "out", "both"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Metrics(Contract):
    in_degree: int
    out_degree: int
    in_sum: float
    out_sum: float
    in_tx: int
    out_tx: int
    avg_amount: float
    median_amount: float
    fan_in: int
    fan_out: int
    pass_through: float | None
    retention_proxy: float | None
    pagerank: float
    betweenness: float
    hub_score: float
    authority_score: float
    reachable_seed_count: int
    seed_distance: int | None
    linked_seed_count: int
    component_id: int
    intercluster_bridge: float
    rapid_transit_share: float | None
    active_days: int
    counterparty_concentration: float
    in_cycle: bool
    boundary_censored: bool
    rapid_transit_amount: float = 0
    repeated_route_share: float = 0
    return_flow_share: float = 0
    observation_quality: float = 0
    incoming_seed_count: int = 0
    downstream_seed_count: int = 0
    temporal_matched_share: float | None = None
    outgoing_concentration: float = 0


class PriorityFactor(Contract):
    key: str
    label: str
    value: float
    weight: float
    contribution: float


class Node(Contract):
    gid: int
    depth: int
    is_seed: bool
    role: Role
    role_score: float = Field(ge=0, le=1)
    role_scores: dict[str, float]
    cluster_id: int
    priority_score: float = Field(ge=0, le=1)
    evidence: str
    detailed_evidence: list[str]
    metrics: Metrics
    priority_factors: list[PriorityFactor]
    limitations: list[str]
    next_step: str

    @field_serializer("gid", when_used="json")
    def serialize_gid(self, value: int) -> str:
        # Real client identifiers exceed JavaScript's 53-bit integer precision.
        return str(value)


class Edge(Contract):
    src: int
    dst: int
    sum_kzt: float = Field(ge=0)
    n_tx: int = Field(ge=0)
    depth: int

    @field_serializer("src", "dst", when_used="json")
    def serialize_gid(self, value: int) -> str:
        return str(value)


class Cluster(Contract):
    cluster_id: int
    n_nodes: int
    n_seed: int
    sum_kzt_internal: float
    inflow_external: float
    outflow_external: float
    top_gids: list[int]
    roles: dict[str, int]
    hypothesis: str

    @field_serializer("top_gids", when_used="json")
    def serialize_gids(self, value: list[int]) -> list[str]:
        return [str(gid) for gid in value]


class MethodologyRole(Contract):
    role: Role
    label: str
    description: str


class Methodology(Contract):
    limitations: list[str]
    roles: list[MethodologyRole]
    priority_weights: dict[str, float]
    priority_formula: str
    role_formula: str
    role_tie_order: list[Role]
    configuration: dict[str, Any]
    clustering: str
    normalization: str
    centralities: str
    temporal_matching: str
    cluster_hypothesis: str
    glossary: dict[str, str]


class GraphResponse(Contract):
    nodes: list[Node]
    edges: list[Edge]
    truncated: bool


class ClusterEdge(Contract):
    src: int
    dst: int
    sum_kzt: float
    n_tx: int


class ClusterGraph(Contract):
    nodes: list[Cluster]
    edges: list[ClusterEdge]


class NodePage(Contract):
    items: list[Node]
    total: int
    page: int
    page_size: int


class TimelinePoint(Contract):
    date: str
    sum_kzt: float
    n_tx: int


class Overview(Contract):
    n_nodes: int
    n_edges: int
    n_transactions: int
    total_volume: float
    n_seed: int
    n_components: int
    n_clusters: int
    n_boundary: int
    n_isolated: int
    period_start: str | None
    period_end: str | None
    roles: dict[str, int]
    source: Literal["real", "synthetic"]
    warnings: list[str]
    duration_seconds: float
    top_nodes: list[Node]
    timeline: list[TimelinePoint] = Field(default_factory=list)


class RecalculateStatus(Contract):
    status: Literal["idle", "running", "completed", "failed"]
    message: str
    duration_seconds: float | None = None


class Health(Contract):
    status: Literal["ok", "error", "starting"]
    ready: bool
    source: Literal["real", "synthetic"] | None = None
    error: str | None = None


class CopilotRequest(Contract):
    message: str = Field(min_length=1, max_length=2000)
    context_gid: int | None = None

    @field_validator("context_gid", mode="before", json_schema_input_type=int | str | None)
    @classmethod
    def validate_context_gid(cls, value):
        if isinstance(value, str):
            if not value.isdecimal():
                raise ValueError("gid должен быть целым десятичным идентификатором")
            return int(value)
        return value


class ToolUse(Contract):
    name: str
    parameters: dict[str, Any]
    summary: str

    @field_serializer("parameters", when_used="json")
    def serialize_parameters(self, value: dict[str, Any]) -> dict[str, Any]:
        def convert(item, key=""):
            if key == "gid" and item is not None:
                return str(item)
            if key == "gids" and isinstance(item, list):
                return [str(gid) for gid in item]
            if isinstance(item, dict):
                return {k: convert(v, k) for k, v in item.items()}
            if isinstance(item, list):
                return [convert(v) for v in item]
            return item
        return convert(value)


class EntityLink(Contract):
    type: Literal["node", "cluster"]
    id: int
    label: str

    @field_serializer("id", when_used="json")
    def serialize_id(self, value: int) -> str | int:
        return str(value) if self.type == "node" else value


class CopilotResponse(Contract):
    answer: str
    observations: list[str]
    hypotheses: list[str]
    limitations: list[str]
    tools_used: list[ToolUse]
    links: list[EntityLink]
    mode: Literal["local"] = "local"
