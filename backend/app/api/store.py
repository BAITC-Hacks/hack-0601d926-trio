"""One immutable-by-convention snapshot per successful calculation.

Readers retain their snapshot during a background recalculation. Failed jobs do
not replace the last good data or the CSV download bytes.
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from .models import Cluster, Node, Edge, Methodology, Overview, RecalculateStatus

logger = logging.getLogger(__name__)
EXPORT_NAMES = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv")


@dataclass(frozen=True)
class Snapshot:
    result: Any
    nodes: list[dict]
    edges: list[dict]
    clusters: list[dict]
    by_gid: dict[int, dict]
    by_cluster: dict[int, dict]
    exports: dict[str, bytes]
    overview: dict


class AnalysisStore:
    def __init__(self, data_dir: Path, output_dir: Path):
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.snapshot: Snapshot | None = None
        self.error: str | None = None
        self._status = RecalculateStatus(status="idle", message="Расчёт ещё не запускался")
        self._lock = threading.Lock()
        self.worker: threading.Thread | None = None

    def status(self) -> RecalculateStatus:
        with self._lock:
            return self._status.model_copy()

    def initialize(self) -> None:
        with self._lock:
            self._status = RecalculateStatus(status="running", message="Выполняется первоначальный расчёт")
        self._calculate()

    def recalculate(self) -> RecalculateStatus:
        with self._lock:
            if self._status.status == "running":
                return self._status.model_copy()
            self._status = RecalculateStatus(status="running", message="Пересчёт выполняется в фоне; предыдущий результат доступен")
            self.worker = threading.Thread(target=self._calculate, name="aml-pipeline", daemon=True)
            self.worker.start()
            return self._status.model_copy()

    def _calculate(self) -> None:
        started = time.perf_counter()
        try:
            from ..analytics import run_pipeline

            self.output_dir.mkdir(parents=True, exist_ok=True)
            # Stage inside the output filesystem: /app/outputs may be a Docker
            # bind mount, where renaming files from /app would fail with EXDEV.
            with tempfile.TemporaryDirectory(prefix=".aml-calculation-", dir=self.output_dir) as temporary:
                staging = Path(temporary)
                result = run_pipeline(self.data_dir, staging)
                nodes = [Node.model_validate(n).model_dump() for n in result.nodes]
                edges = [Edge.model_validate(e).model_dump() for e in result.edges]
                clusters = [Cluster.model_validate(c).model_dump() for c in result.clusters]
                Methodology.model_validate(result.methodology)
                nodes.sort(key=lambda n: (-n["priority_score"], n["gid"]))
                overview = Overview.model_validate({
                    **result.stats, "source": result.source, "warnings": result.warnings,
                    "duration_seconds": result.duration_seconds, "top_nodes": nodes[:10],
                    "timeline": getattr(result, "timeline", []),
                }).model_dump()
                exports = {name: (staging / name).read_bytes() for name in EXPORT_NAMES}
                snapshot = Snapshot(result, nodes, edges, clusters,
                                    {n["gid"]: n for n in nodes},
                                    {c["cluster_id"]: c for c in clusters}, exports, overview)
                for name in EXPORT_NAMES:
                    os.replace(staging / name, self.output_dir / name)
                if (staging / "metadata.json").is_file():
                    os.replace(staging / "metadata.json", self.output_dir / "metadata.json")
                with self._lock:
                    self.snapshot = snapshot
                    self.error = None
                    self._status = RecalculateStatus(
                        status="completed", message="Расчёт завершён, данные и выгрузки обновлены",
                        duration_seconds=result.duration_seconds,
                    )
        except Exception as exc:
            logger.exception("Analytics calculation failed")
            # Explicit data-validation messages are useful locally. No dataset is
            # sent outside this process and no exception stack is returned.
            error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self.error = error
                self._status = RecalculateStatus(status="failed", message=error,
                                                 duration_seconds=time.perf_counter() - started)
