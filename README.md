# adk_metals_data_pipeline

An agentic data pipeline built with **Google ADK 2.x**. It pulls six U.S. metals producer price indexes from the FRED API, computes trend and volatility metrics with pandas, writes raw and processed files to cloud object storage (Amazon S3, Azure Blob Storage, Google Cloud Storage, or a local folder), and publishes a static HTML dashboard with AI-written commentary.

One command runs it:

```powershell
adk run metals_pipeline "Refresh the metals price dashboard from 2018, focus on steel"
```

## What it does

1. A **planner** LLM agent turns the request into a structured `RunPlan` (series, start date, base month, focus).
2. `validate_plan` checks the plan against a series allowlist, clamps dates, and assigns a `run_id`.
3. `ingest` calls FRED for each series and stores the raw JSON exactly as returned.
4. `transform` cleans the data and computes rebased indexes, MoM %, YoY %, rolling 12-month volatility, an annual rollup, and a latest snapshot.
5. `quality_gate` runs data checks and routes the run to **pass** or **fail**.
6. On pass, an **analyst** LLM agent writes 3 to 5 insights using only the computed figures, and `publish` renders the dashboard and writes a run manifest.
7. On fail, `report_failure` writes a manifest explaining which checks failed, and nothing is published.

## Architecture

<img width="2400" height="1261" alt="Metals Pipeline Architecture" src="https://github.com/user-attachments/assets/bcfe00bb-9634-4aed-9f73-056f29187130" />


The root agent is an ADK `Workflow` defined in [metals_pipeline/agent.py](metals_pipeline/agent.py):

```python
root_agent = Workflow(
    name="metals_pipeline",
    edges=[
        ("START", planner, validate_plan, ingest, transform, quality_gate,
         {"pass": analyst, "fail": report_failure}),
        (analyst, publish),
    ],
)
```

| Node | Kind | Hands on |
| --- | --- | --- |
| `planner` | ADK `Agent`, `output_schema=RunPlan` | `RunPlan` |
| `validate_plan` | Function | `ValidatedPlan`, `run_id` in session state |
| `ingest` | Function | `IngestResult` (object paths, row and null counts) |
| `transform` | Function | `MetricsSummary` (latest row per series, CSV paths) |
| `quality_gate` | Function | `Event(route="pass" or "fail")` |
| `analyst` | ADK `Agent`, `input_schema=MetricsSummary`, `output_schema=Commentary` | `Commentary` |
| `publish` | Function | Final message with object paths |
| `report_failure` | Function | Final message with failure reasons |

## Data

Six monthly BLS producer price indexes, via FRED:

| Series | Measures |
| --- | --- |
| `WPU10` | Metals and metal products (headline) |
| `WPU101` | Iron and steel |
| `WPU1017` | Steel mill products |
| `WPU102501` | Aluminum mill shapes |
| `WPU102502` | Copper and brass mill shapes |
| `PCU331110331110` | Iron and steel mills and ferroalloy manufacturing |

Series titles and units shown on the dashboard come from FRED's `series` endpoint. None are typed by hand.

## Prerequisites

