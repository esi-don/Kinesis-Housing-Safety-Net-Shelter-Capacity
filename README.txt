POD 2 HOUSING SAFETY NET FORECASTING

This is a runnable research prototype for the Kinesis IQ Housing Intelligence
team project. It downloads real City of Toronto open data, loads a provisional
events schema, trains shelter and waiting-list models, and produces reproducible
evaluation evidence. It is not yet the completed Pod 2 submission or an
operational warning service. Read artifacts/research_report.html for results.

QUICK START

Use Python 3.12. From this project directory, on Windows:

  py -3.12 -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
  .venv\Scripts\python -m unittest discover -s tests -v
  .venv\Scripts\python -m pod2 all --as-of 2026-11-11
  .venv\Scripts\python -m pod2.verify

Replace --as-of with the current date for later runs. It is an explicit upper
bound on valid shelter observation dates, not a way to reconstruct an old
publication vintage. The default date uses Africa/Kampala, the user's timezone.
No OpenAI API key, cloud database or paid service is needed for this prototype.

On macOS/Linux, use python3.12 to create the environment and .venv/bin/python
for subsequent commands. Python's tzdata package supplies Toronto daylight-saving
rules on Windows. The pipeline uses urllib with normal TLS verification.

RUN INDIVIDUAL STAGES

  python -m pod2 download
  python -m pod2 etl --as-of 2026-11-11
  python -m pod2 train
  python -m pod2 report

The report command also draws four charts from existing observations and saved
model outputs. To regenerate charts only, without downloading or retraining:

  python -m pod2.charts

Matplotlib is recommended for cross-platform plotting and SVG exports; install
the updated requirements.txt to use it. The Windows fallback needs no additional download.
PNG images are in artifacts/charts, with editable SVG exports when Matplotlib
is available. Windows can use its built-in chart engine when Matplotlib is not
installed. chart_manifest.json records input hashes and the renderer. Read the chart captions:
the forecast bands are empirical and the outputs remain research-only.

To reuse the local raw snapshots without network access:

  python -m pod2 all --offline --as-of 2026-11-11

To select a specific snapshot, pass --manifest data/raw/<snapshot>/manifest.json
to etl or to all --offline. Every source checksum is verified before loading.
To change the illustrative threshold: python -m pod2 train --threshold 0.97
Then run report again. Threshold choice is a team/planning decision, and changing
it does not establish the clinical or operational utility of an alert.

Download is about 86 MB for the initial six years plus quarterly sources.
Keep adequate disk space for raw snapshots, the events database and trained
models. Each download creates a new immutable timestamped snapshot; it does not
overwrite history. Re-running all downloads another full snapshot. Use --offline
when repeating model experiments. No cleanup runs automatically.

PROJECT LAYOUT

  pod2/sources.py   CKAN discovery, raw files, provenance and SHA256 checksums
  pod2/etl.py       cleaning, calendar normalization, event IDs and SQLite load
  pod2/models.py    direct shelter models, backlog forecasts and clustering
  pod2/report.py    evidence tables, limitations and pipeline reports
  pod2/charts.py    history, backtests, uncertainty and model comparison figures
  tests/           date, missingness, duplicate, database and leakage checks
  docs/            modelling contract and remaining implementation work
  data/raw/        immutable downloaded source files and publisher metadata
  data/processed/  model-ready tables with explicit row grain
  data/events.sqlite  local provisional KinesisIQ events table
  artifacts/       forecasts, backtests, scores, models and research report

WHAT HAS BEEN IMPLEMENTED

1. Download current and 2021-2025 shelter resources, waiting-list activity and
   active housing stock from the official Toronto CKAN API. Duplicate cached
   formats are not downloaded. Metadata, exact resource IDs, URLs and hashes
   are retained.
2. Preserve missing and suppressed numbers. Parse short historical dates year
   first. Separate occupied beds from occupied rooms and use actual capacity.
   Explicitly flag missing location IDs. Collapse identical natural-key
   duplicates and fail on conflicting observations.
3. Load 13 template fields plus database-assigned ingested_at into SQLite. IDs
   do not depend on occupancy counts. Loads update changed facts without
   duplicating rows, preserve original ingestion time and prune obsolete keys
   for each loaded source to reflect the selected complete snapshot.
4. Forecast citywide sector occupancy counts 7, 14 and 30 days after the most
   recent observed date. Compare persistence, target-weekday repetition,
   gradient boosting and a 50:50 boosting/persistence ensemble. Select by
   earlier development MAE, then evaluate once on a later holdout window.
5. Forecast citywide backlog 1, 2 and 4 quarters after the most recent published
   quarter. Compare persistence and four-quarter drift, plus regularized
   regression when the training sample is adequate. These forecasts do not
   infer waiting times or a neighbourhood distribution.
6. Cluster comparable shelter program profiles separately for beds and rooms.
   Export silhouette scores and stability across two random seeds. These are
   exploratory operational clusters, not demographic vulnerability clusters.
7. Generate empirical uncertainty bands, provisional threshold flags, scores,
   real source-to-event examples, abstract measures, pipeline reports and a
   readable HTML research report. Persist estimators and run metadata.

OPEN FIRST

All forecasts are research-only. Inspect validation_status and the baseline
reference beside each forecast. The initial run exposed poor backlog holdout
performance and several shelter bands with inadequate observed coverage.
The report preserves those failures; it does not claim a proven warning system.

  artifacts/research_report.html
  artifacts/shelter_forecasts.csv
  artifacts/backlog_forecasts.csv
  artifacts/shelter_metrics.csv
  artifacts/backlog_metrics.csv
  artifacts/data_quality.json
  artifacts/run_manifest.json
  artifacts/artifact_verification.json

Pipeline reports use the six sections of the supplied template, as plain text.
They are working evidence, not final filled Word submissions. Team member names
and source-specific interpretation still need review. Full payload examples are
in mapped_samples.json. The formal feature and evaluation contract is in docs.

SOURCE AND REPRODUCIBILITY NOTES

Raw snapshots and SQLite data are retained in the local project but excluded
from Git. The source-and-results ZIP also excludes raw data and the database;
extracting that ZIP requires running download/etl before a full offline run.
Original downloaded snapshots remain available in the local data/raw folder.
Use the local snapshot for an exact rerun; a later download may contain revisions.
The source license is the City of Toronto Open Government Licence. Publisher
metadata is preserved beside the raw files. Never add private LTB documents or
unredacted personal data to this repository.

Read model files only when they come from a trusted run of this project: joblib
uses pickle internally. All report data are public aggregate observations.

REMAINING REQUIREMENTS

The official events schema, sanitized eviction signals, ward/neighbourhood
boundary enrichment, historical waiting-time targets and forthcoming RGI unit
delivery data are not supplied or implemented here. See docs/next_steps.txt for
concrete integration work. Model quality and warning usefulness must be judged
from the actual holdout results; a fitted model is not automatically useful.

TEAM WORKFLOW

Use this independent repository, not the production KinesisIQ repository.
Create a branch for each owned data stream. Commit code and reviewed
documentation; keep raw snapshots and local databases out of Git. A teammate
should be able to create a clean environment, run the tests, download the public
data and regenerate all outputs. Before submission, explain why each selected
model was retained, inspect its failure cases, and document remaining gaps.
