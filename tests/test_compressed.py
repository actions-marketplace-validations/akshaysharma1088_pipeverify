import gzip
import json
import subprocess
import sys

import duckdb
import pandas as pd
import pytest

from pipeverify.sources import SourceError, load, parse_source

DF = pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"]})


def loaded(path):
    con = duckdb.connect()
    load(con, parse_source(str(path)), "t")
    return con.execute('SELECT * FROM "t" ORDER BY id').fetchdf()


def test_csv_gz(tmp_path):
    p = tmp_path / "orders.csv.gz"
    with gzip.open(p, "wt") as f:
        DF.to_csv(f, index=False)
    pd.testing.assert_frame_equal(loaded(p), DF, check_dtype=False)


@pytest.mark.parametrize("name", ["orders.json.gz", "orders.jsonl.gz"])
def test_json_gz(tmp_path, name):
    p = tmp_path / name
    with gzip.open(p, "wt") as f:
        DF.to_json(f, orient="records", lines=True)
    pd.testing.assert_frame_equal(loaded(p), DF, check_dtype=False)


def test_csv_zst(tmp_path):
    p = tmp_path / "orders.csv.zst"
    con = duckdb.connect()
    con.register("src", DF)
    con.execute(f"COPY src TO '{p}' (COMPRESSION zstd)")
    pd.testing.assert_frame_equal(loaded(p), DF, check_dtype=False)


def test_unknown_type_inside_gz_is_rejected(tmp_path):
    p = tmp_path / "notes.txt.gz"
    with gzip.open(p, "wt") as f:
        f.write("hello")
    with pytest.raises(SourceError, match="Unsupported file type"):
        loaded(p)


def test_compressed_parquet_is_rejected_clearly(tmp_path):
    p = tmp_path / "orders.parquet.gz"
    p.write_bytes(b"not really parquet")
    with pytest.raises(SourceError, match="Parquet"):
        loaded(p)

def write_gz(path, df):
    with gzip.open(path, "wt") as f:
        df.to_csv(f, index=False)


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "pipeverify.cli", "compare", *args],
                          capture_output=True, text=True)


def test_cli_compare_csv_gz_identical_passes(tmp_path):
    a, b = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"
    write_gz(a, DF)
    write_gz(b, DF)
    r = run_cli(str(a), str(b), "--key", "id")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_cli_compare_csv_gz_detects_lost_rows(tmp_path):
    a, b = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"
    write_gz(a, DF)
    write_gz(b, DF.iloc[:2])
    r = run_cli(str(a), str(b), "--key", "id", "--format", "json")
    assert json.loads(r.stdout)["verdict"] == "block", r.stdout + r.stderr
