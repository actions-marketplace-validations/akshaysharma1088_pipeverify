"""Held-out check: pipeline code changes of kinds NOT in PipeMend, on clean PipeMend data.
pipeverify runs with default thresholds and no code changes. Compare with the PipeMend monitors."""
import sys, json, collections
PM, PV = sys.argv[1], sys.argv[2]
sys.path[:0] = [PM, PV]
import pipeverify
from pipebench import core
from pipebench.harness import GATE_IGNORE

KEYS = {"silver_transactions": ["txn_id"], "silver_customers": ["customer_id"], "silver_merchants": ["merchant_id"],
        "gold_merchant_daily": ["txn_date", "merchant_id"], "gold_region_daily": ["txn_date", "region"],
        "gold_mcc_daily": ["txn_date", "mcc"], "gold_customer_spend": ["customer_id"]}
B = core.BASE_PIPELINE
def sub(step, old, new):
    assert old in B[step], (step, old)
    return {step: B[step].replace(old, new)}

CHANGES = [  # (id, harmful?, description, overrides, intent per table)
 ("H1", True, "Reversed transactions counted as spend (status filter widened)",
   sub("silver_transactions", "WHERE status = 'POSTED'", "WHERE status IN ('POSTED', 'REVERSED')"), {}),
 ("H2", True, "Fixed six-hour time zone shift applied to every row",
   {"silver_transactions": B["silver_transactions"].replace("CAST(txn_ts AS TIMESTAMP) AS txn_ts", "CAST(txn_ts AS TIMESTAMP) + INTERVAL 6 HOUR AS txn_ts")
     .replace("CAST(CAST(txn_ts AS TIMESTAMP) AS DATE) AS txn_date", "CAST(CAST(txn_ts AS TIMESTAMP) + INTERVAL 6 HOUR AS DATE) AS txn_date")}, {}),
 ("H3", True, "Amounts cast to integer, cents truncated",
   sub("silver_transactions", "CAST(amount AS DECIMAL(12,2)) AS amount", "CAST(floor(amount) AS DECIMAL(12,2)) AS amount"), {}),
 ("H4", True, "New region filter silently drops one region",
   sub("silver_customers", "FROM raw_customers", "FROM raw_customers WHERE upper(region) IN ('NORTH', 'SOUTH', 'EAST')"), {}),
 ("H5", True, "Off-by-one date window drops the latest day",
   sub("silver_transactions", "WHERE status = 'POSTED'", "WHERE status = 'POSTED' AND CAST(txn_ts AS DATE) < (SELECT max(CAST(txn_ts AS DATE)) FROM raw_transactions)"), {}),
 ("H6", True, "Self-join on category multiplies merchant rows",
   {"silver_merchants": "CREATE OR REPLACE TABLE silver_merchants AS SELECT CAST(m.merchant_id AS INTEGER) AS merchant_id, m.mcc FROM raw_merchants m JOIN raw_merchants p ON m.mcc = p.mcc AND p.merchant_id < 110"}, {}),
 ("H7", True, "Unit conversion applied to one category only (transit amounts divided by 100)",
   sub("silver_transactions", "CAST(amount AS DECIMAL(12,2)) AS amount",
       "CAST(CASE WHEN CAST(merchant_id AS INTEGER) IN (SELECT CAST(merchant_id AS INTEGER) FROM raw_merchants WHERE mcc = '4111') THEN amount / 100 ELSE amount END AS DECIMAL(12,2)) AS amount"), {}),
 ("H8", True, "Customer key lower-cased, gold joins lose every row",
   sub("silver_customers", "SELECT customer_id,", "SELECT lower(customer_id) AS customer_id,"), {}),
 ("C1", False, "Refactor silver_transactions into a CTE (same output)",
   {"silver_transactions": "CREATE OR REPLACE TABLE silver_transactions AS WITH p AS (SELECT * FROM raw_transactions WHERE status = 'POSTED') "
     "SELECT CAST(txn_id AS BIGINT) AS txn_id, customer_id, CAST(merchant_id AS INTEGER) AS merchant_id, CAST(amount AS DECIMAL(12,2)) AS amount, "
     "CAST(txn_ts AS TIMESTAMP) AS txn_ts, CAST(CAST(txn_ts AS TIMESTAMP) AS DATE) AS txn_date FROM p"}, {}),
 ("C2", False, "Add a distinct-customer count to gold_merchant_daily",
   sub("gold_merchant_daily", "count(*) AS n_txn,", "count(*) AS n_txn, count(DISTINCT customer_id) AS n_customers,"), {}),
 ("C3", False, "Rewrite the region join as LEFT JOIN with explicit ON (same output)",
   sub("gold_region_daily", "JOIN silver_customers c USING (customer_id)", "LEFT JOIN silver_customers c ON t.customer_id = c.customer_id"), {}),
 ("C4", False, "Intended: exclude a merchant closed for fraud (declared row change)",
   sub("silver_transactions", "WHERE status = 'POSTED'", "WHERE status = 'POSTED' AND CAST(merchant_id AS INTEGER) <> 139"),
   {"silver_transactions": dict(expect_row_change=True), "gold_merchant_daily": dict(expect_row_change=True),
    "gold_region_daily": dict(expect_change=["n_txn", "total"]), "gold_mcc_daily": dict(expect_change=["n_txn", "total"]),
    "gold_customer_spend": dict(expect_change=["n_txn", "total"])}),
 ("C5", False, "Intended: round gold totals to whole cents explicitly",
   {g: B[g].replace("sum(amount) AS total", "round(sum(amount), 2) AS total").replace("sum(t.amount) AS total", "round(sum(t.amount), 2) AS total")
    for g in ("gold_merchant_daily", "gold_region_daily", "gold_mcc_daily", "gold_customer_spend")}, {}),
]

