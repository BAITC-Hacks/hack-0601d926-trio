"""Validate without silently repairing or dropping observations."""

from pathlib import Path
from numbers import Number
from typing import Any

import numpy as np
import pandas as pd


class DataValidationError(ValueError):
    """Input is incomplete, inconsistent or cannot safely be interpreted."""


SCHEMAS = {
    "nodes": ["gid", "depth", "is_seed"],
    "edges": ["src", "dst", "sum_kzt", "n_tx", "depth"],
    "transactions": ["src", "dst", "date", "sum_kzt"],
}


def _integer(frame: pd.DataFrame, column: str, label: str) -> None:
    values = pd.to_numeric(frame[column], errors="coerce")
    if pd.api.types.is_float_dtype(values.dtype) and (values.abs() >= 2 ** 53).any():
        raise DataValidationError(f"{label}.{column}: большое целое представлено float; точность могла быть потеряна. Нужен int64 или точная десятичная строка.")
    if values.isna().any() or not np.isfinite(values).all() or (values != np.floor(values)).any():
        raise DataValidationError(f"{label}.{column}: ожидаются целые числа без пропусков.")
    if (values < np.iinfo(np.int64).min).any() or (values > np.iinfo(np.int64).max).any():
        raise DataValidationError(f"{label}.{column}: значение вне диапазона int64.")
    try:
        frame[column] = values.astype("int64")
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError(f"{label}.{column}: идентификатор вне диапазона int64.") from exc


