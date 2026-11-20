"""Portable research report, pipeline documentation and reproducibility metadata."""
import html
import json
from pathlib import Path

import pandas as pd

LIMITATIONS = [
    "No forecast is approved for operational use. validation_status flags whether the development-selected model loses to persistence on the final holdout or its empirical bands cover fewer than 90% of held-out outcomes. The holdout is not reused to tune or reselect a better model.",
    "This is a research prototype. Shelter alerts use an illustrative 95% occupancy threshold that the team must approve.",
    "Shelter forecasts are citywide sector totals, separated into beds and rooms. They are not ward-level forecasts or estimates of unmet demand.",
    "Future capacity is held unchanged at the latest observed value. Openings, closures and policy changes can invalidate that assumption.",
    "Backtests use today's downloaded, potentially revised historical snapshot. Historical publication dates and data vintages are unavailable; the evaluation is not a fully reconstructed real-time test.",
    "The shelter holdout uses a fixed fitted model with daily observed inputs. Waiting-list holdout evaluation refits using only labels observable at each quarterly origin, with the selected model family fixed.",
    "Intervals use the 90th percentile of absolute development errors. They are empirical uncertainty bands, not guaranteed 90% prediction intervals. Inspect holdout coverage before use.",
    "Only 30 waiting-list quarters are published. Development and holdout samples are particularly small for four-quarter forecasts. Reporting changes and the large 2019 discontinuity require team review.",
    "The waiting-list source contains no waiting-time target. No individual or population waiting-time prediction is produced.",
    "The housing-units dataset measures existing stock, not available allocations or forthcoming RGI completions. It is ingested for audit but is not used as a future-supply predictor.",
    "LTB ingestion, OCR, NER redaction and eviction features are not implemented. A sanitized shared feed or a separately reviewed extraction pipeline is required.",
    "Official KinesisIQ schema validation, municipal boundary mapping and census-based spatial clustering remain integration tasks. Current event mappings follow the supplied template provisionally.",
    "A missing location ID receives an explicit unknown location and program-based site ID. No ward is inferred. Missing numeric facts remain null, and incomplete sector totals are excluded from modelling.",
    "A missing program row cannot establish a closure versus a reporting omission. Changes in the programs reported can change sector totals.",
    "Clustering describes recent operational profiles only. Seed stability is measured; temporal stability and demographic interpretation are not established.",
    "The alert evaluation compares point forecasts with realized target capacity. Live alerts instead assume unchanged current capacity; precision and recall are not evidence of a calibrated crossing probability.",
]

MEASURES = [
    ("occupancyrate", "toronto_shelter", "occupied / capacity for compatible units; capacity > 0", "ratio", "null for invalid denominator or occupancy"),
    ("sectoroccupancy", "toronto_shelter", "sum occupied / sum actual capacity within date, sector, unit", "ratio", "null if any reported program has invalid numeric facts"),
    ("backlogvelocity", "toronto_waitlist", "backlog this quarter minus backlog previous quarter", "applications per quarter", "null when either quarter is missing"),
    ("placementrate", "toronto_waitlist", "housed this quarter / backlog previous quarter", "ratio per quarter", "null for missing or zero prior backlog"),
    ("unexplainednetchange", "toronto_waitlist", "backlog change - newreactivated + housed", "applications", "diagnostic residual; not a measured removals count"),
    ("pressureflag", "toronto_shelter", "occupancyrate >= configured threshold", "boolean", "undefined for missing rate; provisional threshold"),
]

def table(frame):
    return frame.to_html(index=False, border=0, classes="data", float_format=lambda n: f"{n:,.2f}", na_rep="Unavailable")

