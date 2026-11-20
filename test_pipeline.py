import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

from pod2.etl import aggregate_shelter, event, load_events, number, shelter_dates, unique_rows, utc_time
from pod2.models import shelter_features, training_rows, backlog_at

class PipelineTests(unittest.TestCase):
    def test_historical_short_dates_are_year_first(self):
        dates = shelter_dates(pd.Series(["21-01-02", "22-12-31", "2024-05-06T00:00:00"]))
        self.assertEqual(dates.dt.strftime("%Y-%m-%d").tolist(), ["2021-01-02","2022-12-31","2024-05-06"])

    def test_missing_and_suppressed_are_not_zero(self):
        result = number(pd.Series(["", "Suppressed", "n/a", "0", "1,234"]))
        self.assertTrue(result.iloc[:3].isna().all())
        self.assertEqual(result.iloc[3:].tolist(), [0,1234])

    def test_local_midnight_uses_daylight_savings(self):
        self.assertEqual(utc_time("2026-01-01"), "2026-01-01T05:00:00+00:00")
        self.assertEqual(utc_time("2026-07-01"), "2026-07-01T04:00:00+00:00")

    def test_conflicting_duplicate_fails(self):
        with self.assertRaisesRegex(ValueError,"Conflicting"):
            unique_rows(pd.DataFrame({"key":[1,1],"fact":[2,3]}),["key"],["fact"])

    def test_partial_sector_total_is_missing(self):
        frame = pd.DataFrame({"date":[pd.Timestamp("2026-01-01")]*2,"sector":["Men"]*2,
            "unit":["beds"]*2,"occupied":[8,np.nan],"capacity":[10,10],"valid":[True,False]})
        result = aggregate_shelter(frame).iloc[0]
        self.assertTrue(pd.isna(result.occupied))
        self.assertEqual(result.missing_count,1)

    def test_upsert_updates_facts_without_duplicate_and_prunes_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder)/"events.sqlite"
            a = event("source","2026-01-01",[1],{"backlog":5},"snapshot")
            b = event("source","2026-01-01",[2],{"backlog":7},"snapshot")
            load_events(db,[a,b])
            with closing(sqlite3.connect(db)) as conn:
                original = conn.execute("SELECT ingested_at FROM events WHERE event_id=?",(a["event_id"],)).fetchone()[0]
            a["payload"]["backlog"] = 9
            load_events(db,[a])
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute("SELECT payload,ingested_at FROM events").fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(json.loads(rows[0][0])["backlog"],9)
            self.assertEqual(rows[0][1],original)

    def test_missing_day_is_not_compressed_into_lag(self):
        dates = pd.date_range("2025-01-01",periods=50).delete(40)
        frame = pd.DataFrame({"date":dates,"occupied":np.arange(49),"capacity":100})
        data, _ = shelter_features(frame,7)
        self.assertTrue(pd.isna(data.loc["2025-02-11","occupied_lag1"]))

    def test_failed_snapshot_does_not_partly_replace_existing_events(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder)/"events.sqlite"
            a = event("source","2026-01-01",[1],{"backlog":5},"snapshot")
            load_events(db,[a])
            def broken():
                for i in range(1001):
                    yield event("source","2026-01-01",[i+2],{"backlog":9},"snapshot")
                raise ValueError("interrupted snapshot")
            with self.assertRaisesRegex(ValueError,"interrupted"):
                load_events(db,broken())
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute("SELECT event_id FROM events").fetchall()
            self.assertEqual(rows,[(a["event_id"],)])

    def test_features_do_not_change_when_future_values_change(self):
        frame = pd.DataFrame({"date":pd.date_range("2025-01-01",periods=100),"occupied":np.arange(100),"capacity":100})
        a, columns = shelter_features(frame,14)
        changed = frame.copy()
        changed.loc[changed.date > "2025-03-01","occupied"] = 9999
        b, _ = shelter_features(changed,14)
        pd.testing.assert_frame_equal(a.loc[:"2025-03-01",columns],b.loc[:"2025-03-01",columns])

    def test_training_labels_are_observable_at_cutoff(self):
        frame = pd.DataFrame({"date":pd.date_range("2025-01-01",periods=100),"occupied":np.arange(100),"capacity":100})
        data,_ = shelter_features(frame,30)
        train = training_rows(data,pd.Timestamp("2025-03-01"))
        self.assertTrue(train.target_date.le(pd.Timestamp("2025-03-01")).all())
        self.assertTrue((train.index < pd.Timestamp("2025-03-01")).all())

    def test_backlog_predictions_ignore_unobserved_future(self):
        series = pd.Series(np.arange(30)*100+70000,index=pd.date_range("2019-01-01",periods=30,freq="QS"))
        a,_,_ = backlog_at(series,2,20)
        changed = series.copy()
        changed.iloc[21:] = 999999
        b,_,_ = backlog_at(changed,2,20)
        self.assertEqual(a,b)

if __name__ == "__main__":
    unittest.main()
