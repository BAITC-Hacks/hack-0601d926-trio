"""Small deterministic synthetic fixture, never written over real input files."""

from pathlib import Path

import pandas as pd


def generate_demo_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    nodes = pd.DataFrame([
        {"gid": gid, "depth": 0 if gid <= 8 else 4 if gid >= 25 else 1 if gid <= 13 else 2 if gid <= 18 else 3,
         "is_seed": gid <= 8} for gid in range(1, 33)
    ])
    rows = []
    for gid in range(1, 8):
        rows.append({"src": gid, "dst": 9, "date": "2026-07-02", "sum_kzt": float(100000 + gid * 5000)})
    rows += [
        {"src": 9, "dst": 14, "date": "2026-07-03", "sum_kzt": 800000.0},
        {"src": 14, "dst": 19, "date": "2026-07-04", "sum_kzt": 780000.0},
        {"src": 19, "dst": 14, "date": "2026-07-06", "sum_kzt": 50000.0},
    ]
    for gid in range(25, 32):
        rows.append({"src": 19, "dst": gid, "date": "2026-07-05", "sum_kzt": 90000.0})
    for gid in range(2, 7):
        rows.append({"src": gid, "dst": 10, "date": "2026-07-08", "sum_kzt": 120000.0})
    rows.extend([
        {"src": 11, "dst": 15, "date": "2026-07-10", "sum_kzt": 70000.0},
        {"src": 15, "dst": 20, "date": "2026-07-11", "sum_kzt": 65000.0},
    ])
    tx = pd.DataFrame(rows)
    tx["date"] = pd.to_datetime(tx["date"])
    edges = tx.groupby(["src", "dst"], as_index=False).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
    depths = nodes.set_index("gid")["depth"]
    edges["depth"] = edges["src"].map(depths).add(1).clip(1, 4)
    return nodes, edges, tx


def write_demo(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("nodes", "edges", "transactions"):
        if (directory / f"{name}.parquet").exists():
            raise FileExistsError("Генератор не перезаписывает существующие parquet.")
    for name, frame in zip(("nodes", "edges", "transactions"), generate_demo_frames()):
        frame["__synthetic__"] = True
        frame.to_parquet(directory / f"{name}.parquet", index=False)
    (directory / "SYNTHETIC_DEMO.txt").write_text("Синтетические данные для разработки. Не реальные результаты.\n", encoding="utf-8")
