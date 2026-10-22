"""Collect public usage metrics and keep them in CSV files under metrics/.

GitHub keeps traffic data (views, clones, referrers) for only 14 days, so this script is run on a
schedule and merges each run into a growing history. Standard library only.

Environment:
  REPO            owner/name, default: GITHUB_REPOSITORY
  GH_TOKEN        token; traffic data needs a token with read access to repository Administration
  PYPI_PACKAGE    optional, PyPI package name for download counts
  ZENODO_RECORDS  optional, comma-separated Zenodo record ids
  OUT_DIR         default: metrics
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
PYPI = os.environ.get("PYPI_PACKAGE", "").strip()
ZENODO = [z.strip() for z in os.environ.get("ZENODO_RECORDS", "").split(",") if z.strip()]
OUT = os.environ.get("OUT_DIR", "metrics")
TODAY = dt.date.today().isoformat()


def get(url: str, auth: bool = False, raw: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": "pipeverify-metrics",
                                               "Accept": "application/vnd.github+json"})
    if auth and TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8")
    return body if raw else json.loads(body)


def merge(path: str, key: list[str], rows: list[dict], fields: list[str]) -> None:
    """Upsert rows into a CSV keyed by `key`; newer values win."""
    existing: dict[tuple, dict] = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                existing[tuple(r[k] for k in key)] = r
    for r in rows:
        existing[tuple(str(r[k]) for k in key)] = {f: r.get(f, "") for f in fields}
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k in sorted(existing):
            w.writerow({f: existing[k].get(f, "") for f in fields})


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def github() -> dict:
    snap: dict = {"date": TODAY, "repo": REPO}
    try:
        info = get(f"https://api.github.com/repos/{REPO}", auth=True)
        snap.update(stars=info["stargazers_count"], forks=info["forks_count"],
                    watchers=info.get("subscribers_count", ""), open_issues=info["open_issues_count"])
    except Exception as exc:  # noqa: BLE001
        warn(f"repo info: {exc}")
    try:
        html = get(f"https://github.com/{REPO}/network/dependents", raw=True)
        m = re.search(r"([\d,]+)\s*\n?\s*Repositor", html)
        snap["dependent_repos"] = m.group(1).replace(",", "") if m else ""
    except Exception as exc:  # noqa: BLE001
        warn(f"dependents: {exc}")

    daily: dict[str, dict] = {}
    try:
        for kind in ("views", "clones"):
            data = get(f"https://api.github.com/repos/{REPO}/traffic/{kind}", auth=True)
            for d in data.get(kind, []):
                day = d["timestamp"][:10]
                row = daily.setdefault(day, {"date": day})
                row[kind] = d["count"]
                row[f"unique_{kind}"] = d["uniques"]
        merge(f"{OUT}/github_traffic_daily.csv", ["date"], list(daily.values()),
              ["date", "views", "unique_views", "clones", "unique_clones"])
        refs = get(f"https://api.github.com/repos/{REPO}/traffic/popular/referrers", auth=True)
        merge(f"{OUT}/github_referrers.csv", ["date", "referrer"],
              [{"date": TODAY, **r} for r in refs], ["date", "referrer", "count", "uniques"])
    except urllib.error.HTTPError as exc:
        warn(f"traffic needs a token with Administration read access ({exc.code}); skipped")
    return snap


def pypi(snap: dict) -> None:
    if not PYPI:
        return
    try:
        data = get(f"https://pypistats.org/api/packages/{PYPI}/overall?mirrors=false")
        rows = [{"date": d["date"], "downloads": d["downloads"]} for d in data["data"]
                if d["category"] == "without_mirrors"]
        merge(f"{OUT}/pypi_downloads_daily.csv", ["date"], rows, ["date", "downloads"])
        recent = get(f"https://pypistats.org/api/packages/{PYPI}/recent")["data"]
        snap.update(pypi_last_week=recent.get("last_week", ""), pypi_last_month=recent.get("last_month", ""))
    except Exception as exc:  # noqa: BLE001
        warn(f"pypi: {exc}")


def zenodo() -> None:
    rows = []
    for rid in ZENODO:
        try:
            s = get(f"https://zenodo.org/api/records/{rid}").get("stats", {})
            rows.append({"date": TODAY, "record": rid, **{k: s.get(k, "") for k in
                         ("views", "unique_views", "downloads", "unique_downloads",
                          "version_views", "version_downloads")}})
        except Exception as exc:  # noqa: BLE001
            warn(f"zenodo {rid}: {exc}")
    if rows:
        merge(f"{OUT}/zenodo_stats.csv", ["date", "record"], rows,
              ["date", "record", "views", "unique_views", "downloads", "unique_downloads",
               "version_views", "version_downloads"])


def total(path: str, col: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, newline="") as f:
        return sum(int(r[col] or 0) for r in csv.DictReader(f))


def first_date(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, newline="") as f:
        dates = [r["date"] for r in csv.DictReader(f)]
    return min(dates) if dates else ""


def summary(snap: dict) -> None:
    t = f"{OUT}/github_traffic_daily.csv"
    p = f"{OUT}/pypi_downloads_daily.csv"
    lines = [
        "# Usage metrics", "",
        f"Updated {TODAY} by `scripts/collect_metrics.py`. Raw history is in the CSV files in this folder.", "",
        "| Metric | Value |", "|---|---|",
        f"| GitHub stars | {snap.get('stars', '')} |",
        f"| Forks | {snap.get('forks', '')} |",
        f"| Watchers | {snap.get('watchers', '')} |",
        f"| Repositories that depend on it | {snap.get('dependent_repos', '') or 'n/a'} |",
        f"| Repository views since {first_date(t) or 'n/a'} | {total(t, 'views'):,} |",
        f"| Repository clones since {first_date(t) or 'n/a'} | {total(t, 'clones'):,} |",
    ]
    if PYPI:
        lines += [f"| PyPI downloads since {first_date(p) or 'n/a'} (without mirrors) | {total(p, 'downloads'):,} |",
                  f"| PyPI downloads, last 30 days | {snap.get('pypi_last_month', '')} |"]
    with open(f"{OUT}/README.md", "w") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    if not REPO:
        sys.exit("Set REPO=owner/name")
    os.makedirs(OUT, exist_ok=True)
    snap = github()
    pypi(snap)
    zenodo()
    fields = ["date", "repo", "stars", "forks", "watchers", "open_issues", "dependent_repos",
              "pypi_last_week", "pypi_last_month"]
    merge(f"{OUT}/snapshots.csv", ["date"], [snap], fields)
    summary(snap)
    print(json.dumps(snap, indent=2))


if __name__ == "__main__":
    main()
