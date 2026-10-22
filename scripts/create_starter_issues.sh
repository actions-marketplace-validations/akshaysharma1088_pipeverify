#!/usr/bin/env bash
# One-time: create labels and starter issues for new contributors. Needs the GitHub CLI (gh auth login).
set -euo pipefail
R="${1:-akshaysharma1088/pipeverify}"

gh label create "good first issue" -R "$R" -c 7057ff -d "Good for newcomers" 2>/dev/null || true
gh label create "help wanted" -R "$R" -c 008672 -d "Extra attention is welcome" 2>/dev/null || true
gh label create "adopter" -R "$R" -c 0e8a16 -d "Someone sharing how they use pipeverify" 2>/dev/null || true
gh label create "integration" -R "$R" -c 1d76db -d "dbt, Airflow, Dagster and other tools" 2>/dev/null || true

issue() { gh issue create -R "$R" --title "$1" --label "$2" --body "$3"; }

issue "Read compressed files (.csv.gz, .json.gz, .parquet.gz)" "good first issue" \
"Snapshots are often stored compressed. \`pipeverify/sources.py\` picks a reader from the last file extension, so \`orders.csv.gz\` is rejected as an unsupported type.

DuckDB already reads gzip files, so the change is to detect the extension before \`.gz\` / \`.zst\`.

**Done when**
- \`pipeverify compare a.csv.gz b.csv.gz -k id\` works
- A test in \`tests/\` covers a compressed CSV"

issue "Do not sum ID-like columns in the column total check" "good first issue" \
"The total-shift check sums every numeric column, including identifiers such as \`store_id\`. When rows are added this produces a finding like \"Total of 'store_id' moved 3.68%\", which is noise.

Idea: skip columns that are part of the key, plus integer columns whose name ends in \`_id\` or \`id\`, with a threshold option to turn this off.

**Done when**
- The fan-out example in \`examples/notebooks/quickstart.ipynb\` no longer reports \`store_id\`
- A test covers it"

issue "Add a --quiet option that prints only the verdict" "good first issue" \
"For shell scripts it is handy to print just \`PASS\`, \`REVIEW\` or \`BLOCK\`. Add \`--quiet\` to the shared output options in \`pipeverify/cli.py\`. The exit code should not change.

**Done when**
- \`pipeverify compare ... --quiet\` prints one word
- A CLI test covers it (click's \`CliRunner\` works well)"

issue "Example: run pipeverify inside an Airflow task" "good first issue,integration" \
"Add \`examples/airflow/\` with a small DAG that builds a table, calls \`pipeverify.verify\` against the previous snapshot, and fails the task on BLOCK. A README with how to run it locally is enough; it does not need to run in CI."

issue "Example: check a dbt pull request" "help wanted,integration" \
"Show how to compare a dbt model built in a PR schema against production, using the GitHub Action. A minimal approach: \`dbt build --target pr\`, export both versions to Parquet (or point a SQLAlchemy source at both schemas), then run the action.

Add \`examples/dbt/\` with the workflow file and a README."

issue "Docs: Postgres, Snowflake and BigQuery config examples" "good first issue" \
"The README shows a generic SQLAlchemy source. Add a short docs page with tested config snippets for Postgres, Snowflake and BigQuery: the driver to install, the URL format, and how to keep credentials in environment variables."

echo "Done. Also turn on Discussions: Settings > General > Features > Discussions."
