"""Validated source normalization and provisional KinesisIQ event mapping."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .sources import load_manifest

def number(values):
    return pd.to_numeric(values.astype(str).str.strip().str.replace(",", "", regex=False), errors="coerce")

def shelter_dates(values):
    text = values.astype(str).str.strip()
    short = text.str.fullmatch(r"\d{2}-\d{2}-\d{2}")
    result = pd.to_datetime(text.where(~short), format="mixed", errors="coerce")
    result.loc[short] = pd.to_datetime(text[short], format="%y-%m-%d", errors="coerce")
    return result.dt.normalize()

def quarter_dates(values):
    parts = values.astype(str).str.extract(r"^Q([1-4])\s+(\d{4})$")
    return pd.to_datetime(dict(year=pd.to_numeric(parts[1]),
        month=(pd.to_numeric(parts[0])-1)*3+1, day=1), errors="coerce")

def stable_id(source, key):
    return hashlib.sha256(json.dumps([source, *map(str, key)], separators=(",", ":")).encode()).hexdigest()

@lru_cache(maxsize=16384)
def utc_time(date):
    return pd.Timestamp(date).tz_localize("America/Toronto").tz_convert("UTC").isoformat()

def clean_json(obj):
    if isinstance(obj, dict):
        return {k: clean_json(v) for k,v in obj.items()}
    if isinstance(obj, (list,tuple)):
        return [clean_json(v) for v in obj]
    if obj is None or (isinstance(obj, (float,np.floating)) and not np.isfinite(obj)):
        return None
    if isinstance(obj, np.generic):
        return obj.item()
    return obj

def unique_rows(frame, key, facts):
    """Identical duplicate facts are safe; conflicting facts require human resolution."""
    conflicts = frame.groupby(key, dropna=False)[facts].nunique(dropna=False).gt(1).any(axis=1)
    if conflicts.any():
        raise ValueError(f"Conflicting observations for natural keys: {conflicts[conflicts].index[:5].tolist()}")
    return frame.drop_duplicates(key, keep="last")

def read_sources(root, manifest, name):
    frames = []
    for item in manifest["sources"]:
        if item["pipeline"] == name:
            frame = pd.read_csv(root/item["path"], dtype=str, keep_default_na=False)
            frame["resourceid"] = item["resource_id"]
            frames.append(frame)
    if not frames:
        raise ValueError(f"Missing source {name}")
    return pd.concat(frames, ignore_index=True)

def normalize_shelter(raw, as_of):
    required = ["OCCUPANCY_DATE", "PROGRAM_ID", "LOCATION_ID", "CAPACITY_TYPE", "SECTOR",
                "OCCUPIED_BEDS", "OCCUPIED_ROOMS", "CAPACITY_ACTUAL_BED", "CAPACITY_ACTUAL_ROOM"]
    missing = set(required)-set(raw)
    if missing:
        raise ValueError(f"Shelter schema changed: missing {missing}")
    frame = pd.DataFrame({"date": shelter_dates(raw.OCCUPANCY_DATE),
        "program": raw.PROGRAM_ID.str.strip(), "location": raw.LOCATION_ID.str.strip(),
        "sector": raw.SECTOR.str.strip(), "unit": raw.CAPACITY_TYPE.map(
            {"Bed Based Capacity": "beds", "Room Based Capacity": "rooms"}),
        "resourceid": raw.resourceid, "sourcerow": raw._id,
        "address": raw.get("LOCATION_ADDRESS", pd.Series("", index=raw.index)),
        "programname": raw.get("PROGRAM_NAME", pd.Series("", index=raw.index))})
    bed = frame.unit.eq("beds")
    frame["occupied"] = number(raw.OCCUPIED_BEDS.where(bed, raw.OCCUPIED_ROOMS))
    frame["capacity"] = number(raw.CAPACITY_ACTUAL_BED.where(bed, raw.CAPACITY_ACTUAL_ROOM))
    frame["locationmissing"] = frame.location.eq("")
    frame.loc[frame.locationmissing,"location"] = "unknown"
    invalid_key = frame.date.isna() | frame.unit.isna() | frame.program.eq("") | frame.sector.eq("")
    if invalid_key.any():
        raise ValueError(f"Invalid shelter dates, keys or capacity types: {int(invalid_key.sum())}")
    future = frame.date.gt(pd.Timestamp(as_of))
    if future.any():
        raise ValueError("Shelter snapshot contains future observations relative to --as-of")
    before = len(frame)
    frame = unique_rows(frame, ["date", "program", "location", "unit"],
                        ["sector", "occupied", "capacity"])
    frame["valid"] = frame.occupied.notna() & frame.capacity.gt(0) & frame.occupied.ge(0)
    frame["overcapacity"] = frame.occupied.gt(frame.capacity) & frame.valid
    frame["occupancyrate"] = (frame.occupied/frame.capacity).where(frame.valid)
    quality = {"source_rows": len(raw), "clean_rows": len(frame), "identical_duplicates": before-len(frame),
        "invalid_numeric_rows": int((~frame.valid).sum()), "above_capacity_rows": int(frame.overcapacity.sum()),
        "missing_location_id_rows": int(frame.locationmissing.sum()),
        "first_date": str(frame.date.min().date()), "last_date": str(frame.date.max().date())}
    return frame, quality

def aggregate_shelter(frame):
    groups = []
    for key, group in frame.groupby(["date", "sector", "unit"]):
        valid = group[group.valid]
        # A partial count must never masquerade as the complete sector total.
        complete = len(valid) == len(group)
        occupied = valid.occupied.sum() if complete else np.nan
        capacity = valid.capacity.sum() if complete else np.nan
        groups.append({"date": key[0], "sector": key[1], "unit": key[2],
            "occupied": occupied, "capacity": capacity,
            "occupancyrate": occupied/capacity if complete and capacity > 0 else np.nan,
            "programs": len(group), "missing_count": len(group)-len(valid)})
    return pd.DataFrame(groups).sort_values(["sector", "unit", "date"])

def normalize_quarterly(raw, name):
    mapping = ({"Total active waiting list": "backlog", "New/reactivated": "newreactivated", "Housed": "housed"}
        if name == "waitlist" else {"Subsidized Housing Units": "subsidizedunits", "Affordable Housing Units": "affordableunits"})
    period = "Quarter/Year" if name == "waitlist" else "Quarter"
    if not set([period, *mapping]).issubset(raw):
        raise ValueError(f"{name} schema changed; inspect columns")
    frame = pd.DataFrame({"date": quarter_dates(raw[period]), "quarter": raw[period],
        "resourceid": raw.resourceid, "sourcerow": raw._id})
    for col in raw:
        if col not in [period, "resourceid", "_id"]:
            target = mapping.get(col, re.sub(r"[^a-z0-9]", "", col.lower()))
            if target in frame:
                raise ValueError(f"Payload field collision: {col}")
            frame[target] = number(raw[col])
    if frame.date.isna().any():
        raise ValueError(f"Invalid {name} quarter")
    facts = [c for c in frame if c not in ["date", "quarter", "resourceid", "sourcerow"]]
    frame = unique_rows(frame, ["date"], facts).sort_values("date")
    if (frame[facts] < 0).any().any():
        raise ValueError(f"Negative counts in {name}")
    return frame

def event(source, date, key, payload, snapshot, site="Toronto", zone=None):
    return {"event_id": stable_id(source, key), "source": source,
        "anon_id": stable_id("public", [site]), "site_id": site, "zone": zone,
        "event_time": utc_time(date), "modality": source, "text_input": None,
        "payload": clean_json({**payload, "availableat": snapshot, "availabilitybasis": "snapshot_download"}),
        "consent_method": "public_data", "consent_scope": ["analytics", "research"],
        "privacy_token": "public", "geo_json": None}

def shelter_events(frame, downloaded_at):
    for row in frame.to_dict("records"):
        date = row.pop("date")
        key = [date.date(), row["program"], row["location"], row["unit"]]
        site = "location:"+row["location"] if row["location"] != "unknown" else "program:"+row["program"]
        yield event("toronto_shelter", date, key, row, downloaded_at, site, row.pop("address") or None)

def quarterly_events(frame, name, downloaded_at):
    for row in frame.to_dict("records"):
        date = row.pop("date")
        row["periodend"] = str((date + pd.offsets.QuarterEnd()).date())
        yield event("toronto_"+name, date, [date.date()], row, downloaded_at)

SCHEMA = """CREATE TABLE IF NOT EXISTS events (
event_id TEXT PRIMARY KEY, source TEXT NOT NULL, anon_id TEXT NOT NULL,
site_id TEXT NOT NULL, zone TEXT, event_time TEXT NOT NULL, modality TEXT NOT NULL,
text_input TEXT, payload TEXT, consent_method TEXT NOT NULL,
consent_scope TEXT NOT NULL, privacy_token TEXT NOT NULL, geo_json TEXT,
ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE INDEX IF NOT EXISTS events_source_time ON events(source,event_time);"""

def load_events(path, events):
    """Upsert facts and prune obsolete keys to mirror the selected complete snapshot."""
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        connection.execute("CREATE TEMP TABLE current_ids (id TEXT PRIMARY KEY)")
        columns = ["event_id", "source", "anon_id", "site_id", "zone", "event_time", "modality",
                   "text_input", "payload", "consent_method", "consent_scope", "privacy_token", "geo_json"]
        sql = f"INSERT INTO events ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT(event_id) DO UPDATE SET "
        sql += ",".join(f"{c}=excluded.{c}" for c in columns if c != "event_id")
        sql += " WHERE " + " OR ".join(f"events.{c} IS NOT excluded.{c}" for c in columns if c != "event_id")
        sources, count = set(), 0
        batch = []
        for item in events:
            sources.add(item["source"])
            values = [json.dumps(item[c], allow_nan=False) if c in ["payload", "consent_scope", "geo_json"]
                      and item[c] is not None else item[c] for c in columns]
            batch.append(values)
            if len(batch) >= 1000:
                connection.executemany(sql, batch)
                connection.executemany("INSERT INTO current_ids VALUES (?)", [(row[0],) for row in batch])
                batch.clear()
            count += 1
        if batch:
            connection.executemany(sql, batch)
            connection.executemany("INSERT INTO current_ids VALUES (?)", [(row[0],) for row in batch])
        for source in sources:
            connection.execute("DELETE FROM events WHERE source=? AND event_id NOT IN (SELECT id FROM current_ids)", (source,))
    return count

def run(root, as_of, manifest_path=None):
    import itertools
    root = Path(root)
    manifest = load_manifest(root, manifest_path)
    clean = root/"data/processed"
    reports = root/"artifacts"
    clean.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    raw = {name: read_sources(root, manifest, name) for name in ["shelter", "waitlist", "supply"]}
    shelter, quality = normalize_shelter(raw["shelter"], as_of)
    waitlist = normalize_quarterly(raw["waitlist"], "waitlist")
    supply = normalize_quarterly(raw["supply"], "supply")
    frames = {"shelter_programs": shelter, "shelter_daily": aggregate_shelter(shelter), "waitlist": waitlist, "supply": supply}
    for name, frame in frames.items():
        frame.to_csv(clean/f"{name}.csv", index=False)
    generators = lambda: itertools.chain(shelter_events(shelter, manifest["downloaded_at"]),
        quarterly_events(waitlist, "waitlist", manifest["downloaded_at"]),
        quarterly_events(supply, "supply", manifest["downloaded_at"]))
    count = load_events(root/"data/events.sqlite", generators())
    (reports/"events_schema.sql").write_text(SCHEMA, encoding="utf-8")
    quality.update({"event_count": count, "waitlist_quarters": len(waitlist),
        "published_backlog_quarters": int(waitlist.backlog.notna().sum()),
        "missing_backlog_quarters": int(waitlist.backlog.isna().sum()),
        "supply_quarters": len(supply), "schema_status": "provisional_pending_official_schema",
        "as_of": as_of, "manifest": manifest, "stock_is_not_future_supply": True})
    (reports/"data_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    samples = []
    for item in manifest["sources"]:
        name = item["pipeline"]
        frame = {"shelter": shelter,"waitlist":waitlist,"supply":supply}[name]
        sample_frame = frame[frame.resourceid.eq(item["resource_id"])].head(1)
        generator = shelter_events(sample_frame, manifest["downloaded_at"]) if name == "shelter" else quarterly_events(sample_frame,name,manifest["downloaded_at"])
        mapped = next(generator)
        resource = mapped["payload"]["resourceid"]
        source_row = raw[name][raw[name].resourceid.eq(resource) & raw[name]._id.eq(mapped["payload"]["sourcerow"])].iloc[0]
        samples.append({"pipeline": name, "source_row": source_row.drop("resourceid").to_dict(), "mapped_event": mapped})
    (reports/"mapped_samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print(f"ETL loaded {count:,} events; shelter dates {quality['first_date']} to {quality['last_date']}", flush=True)
    return quality
