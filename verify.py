"""Verify the produced evidence, not just whether model fitting returned."""
import json
import hashlib
import sqlite3
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd

from .sources import load_manifest

def run(root):
    root = Path(root)
    out = root/"artifacts"
    manifest = load_manifest(root)
    quality = json.loads((out/"data_quality.json").read_text())
    with closing(sqlite3.connect(root/"data/events.sqlite")) as connection:
        count, unique = connection.execute("SELECT COUNT(*),COUNT(DISTINCT event_id) FROM events").fetchone()
        assert count == unique == quality["event_count"], "Event count or identity mismatch"
        for sample in json.loads((out/"mapped_samples.json").read_text()):
            payload = connection.execute("SELECT payload FROM events WHERE event_id=?",(sample["mapped_event"]["event_id"],)).fetchone()
            assert payload and json.loads(payload[0]) == sample["mapped_event"]["payload"], "Source example does not match database"
    samples = json.loads((out/"mapped_samples.json").read_text())
    assert {s["mapped_event"]["payload"]["resourceid"] for s in samples} == {s["resource_id"] for s in manifest["sources"]}
    shelter = pd.read_csv(out/"shelter_forecasts.csv")
    backlog = pd.read_csv(out/"backlog_forecasts.csv")
    assert set(shelter.horizon_days) == {7,14,30}
    assert set(backlog.horizon_quarters) == {1,2,4}
    assert shelter.status.eq("research_only").all()
    for frame in [shelter,backlog]:
        expected = np.where(frame.holdout_mae > frame.baseline_holdout_mae+1e-9, "fails_baseline",
            np.where(frame.holdout_interval_coverage < .9,"fails_interval_coverage","passes_initial_checks"))
        assert (frame.validation_status.to_numpy() == expected).all(), "Validation status hides a failed assessment"
    for frame,column in [(shelter,"predicted"),(backlog,"predicted_backlog")]:
        assert np.isfinite(frame[[column,"lower90","upper90"]].to_numpy()).all()
        assert frame.lower90.ge(0).all() and frame.lower90.le(frame[column]).all() and frame[column].le(frame.upper90).all()
    assert (pd.to_datetime(shelter.target_date)-pd.to_datetime(shelter.data_cutoff)).dt.days.eq(shelter.horizon_days).all()
    for filename in ["shelter_metrics.csv","backlog_metrics.csv"]:
        metrics = pd.read_csv(out/filename)
        assert set(metrics.stage) == {"development","holdout"}
        assert metrics.n.gt(0).all() and np.isfinite(metrics[["mae","rmse","bias"]].to_numpy()).all()
    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                href = dict(attrs).get("href","")
                assert (out/href).is_file(), f"Missing report evidence link: {href}"
            if tag == "img":
                src = dict(attrs).get("src","")
                assert (out/src).is_file(), f"Missing report chart: {src}"
    Links().feed((out/"research_report.html").read_text(encoding="utf-8"))
    if (out/"chart_manifest.json").exists():
        charts = json.loads((out/"chart_manifest.json").read_text())
        for name,digest in charts["source_sha256"].items():
            assert hashlib.sha256((root/name).read_bytes()).hexdigest() == digest, f"Stale chart input: {name}"
        for name,digest in charts["image_sha256"].items():
            assert hashlib.sha256((out/name).read_bytes()).hexdigest() == digest, f"Changed chart image: {name}"
    result = {"status":"passed", "checksummed_resources":len(manifest["sources"]),
        "unique_events":count,"verified_real_mapped_samples":len(samples),
        "shelter_forecasts":len(shelter),"backlog_forecasts":len(backlog),
        "checks":["source hashes","database uniqueness","sample-to-event consistency","all horizons",
            "finite metrics","ordered nonnegative uncertainty bands","forecast target dates","report evidence links"]}
    (out/"artifact_verification.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
    return result

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1])
    run(parser.parse_args().root)