def validate_frames(
    nodes: pd.DataFrame, edges: pd.DataFrame, transactions: pd.DataFrame, config: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    frames = {"nodes": nodes.copy(), "edges": edges.copy(), "transactions": transactions.copy()}
    warnings: list[str] = []
    for label, frame in frames.items():
        missing = set(SCHEMAS[label]) - set(frame.columns)
        if missing:
            raise DataValidationError(f"{label}.parquet: отсутствуют колонки {', '.join(sorted(missing))}.")
        if frame[SCHEMAS[label]].isna().any().any():
            raise DataValidationError(f"{label}.parquet: пропуски в обязательных колонках.")
        for column in ("gid", "src", "dst", "depth", "n_tx"):
            if column in SCHEMAS[label]:
                _integer(frame, column, label)
        if "sum_kzt" in SCHEMAS[label]:
            frame["sum_kzt"] = pd.to_numeric(frame["sum_kzt"], errors="coerce")
            if not np.isfinite(frame["sum_kzt"]).all() or (frame["sum_kzt"] <= 0).any():
                raise DataValidationError(f"{label}.sum_kzt: суммы должны быть конечными и положительными.")
            frame["sum_kzt"] = frame["sum_kzt"].astype(float)
    nodes, edges, tx = frames["nodes"], frames["edges"], frames["transactions"]
    if nodes.empty:
        raise DataValidationError("nodes.parquet: требуется хотя бы один узел.")
    if nodes["gid"].duplicated().any():
        raise DataValidationError("nodes.parquet: дубли gid запрещены.")
    if edges.duplicated(["src", "dst"]).any():
        raise DataValidationError("edges.parquet: дубли пар src/dst; ожидается одно агрегированное ребро.")
    bool_values = {True: True, False: False, "true": True, "false": False, "True": True, "False": False}
    nodes["is_seed"] = nodes["is_seed"].map(bool_values)
    if nodes["is_seed"].isna().any():
        raise DataValidationError("nodes.is_seed: допустимы только bool или 0/1.")
    nodes["is_seed"] = nodes["is_seed"].astype(bool)
    if not nodes["depth"].between(0, config["max_depth"]).all():
        raise DataValidationError(f"nodes.depth: ожидается глубина 0–{config['max_depth']}.")
    if ((nodes["depth"] == 0) != nodes["is_seed"]).any():
        raise DataValidationError("nodes: depth=0 должен соответствовать is_seed=true.")
    if not edges["depth"].between(1, config["max_depth"]).all() or (edges["n_tx"] <= 0).any():
        raise DataValidationError(f"edges: depth должен быть 1–{config['max_depth']}, n_tx должен быть положительным.")
    gids = set(nodes["gid"])
    for label, frame in [("edges", edges), ("transactions", tx)]:
        absent = (set(frame["src"]) | set(frame["dst"])) - gids
        if absent:
            raise DataValidationError(f"{label}: ссылки на отсутствующие nodes: {sorted(absent)[:5]}.")
    if any(isinstance(value, Number) for value in tx["date"]):
        raise DataValidationError("transactions.date: числовые даты неоднозначны; ожидается дата или ISO-строка.")
    try:
        tx["date"] = pd.to_datetime(tx["date"], errors="raise", utc=True, format="mixed").dt.tz_convert(None)
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataValidationError("transactions.date: некорректная дата.") from exc
    if tx["date"].isna().any():
        raise DataValidationError("transactions.date: некорректная или пустая дата.")
    if (tx["sum_kzt"] < config["min_transaction_kzt"]).any():
        raise DataValidationError(f"transactions: сумма ниже заявленного порога {config['min_transaction_kzt']} KZT.")
    duplicate_count = int(tx.duplicated(SCHEMAS["transactions"]).sum())
    if duplicate_count:
        warnings.append(f"{duplicate_count} повторных комбинаций отправитель/получатель/дата/сумма сохранены: без ID операции нельзя отличить дубль от повторного платежа.")
    aggregate = tx.groupby(["src", "dst"], as_index=False).agg(tx_sum=("sum_kzt", "sum"), tx_count=("sum_kzt", "size"))
    comparison = edges.merge(aggregate, on=["src", "dst"], how="outer", indicator=True)
    if not (comparison["_merge"] == "both").all():
        raise DataValidationError("edges и transactions не совпадают по парам src/dst.")
    if not np.isclose(comparison["sum_kzt"], comparison["tx_sum"], atol=config["sum_tolerance_kzt"], rtol=0).all():
        raise DataValidationError("edges.sum_kzt не совпадает с суммой transactions для пары src/dst.")
    if not (comparison["n_tx"] == comparison["tx_count"]).all():
        raise DataValidationError("edges.n_tx не совпадает с числом transactions для пары src/dst.")
    if tx.empty:
        warnings.append("Транзакций нет: доступна только структура списка узлов.")
    else:
        periods = tx["date"].dt.to_period("M").unique()
        if len(periods) != 1 or str(periods[0]) != "2026-07":
            warnings.append("Период данных отличается от исходного июля 2026; фактические даты показаны в статистике.")
    if (edges["src"] == edges["dst"]).any():
        warnings.append("Есть переводы на тот же gid; они сохранены в обороте и циклах, исключены из временного сопоставления потоков.")
    return (
        nodes.sort_values("gid", kind="stable").reset_index(drop=True),
        edges.sort_values(["src", "dst"], kind="stable").reset_index(drop=True),
        tx.sort_values(["date", "src", "dst", "sum_kzt"], kind="stable").reset_index(drop=True),
        warnings,
    )


def load_data(data_dir: Path, config: dict[str, Any]):
    paths = {name: data_dir / f"{name}.parquet" for name in SCHEMAS}
    present = [name for name, path in paths.items() if path.is_file()]
    if not present:
        from .demo import generate_demo_frames
        frames = generate_demo_frames()
        nodes, edges, tx, warnings = validate_frames(*frames, config)
        warnings.insert(0, "ДЕМОНСТРАЦИОННЫЕ СИНТЕТИЧЕСКИЕ ДАННЫЕ. Реальные parquet не найдены; результаты не относятся к исходной сети.")
        return nodes, edges, tx, warnings, "synthetic"
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise DataValidationError(f"Неполный набор parquet: отсутствуют {', '.join(missing)}. Синтетическая подмена запрещена.")
    try:
        frames = {name: pd.read_parquet(path) for name, path in paths.items()}
    except Exception as exc:
        raise DataValidationError(f"Не удалось прочитать parquet: {exc}") from exc
    synthetic_markers = ["__synthetic__" in frame.columns and bool(frame["__synthetic__"].eq(True).all()) for frame in frames.values()]
    if any(synthetic_markers) and not all(synthetic_markers):
        raise DataValidationError("Смешаны синтетические и реальные parquet; используйте отдельные каталоги.")
    source = "synthetic" if all(synthetic_markers) else "real"
    nodes, edges, tx, warnings = validate_frames(frames["nodes"], frames["edges"], frames["transactions"], config)
    if source == "synthetic":
        warnings.insert(0, "ДЕМОНСТРАЦИОННЫЕ СИНТЕТИЧЕСКИЕ ДАННЫЕ: в parquet сохранён явный признак __synthetic__.")
    return nodes, edges, tx, warnings, source
