"""Public API: verify one table or a whole config of tables."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb
import yaml

from .checks import BLOCK, PASS, RANK, REVIEW, Finding, Thresholds, run_checks, verdict
from .source_checks import SourceSpec, run_source_checks
from .sources import load, parse_source


@dataclass
class TableResult:
    name: str
    verdict: str
    findings: list[Finding]
    stats: dict[str, Any]
    before: str
    after: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "verdict": self.verdict, "before": self.before, "after": self.after,
                "stats": self.stats, "error": self.error,
                "findings": [f.to_dict() for f in self.findings]}


@dataclass
class RunResult:
    tables: list[TableResult] = field(default_factory=list)
    seconds: float = 0.0
    explanation: str | None = None

    @property
    def verdict(self) -> str:
        worst = max((RANK[t.verdict] for t in self.tables), default=0)
        return {0: PASS, 1: REVIEW, 2: BLOCK}[worst]

    def to_dict(self) -> dict[str, Any]:
        from . import __version__
        return {"tool": "pipeverify", "version": __version__, "verdict": self.verdict,
                "seconds": round(self.seconds, 3), "explanation": self.explanation,
                "tables": [t.to_dict() for t in self.tables]}


def verify(before: Any, after: Any, *, name: str = "table", key: list[str] | str | None = None,
           expect_change: list[str] | None = None, ignore: list[str] | None = None,
           expect_row_change: bool = False, thresholds: Thresholds | dict | None = None,
           source: SourceSpec | dict | None = None, base_dir: str = ".") -> TableResult:
    """Compare two snapshots of one table, and optionally the output with its raw source.

    `before`, `after` and `source["data"]` can be file paths, duckdb:<file>:<table>
    strings, config mappings, or pandas DataFrames. `before` may be None when only
    source checks are wanted (for example when no earlier snapshot exists).
    """
    if isinstance(key, str):
        key = [k.strip() for k in key.split(",") if k.strip()]
    if not isinstance(thresholds, Thresholds):
        thresholds = Thresholds.from_dict(thresholds)
    if before is None and source is None:
        raise ValueError("Give a before snapshot, a source, or both")
    src_b = parse_source(before, base_dir) if before is not None else None
    src_a = parse_source(after, base_dir)
    con = duckdb.connect()
    try:
        load(con, src_a, "after")
        if src_b is not None:
            load(con, src_b, "before")
            findings, stats = run_checks(con, "before", "after", key=key, expect_change=expect_change,
                                         ignore=ignore, expect_row_change=expect_row_change,
                                         thresholds=thresholds)
            if not key:
                findings.append(Finding("keys", "info",
                                        "No key given, so row-level changes were not compared. Add a key for stronger checks."))
        else:
            findings = []
            stats = {"rows_after": con.execute('SELECT count(*) FROM "after"').fetchall()[0][0]}
        if source is not None:
            spec = SourceSpec.from_dict(source)
            load(con, parse_source(spec.data, base_dir), "source")
            sf, sstats = run_source_checks(con, "after", "source", spec, key or [], thresholds)
            findings += sf
            stats.update(sstats)
    finally:
        con.close()
    order = {BLOCK: 0, REVIEW: 1, "info": 2, PASS: 3}
    findings.sort(key=lambda f: order[f.severity])
    return TableResult(name=name, verdict=verdict(findings), findings=findings, stats=stats,
                       before=src_b.describe() if src_b else "(none)", after=src_a.describe())


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict) or "tables" not in cfg:
        raise ValueError(f"{path}: config needs a 'tables' list")
    return cfg


def run_config(path: str, only: list[str] | None = None) -> RunResult:
    cfg = load_config(path)
    base_dir = os.path.dirname(os.path.abspath(path))
    defaults = cfg.get("thresholds") or {}
    result = RunResult()
    start = time.perf_counter()
    for spec in cfg["tables"]:
        name = spec.get("name") or spec.get("after")
        if only and name not in only:
            continue
        th = {**defaults, **(spec.get("thresholds") or {})}
        try:
            res = verify(spec.get("before"), spec["after"], name=str(name), key=spec.get("key"),
                         expect_change=spec.get("expect_change"), ignore=spec.get("ignore"),
                         expect_row_change=bool(spec.get("expect_row_change", False)),
                         thresholds=th, source=spec.get("source"), base_dir=base_dir)
        except Exception as exc:  # report the table as blocked, keep checking the rest
            res = TableResult(name=str(name), verdict=BLOCK, stats={},
                              findings=[Finding("error", BLOCK, f"Could not check this table: {exc}")],
                              before=str(spec.get("before")), after=str(spec.get("after")), error=str(exc))
        result.tables.append(res)
    result.seconds = time.perf_counter() - start
    return result