- Python 3.11 or newer
- A free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html)
- A free [Gemini API key](https://aistudio.google.com/apikey) from Google AI Studio
- For S3 mode only: an AWS account, an S3 bucket, and either the [AWS CLI](https://aws.amazon.com/cli/) or an IAM access key
- For Azure mode only: an Azure subscription (Azure for Students works) and the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- For GCS mode only: the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and a GCP project with billing or credits

## Setup

Windows (PowerShell). On macOS or Linux, use `source .venv/bin/activate` and `cp` instead.

```powershell
git clone <repo-url>
cd adk_metals_data_pipeline
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then fill in GOOGLE_API_KEY and FRED_API_KEY
```

ADK loads `.env` from the agent folder or any parent folder, so either `.env` at the repo root or `metals_pipeline/.env` works.



## Run

### Local mode (default, no cloud account needed)

```powershell
adk run metals_pipeline "Refresh the metals price dashboard from 2018"
start output\site\latest\index.html
```

### Amazon S3 mode

One-time setup (bucket names are globally unique; keep Block Public Access on):

```powershell
aws configure                      # or: aws sso login
aws s3api create-bucket --bucket <yourbucket> --region us-east-2 `
  --create-bucket-configuration LocationConstraint=us-east-2
```

The identity you use needs `s3:PutObject`, `s3:GetObject` and `s3:ListBucket` on the bucket (`ListBucket` makes missing objects return 404 rather than 403).

Then set these values in `.env` and rerun the same `adk run` command:

```
STORAGE_BACKEND=s3
S3_BUCKET=<yourbucket>
AWS_REGION=us-east-2
```

Credentials come from boto3's default chain (`aws configure`, `aws sso login`, `AWS_ACCESS_KEY_ID` env vars, or an IAM role), so nothing needs to sit in `.env`. If you'd rather use an IAM user's access key, set `S3_AccessKeyId` and `S3_SecretAccessKey` in `.env` instead. `.env` is gitignored.

Open the dashboard. The bucket stays private, so download it with the pipeline's own credentials:

```powershell
python -m metals_pipeline.view_dashboard              # latest run
python -m metals_pipeline.view_dashboard <run_id>     # a specific run
```

This saves the HTML under `output/downloaded/` and opens it in your browser. It works for every backend (for `local` it opens `output/site/latest/index.html`). The dashboard holds its data inside the file, so it doesn't query S3 once it's open. Each run writes a new one.

To list what a run wrote: `aws s3 ls s3://<yourbucket>/ --recursive`.

### Azure Blob Storage mode

One-time setup (resource names are examples; storage account names must be globally unique, 3 to 24 lowercase letters and digits):

```powershell
az login
az group create --name metals-rg --location eastus
az storage account create --name <youraccount> --resource-group metals-rg --location eastus --sku Standard_LRS --allow-blob-public-access false
az storage container create --account-name <youraccount> --name metals-pipeline --auth-mode login

# Let your own login write blobs (Azure requires a data-plane role even for subscription owners)
az role assignment create --role "Storage Blob Data Contributor" --assignee <your-login-email> `
  --scope $(az storage account show --name <youraccount> --resource-group metals-rg --query id -o tsv)
```

Then set these values in `.env` and rerun the same `adk run` command:

```
STORAGE_BACKEND=azure
AZURE_STORAGE_ACCOUNT=<youraccount>
AZURE_STORAGE_CONTAINER=metals-pipeline
```

Authentication goes through `DefaultAzureCredential`, which uses your `az login` session, so no key is stored anywhere. If you can't assign roles, set `AZURE_STORAGE_CONNECTION_STRING` in `.env` instead (Portal > Storage account > Access keys). `.env` is gitignored. A new role assignment can take a few minutes to take effect.



### Watch the graph in the ADK web UI

```powershell
adk web          # run from the repo root, then pick "metals_pipeline"
```

### If Gemini returns `503 UNAVAILABLE`

The default model is `gemini-flash-lite-latest`, because on the free tier `gemini-flash-latest` is often overloaded and returns `503 high demand`. The agents also retry 429 and 5xx errors with exponential backoff, up to six attempts. To try another model, set `ADK_MODEL` in `.env`.

## Outputs

The same layout is used in the S3 bucket, the Azure container, the GCS bucket, and local `output/`:

```text
raw/fred/{series_id}/ingest_date={YYYY-MM-DD}/{run_id}.json     raw FRED response + request metadata
processed/metals_ppi/run_id={run_id}/monthly.csv                series x month: index, rebased_100, mom_pct, yoy_pct, vol_12m
processed/metals_ppi/run_id={run_id}/annual.csv                 series x year: avg/min/max index, avg YoY, months (partial-year flag)
processed/metals_ppi/run_id={run_id}/latest.csv                 series: latest values, 5-year high/low, YoY rank
site/run_id={run_id}/index.html                                 dashboard for this run
site/latest/index.html                                          most recent dashboard
runs/{run_id}/manifest.json                                     plan, row/null counts, checks, paths, timings, versions
```

The dashboard is one self-contained HTML file with its data embedded. It loads Chart.js from a pinned CDN version and shows the AI commentary, a rebased line chart, a sorted YoY bar chart, a summary table, and a footer listing object paths, checks passed, and the ADK version.

## Tests

```powershell
pytest
```

The tests use no network and no LLM. They run on trimmed fixtures in `tests/fixtures/`, whose values follow simple formulas so the expected numbers can be checked by hand.

| File | Covers |
| --- | --- |
| `test_transform.py` | MoM / YoY / rebased values for hand-checked months, `.` values counted as nulls, partial-year flag, latest table |
| `test_quality.py` | Gate fails on duplicate dates, gaps, non-positive values, zero or too few rows, stale data, high null share, extreme YoY |
| `test_validate_plan.py` | Allowlist rejection, start-date clamping, base-month fixes |
| `test_storage_local.py` | `LocalStore` writes and reads back the exact contract paths, rejects path escapes |
| `test_storage_s3.py` | `S3Store` with a mocked boto3 client: keys, content types, 404 handling, config errors |
| `test_storage_azure.py` | `AzureBlobStore` with a mocked container client, plus config errors |
| `test_storage_gcs.py` | `GCSStore` with a mocked client |
| `test_pipeline_offline.py` | Node chain end to end, covering the pass branch (all files written) and a forced failure (fail route, manifest with reason) |

## Quality checks (run on live data)

Every series must have at least 24 rows, unique month-start dates with no gaps, positive values, a latest month no older than 90 days, a null share under 5%, a latest YoY within ±100%, and a value at the base month.

## Design decisions

- **Graph, not one script.** The ADK `Workflow` makes the step order, the branch on data quality, and the LLM steps explicit, and `adk web` shows each node's trace.
- **Deterministic numbers, LLM language.** All math lives in pure functions (`transform.py`, `quality.py`) that are unit-tested. LLMs do only what language helps with: reading the request and explaining results. The analyst receives the same rounded figures the table shows and is told to quote only those.
- **Small data between nodes.** Nodes pass Pydantic models holding paths and summaries, never DataFrames. Large data lives in storage, and `run_id` lives in session state.
- **Swappable storage.** `storage.py` defines one interface with `LocalStore`, `S3Store`, `AzureBlobStore`, and `GCSStore`, all using the same object paths. Switching is one env var: `STORAGE_BACKEND=local|s3|azure|gcs`.
- **No secrets in the repo.** Keys come from `.env` (gitignored). S3 uses boto3's credential chain, Azure uses `az login` through `DefaultAzureCredential`, and GCS uses ADC. FRED error messages never echo request URLs, which contain the key.

## Known limits

- Runs only on demand (`adk run`). There is no scheduler.
- If one FRED call fails after retries, the whole run fails, with no partial publish.
- Quality thresholds are constants in `config.py`.
- BLS revises recent months. Each run re-pulls full history but does not track revisions.
- The analyst's figures are constrained by the prompt but not yet verified automatically.

## Repository layout

```text
metals_pipeline/      ADK agent package (what `adk run` points at)
  agent.py            root_agent = Workflow(...)
  agents.py           planner and analyst
  nodes.py            function nodes
  transform.py        pure pandas metrics
  quality.py          pure data checks
  report.py           HTML rendering
  storage.py          LocalStore, S3Store, AzureBlobStore, GCSStore
  view_dashboard.py   download + open the latest dashboard from any backend
  fred.py             FRED API client
  schemas.py          Pydantic models passed between nodes
  config.py           allowlist, defaults, thresholds, paths
  templates/report.html.j2
tests/                pytest suite + fixtures
```

## Future improvements

See the Future Improvements document submitted with this assignment.
