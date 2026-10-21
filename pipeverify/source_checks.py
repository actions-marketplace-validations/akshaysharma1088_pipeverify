"""Checks against the source: compare an output table with the raw data it was built from.

A before/after diff can only see damage to rows that existed before. Errors in new data
need evidence the pipeline does not control. These checks use the raw source table:

  source_conflict   the source sent several different values for one key and the output
                    kept one of them, so a choice was made that a person should confirm
  source_mismatch   an output value matches none of the values the source sent for that key
  source_missing    keys in the source (after an optional filter) that never reached the output
  source_orphan     keys in the output that do not exist in the source
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

from .checks import BLOCK, INFO, PASS, REVIEW, Finding, Thresholds, q


@dataclass
class SourceSpec:
    data: Any
    key: list[str] | None = None            # key column(s) in the source, default: the table key
    columns: dict[str, str] = field(default_factory=dict)  # output column -> source column
    where: str | None = None                # SQL filter for source rows that should reach the output
    checks: list[str] | None = None         # default ["conflict"]; also mismatch, missing, orphan

    @classmethod
    def from_dict(cls, d: Any) -> "SourceSpec":
        if isinstance(d, SourceSpec):
            return d
        if not isinstance(d, dict) or "data" not in d:
            raise ValueError("source needs at least 'data' (where the raw table is)")
        cols = d.get("columns") or {}
        if isinstance(cols, list):
            cols = {c: c for c in cols}
        key = d.get("key")
        if isinstance(key, str):
            key = [k.strip() for k in key.split(",") if k.strip()]
        return cls(data=d["data"], key=key, columns=dict(cols), where=d.get("where"),
                   checks=d.get("checks"))


def _grade(pct: float, review: float, block: float) -> str:
    if pct >= block and pct > 0:
        return BLOCK
    if pct > review or (review == 0 and pct > 0):
        return REVIEW
    return PASS


def run_source_checks(con: duckdb.DuckDBPyConnection, after: str, source: str, spec: SourceSpec,
                      key: list[str], t: Thresholds) -> tuple[list[Finding], dict[str, Any]]:
    if not key:
        raise ValueError("Source checks need a key on the table")
    skey = spec.key or key
    if len(skey) != len(key):
        raise ValueError("The source key must have as many columns as the table key")
    wanted = set(spec.checks or ["conflict"])  # others are opt-in: they need a filter or pass-through columns
    where = f"WHERE {spec.where}" if spec.where else ""
    # normalized source view: key columns renamed to the output's key names, compared as text
    sel = [f"CAST({q(s)} AS VARCHAR) AS {q('_k' + str(i))}" for i, s in enumerate(skey)]
    sel += [f"{q(sc)} AS {q('_v_' + oc)}" for oc, sc in spec.columns.items()]
    con.execute(f"CREATE OR REPLACE TEMP VIEW _pv_src AS SELECT {', '.join(sel)} FROM {q(source)} {where}")
    akeys = ", ".join(f"CAST({q(k)} AS VARCHAR) AS {q('_k' + str(i))}" for i, k in enumerate(key))
    con.execute(f"CREATE OR REPLACE TEMP VIEW _pv_out AS SELECT {akeys}, * FROM {q(after)}")
    on = " AND ".join(f"o.{q('_k' + str(i))} IS NOT DISTINCT FROM s.{q('_k' + str(i))}" for i in range(len(key)))
    kcols = ", ".join(q("_k" + str(i)) for i in range(len(key)))

    findings: list[Finding] = []
    n_out = con.execute("SELECT count(*) FROM _pv_out").fetchone()[0]
    n_src = con.execute(f"SELECT count(DISTINCT ({kcols})) FROM _pv_src").fetchone()[0]
    stats = {"source_keys": n_src, "output_rows": n_out}

    for oc in spec.columns:
        v = q("_v_" + oc)
        if "conflict" in wanted:
            n = con.execute(
                f"SELECT count(*) FROM (SELECT {kcols} FROM _pv_src GROUP BY ALL "
                f"HAVING count(DISTINCT CAST({v} AS VARCHAR)) > 1) s "
                f"WHERE EXISTS (SELECT 1 FROM _pv_out o WHERE {on})").fetchone()[0]
            if n:
                ex = con.execute(
                    f"SELECT {kcols}, list(DISTINCT CAST({v} AS VARCHAR) ORDER BY CAST({v} AS VARCHAR)) FROM _pv_src "
                    f"GROUP BY ALL HAVING count(DISTINCT CAST({v} AS VARCHAR)) > 1 LIMIT 3").fetchall()
                pct = n / max(n_out, 1) * 100
                findings.append(Finding(
                    "source_conflict", REVIEW,
                    f"{n:,} key(s) arrived from the source with conflicting '{oc}' values and the output kept one "
                    f"({pct:.2f}% of rows). Confirm which value is right.",
                    oc, {"count": n, "pct": round(pct, 4), "examples": [[str(x) for x in r] for r in ex]}))
        if "mismatch" in wanted:
            num = (f"TRY_CAST(o.{q(oc)} AS DOUBLE) IS NOT NULL AND TRY_CAST(s.{v} AS DOUBLE) IS NOT NULL AND "
                   f"abs(TRY_CAST(o.{q(oc)} AS DOUBLE) - TRY_CAST(s.{v} AS DOUBLE)) <= "
                   f"{t.float_tolerance} * greatest(1.0, abs(TRY_CAST(s.{v} AS DOUBLE)))")
            txt = f"trim(CAST(o.{q(oc)} AS VARCHAR)) = trim(CAST(s.{v} AS VARCHAR))"
            n = con.execute(
                f"SELECT count(*) FROM _pv_out o WHERE EXISTS (SELECT 1 FROM _pv_src s WHERE {on}) "
                f"AND NOT EXISTS (SELECT 1 FROM _pv_src s WHERE {on} AND (({num}) OR {txt} "
                f"OR (o.{q(oc)} IS NULL AND s.{v} IS NULL)))").fetchone()[0]
            if n:
                pct = n / max(n_out, 1) * 100
                sev = _grade(pct, t.source_mismatch_review_pct, t.source_mismatch_block_pct)
                findings.append(Finding("source_mismatch", sev,
                                        f"{n:,} row(s) have a '{oc}' value the source never sent ({pct:.2f}%)",
                                        oc, {"count": n, "pct": round(pct, 4)}))
    if "missing" in wanted:
        n = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {kcols} FROM _pv_src) s "
                        f"WHERE NOT EXISTS (SELECT 1 FROM _pv_out o WHERE {on})").fetchone()[0]
        if n:
            pct = n / max(n_src, 1) * 100
            findings.append(Finding("source_missing", _grade(pct, t.source_missing_review_pct, t.source_missing_block_pct),
                                    f"{n:,} source key(s) never reached the output ({pct:.2f}%)", None,
                                    {"count": n, "pct": round(pct, 4)}))
    if "orphan" in wanted:
        n = con.execute(f"SELECT count(*) FROM _pv_out o WHERE NOT EXISTS (SELECT 1 FROM _pv_src s WHERE {on})").fetchone()[0]
        if n:
            pct = n / max(n_out, 1) * 100
            findings.append(Finding("source_orphan", REVIEW if pct > 0 else INFO,
                                    f"{n:,} output row(s) have keys that are not in the source ({pct:.2f}%)", None,
                                    {"count": n, "pct": round(pct, 4)}))
    stats["source_checks"] = sorted(wanted)
    return findings, stats
