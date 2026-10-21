"""Build the retail demo: one 'before' table and five 'after' versions.

Each after version is a change that a CI pipeline would accept (the job runs, the schema
is fine) but only some of them leave the data right. Patterns come from the PipeMend
benchmark of data pipeline incidents.

Run: python examples/retail/make_demo.py
"""
import os

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
SC = os.path.join(HERE, "scenarios")

BASE = """
SELECT
  s.store_id,
  DATE '2026-09-01' + CAST(d.i AS INTEGER) AS sale_date,
  CASE s.store_id % 4 WHEN 0 THEN 'North' WHEN 1 THEN 'South' WHEN 2 THEN 'East' ELSE 'West' END AS region,
  'USD' AS currency,
  CAST(20 + (s.store_id * 7 + d.i * 13) % 25 AS INTEGER) AS orders,
  ROUND((20 + (s.store_id * 7 + d.i * 13) % 25) * (31.5 + (s.store_id % 5) * 2.25), 2) AS revenue,
  TIMESTAMP '2026-10-01 02:00:00' AS loaded_at
FROM range(1, 41) s(store_id), range(0, 30) d(i)
"""

SCENARIOS = {
    "1_safe_fix": (
        "Restates revenue for late refunds on one day, as intended.",
        "SELECT * REPLACE (CASE WHEN sale_date = DATE '2026-09-15' AND store_id IN (3, 7) "
        "THEN ROUND(revenue - 42.0, 2) ELSE revenue END AS revenue, "
        "TIMESTAMP '2026-10-02 02:00:00' AS loaded_at) FROM base",
        {"expect_change": ["revenue"]},
    ),
    "2_dedupe_drops_rows": (
        "An agent removed duplicates on the wrong columns (store and order count, not store and day).",
        "SELECT * FROM base QUALIFY row_number() OVER (PARTITION BY store_id, orders ORDER BY sale_date) = 1",
        {},
    ),
    "3_join_fanout": (
        "A new join to a store dimension with two rows for store 12 doubles its sales.",
        "SELECT * FROM base UNION ALL SELECT * FROM base WHERE store_id = 12",
        {},
    ),
    "4_unit_change": (
        "Upstream started sending West region revenue in cents. Same column, new meaning.",
        "SELECT * REPLACE (CASE WHEN region = 'West' AND sale_date >= DATE '2026-09-20' "
        "THEN revenue * 100 ELSE revenue END AS revenue) FROM base",
        {},
    ),
    "5_null_creep": (
        "A renamed source field leaves region empty for new stores.",
        "SELECT * REPLACE (CASE WHEN store_id > 38 THEN NULL ELSE region END AS region) FROM base",
        {},
    ),
}


def main():
    con = duckdb.connect()
    con.execute(f"CREATE TABLE base AS {BASE}")
    os.makedirs(os.path.join(HERE, "before"), exist_ok=True)
    before = os.path.join(HERE, "before", "daily_sales.parquet")
    con.execute(f"COPY base TO '{before}' (FORMAT parquet)")
    for name, (desc, sql, extra) in SCENARIOS.items():
        d = os.path.join(SC, name)
        os.makedirs(d, exist_ok=True)
        con.execute(f"COPY ({sql}) TO '{os.path.join(d, 'daily_sales.parquet')}' (FORMAT parquet)")
        lines = [f"# {desc}", "tables:", "  - name: gold.daily_sales",
                 "    before: ../../before/daily_sales.parquet", "    after: daily_sales.parquet",
                 "    key: [store_id, sale_date]", "    ignore: [loaded_at]"]
        if extra.get("expect_change"):
            lines.append(f"    expect_change: [{', '.join(extra['expect_change'])}]")
        with open(os.path.join(d, "pipeverify.yml"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"Demo written to {HERE}")


if __name__ == "__main__":
    main()