RANK = {"pass": 0, "review": 1, "block": 2}
def snap(con): return {t: con.execute(f"SELECT * FROM {t}").fetchdf() for t in KEYS}

rows = []
for seed in (1, 2):
    raw = core.generate_clean(seed)
    con = core.load(core.copy_raw(raw)); assert core.run_pipeline(con, B) is None
    before = snap(con)
    for cid, harmful, desc, over, intent in CHANGES:
        steps = dict(B); steps.update(over)
        con = core.load(core.copy_raw(raw)); err = core.run_pipeline(con, steps)
        assert err is None, (cid, err)
        mon = [a for a in core.monitors(con) if not a.startswith(GATE_IGNORE)]
        after = snap(con)
        worst, why = "pass", []
        for t, k in KEYS.items():
            r = pipeverify.verify(before[t], after[t], name=t, key=k, **intent.get(t, {}))
            if RANK[r.verdict] > RANK[worst]: worst = r.verdict
            why += [f"{t}: {f.message}" for f in r.findings if f.severity in ("review", "block")]
        rawdf = con.execute("SELECT * FROM raw_transactions").fetchdf()
        src = pipeverify.verify(None, after["silver_transactions"], name="silver_transactions", key="txn_id",
                                source={"data": rawdf, "key": ["txn_id"], "columns": {"amount": "amount"}, "checks": ["conflict"]})
        v2 = worst if RANK[worst] >= RANK[src.verdict] else src.verdict
        rows.append(dict(seed=seed, id=cid, harmful=harmful, desc=desc, monitors=mon[:3], monitors_flag=bool(mon), pv=v2, why=why[:3]))
        print(seed, cid, "HARM" if harmful else "ok  ", "mon:", "FLAG" if mon else "-   ", "pv:", v2.upper(), "|", (mon[:1] or [""])[0][:60], "|", (why[:1] or [""])[0][:70])
json.dump(rows, open("heldout_rows.json", "w"), indent=1)
H = [r for r in rows if r["harmful"]]; C = [r for r in rows if not r["harmful"]]
f = lambda rs, k: sum(1 for r in rs if (r["monitors_flag"] if k == "m" else r["pv"] != "pass" if k == "p" else (r["monitors_flag"] or r["pv"] != "pass")))
print(f"\nharmful {len(H)}: monitors {f(H,'m')}, pipeverify {f(H,'p')}, either {f(H,'e')}")
print(f"correct {len(C)}: monitors FA {f(C,'m')}, pipeverify FA {f(C,'p')}")
print("pv verdicts harmful:", collections.Counter(r["pv"] for r in H), "correct:", collections.Counter(r["pv"] for r in C))