def run(root):
    root = Path(root)
    out = root/"artifacts"
    quality = json.loads((out/"data_quality.json").read_text(encoding="utf-8"))
    from .charts import run as create_charts
    charts = create_charts(root)
    chart_gallery = ''.join('<figure><h3>'+html.escape(chart['title'])+'</h3><img src="'+
        html.escape(chart['png'])+'" alt="'+html.escape(chart['title'])+'" loading="lazy">'+
        '<figcaption>'+html.escape(chart['explanation'])+' <a href="'+html.escape(chart.get('svg',chart['png']))+
        '">Download chart</a></figcaption></figure>' for chart in charts)
    shelter = pd.read_csv(out/"shelter_forecasts.csv")
    backlog = pd.read_csv(out/"backlog_forecasts.csv")
    metrics = pd.read_csv(out/"shelter_metrics.csv")
    backlog_metrics = pd.read_csv(out/"backlog_metrics.csv")
    selected = pd.DataFrame(json.loads((out/"shelter_selection.json").read_text()))
    chosen = selected[selected.status.eq("trained")][["sector","unit","horizon","selected"]]
    holdout = metrics[metrics.stage.eq("holdout")].merge(chosen,on=["sector","unit","horizon"])
    holdout = holdout[holdout.model.eq(holdout.selected)]
    measures = pd.DataFrame(MEASURES,columns=["measure_key","modality","formula","units","missing_policy"])
    measures.to_csv(out/"measure_dictionary.csv",index=False)
    waitlist = pd.read_csv(root/"data/processed/waitlist.csv",parse_dates=["date"]).set_index("date").asfreq("QS")
    diagnostics = pd.DataFrame({"backlog":waitlist.backlog,"backlogvelocity":waitlist.backlog.diff(),
        "placementrate":waitlist.housed/waitlist.backlog.shift(1),
        "unexplainednetchange":waitlist.backlog.diff()-waitlist.newreactivated+waitlist.housed})
    diagnostics.to_csv(out/"backlog_measures.csv",index_label="date")
    links = "".join(f'<li><a href="{html.escape(name)}">{html.escape(label)}</a></li>' for name,label in [
        ("shelter_forecasts.csv","Shelter forecasts"),("shelter_metrics.csv","All shelter model comparisons"),
        ("shelter_backtest.csv","Selected shelter holdout predictions"),("backlog_forecasts.csv","Backlog forecasts"),
        ("backlog_metrics.csv","Backlog model comparisons"),("program_clusters.csv","Exploratory program profiles"),
        ("data_quality.json","Data provenance and quality"),("mapped_samples.json","Real source rows and events"),
        ("measure_dictionary.csv","Abstract measures"),("run_manifest.json","Run reproducibility record")])
    source_rows = [{k:s[k] for k in ["pipeline","resource_name","resource_id","sha256"]} for s in quality["manifest"]["sources"]]
    sections = [
        ("What this version delivers", f'<p>Downloaded {len(source_rows)} official Toronto resources and loaded {quality["event_count"]:,} provisional events. Shelter history runs from {quality["first_date"]} to {quality["last_date"]}. Published waiting-list history contains {quality["published_backlog_quarters"]} quarters.</p><p>Targets are occupancy counts by citywide sector and capacity unit at 7, 14 and 30 days after the latest observation, and active waiting-list counts at 1, 2 and 4 quarters. These are planning forecasts, not proven operational warnings.</p>'),
        ("Validation findings", '<p><strong>These outputs are research-only.</strong> '+
            f'{int(shelter.validation_status.ne("passes_initial_checks").sum())} of {len(shelter)} shelter forecasts fail at least one initial validation check. '+
            f'{int(backlog.validation_status.eq("fails_baseline").sum())} of {len(backlog)} backlog forecasts lose to persistence on the final holdout. '+
            'Baseline reference forecasts and holdout comparisons are exported beside each candidate forecast. Initial checks assess baseline MAE and observed interval coverage; passing them does not establish operational readiness.</p>'),
        ("Visual explanations", '<p>Read the history first, then compare predictions with actual outcomes and simple baselines. Charts use the saved data snapshot; models have not been retrained for this visualization update.</p>'+chart_gallery),
        ("Shelter forecasts", table(shelter[["sector","unit","horizon_days","data_cutoff","target_date","predicted","baseline_forecast","lower90","upper90","capacity_assumption","pressure_scenario","validation_status","model"]])),
        ("Shelter final holdout results", '<p>Model selection uses three earlier 60-day rolling validation windows. Training labels end on or before each cutoff. The final 90-day origin window is reserved for assessment. Counts of evaluated origins vary by horizon and missing data. Baseline comparisons remain in the downloadable metrics file.</p>'+table(holdout[["sector","unit","horizon","model","n","mae","rmse","bias","interval_coverage","precision","recall"]])),
        ("Waiting-list forecasts", '<p>The forecast clock starts at the latest published quarter, not at today. Some target periods may already be in progress or completed when the report is read. All outputs are citywide; no neighbourhood distribution is inferred.</p>'+table(backlog)),
        ("Waiting-list evaluation", table(backlog_metrics)),
        ("Limitations and unfinished project requirements", '<ul>'+''.join('<li>'+html.escape(item)+'</li>' for item in LIMITATIONS)+'</ul>'),
        ("Measures", table(measures)),
        ("Source inventory", table(pd.DataFrame(source_rows))),
        ("Downloadable evidence", '<ul>'+links+'</ul>'),
    ]
    body = ''.join(f'<section><h2>{html.escape(title)}</h2>{content}</section>' for title,content in sections)
    document = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Pod 2 housing forecast research report</title><style>
    body{font:16px/1.55 system-ui,sans-serif;color:#182b3a;background:#f7f9fc;margin:0}main{max-width:1440px;margin:auto;padding:40px 28px}h1,h2{color:#132b40;line-height:1.2}h1{font-size:36px}h2{margin-top:36px;font-size:24px}section{overflow-x:auto}table{border-collapse:collapse;background:white;width:100%;font-size:13px;margin:18px 0}th,td{border:1px solid #d5dce4;padding:10px;text-align:left;vertical-align:top}th{background:#183d58;color:white}tr:nth-child(even){background:#edf3f8}li{margin-bottom:10px}a{color:#125c93}.subtitle{font-size:18px;color:#40596e}code{overflow-wrap:anywhere}@media print{body{background:white}main{padding:0}table{font-size:9px}th{color:black;background:#ddd}}
    figure{margin:24px 0 40px}figure img{display:block;width:100%;max-width:1200px;height:auto;background:white}figcaption{max-width:1000px;color:#40596e;margin-top:10px}h3{font-size:19px}
    </style></head><body><main><h1>Pod 2 housing forecast research report</h1><p class="subtitle">Working prototype with chronological evaluation and reproducible source snapshots</p>'''+body+"</main></body></html>"
    (out/"research_report.html").write_text(document,encoding="utf-8")
    for name in ["shelter","waitlist","supply"]:
        write_pipeline_report(root,name,quality)
    return out/"research_report.html"

def write_pipeline_report(root,name,quality):
    inventory = [s for s in quality["manifest"]["sources"] if s["pipeline"] == name]
    samples = [s for s in json.loads((root/"artifacts/mapped_samples.json").read_text()) if s["pipeline"] == name]
    texts = {
        "shelter": ("Daily shelter occupancy and actual capacity", "One source observation describes a program at a location on a date, measured in beds or rooms. This pipeline supports citywide sector forecasting. It does not measure unmet demand or users' previous neighbourhoods.", "Whitespace and numeric text are normalized. Historical YY-MM-DD dates are parsed year first; modern ISO dates are parsed separately. Missing location IDs use location=unknown and program-based site IDs. Missing counts remain null. Invalid occupancy or nonpositive actual capacity is retained and flagged; incomplete sector totals are excluded from training. Exact duplicates are collapsed; conflicting natural keys fail the run.", "date, program, location or explicit unknown, capacity type"),
        "waitlist": ("Centralized waiting-list activity", "One row is a citywide quarter, covering active applications, new/reactivated applications, households housed and published breakdowns. It does not provide individual waiting times or ward breakdowns.", "Qn YYYY labels become quarter starts. Commas are removed from numeric text. Blanks, suppressed and n/a counts remain null. No quarterly-to-daily expansion is used. Category fields are stored separately rather than added together; potentially overlapping breakdowns are never summed into the backlog.", "quarter start"),
        "supply": ("Existing subsidized and affordable housing stock", "One row is a citywide quarterly count of existing units under City administration. It is not a pipeline of future RGI completions and is not used as that predictor.", "Quarter labels become quarter starts. Commas are removed from numeric text. n/a and blank counts remain null. Subsidized and affordable unit counts remain separate measures.", "quarter start")}
    title,meaning,cleaning,key = texts[name]
    text = f"{title} dataset pipeline report\n\nTeam: Pod 2\nMembers: Not supplied\nPipeline command: python -m pod2 etl --as-of {quality['as_of']}\nReport status: Working prototype; integration dependencies remain\n\n1. Introduction\n{meaning}\n\n"
    text += "Source inventory and immutable snapshot hashes\n"+json.dumps(inventory,indent=2)+"\n\n"
    text += "2. Data cleaning\n"+cleaning+"\n\n3. Mapping to the events table\n"
    text += f"event_id: SHA256 of pipeline plus natural key ({key}), excluding mutable counts.\nsource and modality: toronto_{name}; constant per pipeline table.\n"
    text += "anon_id: SHA256 of the public site identifier; no person's name.\nsite_id: published shelter location ID, program ID if location missing, otherwise Toronto for citywide quarterly sources.\nzone: published shelter address, otherwise null.\nevent_time: observation date or quarter start at Toronto midnight, converted to UTC with daylight saving.\ntext_input: null.\npayload: separately named facts; see sample JSON for each resource and source-column correspondence. Includes resourceid, sourcerow, snapshot availableat and availabilitybasis. Snapshot download time is not the original historical release date.\nconsent_method: public_data.\nconsent_scope: analytics and research.\nprivacy_token: public.\ngeo_json: null pending boundary enrichment.\ningested_at: database default, preserved on updates.\nSchema is provisional pending the official KinesisIQ schema.\n\n"
    text += "Payload field definitions and examples\n"
    for sample in samples:
        payload = sample["mapped_event"]["payload"]
        text += f"Resource {payload['resourceid']}\n"+json.dumps(payload,indent=2)+"\n"
    text += "\n4. Sample mapped data\nReal source rows with publisher column names and mapped events:\n"+json.dumps(samples,indent=2)+"\n\n"
    text += "5. Measures\nSee measure_dictionary.csv for formulas, units and missing-value policies. backlog_measures.csv contains actual quarterly diagnostics. Shelter sector totals and missing_count are in data/processed/shelter_daily.csv. All statistics exclude undefined observations explicitly.\n\n"
    text += "6. Conclusion\nReady to use: checksummed snapshots, validated clean tables, duplicate-safe local events and real mapped examples.\nTreat as provisional: revised historical snapshots, source omissions, missing numeric facts and official schema compatibility. A repeat load updates changed facts and prunes removed keys for each loaded source, while retaining ingested_at for unchanged event IDs.\nNot claimed: finer geography, waiting-time accuracy, future RGI supply, eviction correlations or operational readiness.\n"
    (root/f"artifacts/{name}_pipeline_report.txt").write_text(text,encoding="utf-8")
