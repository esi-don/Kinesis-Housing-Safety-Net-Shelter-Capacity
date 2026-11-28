"""Direct forecasts with chronological selection, purged labels and a final holdout."""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

VERSION = "pod2-v0.1"

def scores(y, predicted):
    error = np.asarray(predicted)-np.asarray(y)
    return {"n": len(error), "mae": float(np.mean(np.abs(error))),
            "rmse": float(np.sqrt(np.mean(error**2))), "bias": float(error.mean())}

def alert_scores(y, predicted, capacity, threshold):
    actual = np.asarray(y)/np.asarray(capacity) >= threshold
    alert = np.asarray(predicted)/np.asarray(capacity) >= threshold
    tp = int((actual & alert).sum())
    return {"precision": tp/int(alert.sum()) if alert.sum() else None,
        "recall": tp/int(actual.sum()) if actual.sum() else None,
        "false_alarms": int((alert & ~actual).sum()), "actual_pressure_cases": int(actual.sum())}

def shelter_features(group, horizon):
    # Reindex before shifting: a lag of seven means calendar days, never seven available rows.
    group = group.set_index("date").sort_index().asfreq("D")
    y = group.occupied
    x = pd.DataFrame(index=group.index)
    for lag in [0, 1, 7, 14, 28]:
        x[f"occupied_lag{lag}"] = y.shift(lag)
    for window in [7, 28]:
        x[f"mean{window}"] = y.rolling(window, min_periods=window).mean()
        x[f"std{window}"] = y.rolling(window, min_periods=window).std()
    x["capacity"] = group.capacity
    x["capacity_change7"] = group.capacity.diff(7)
    target_dates = x.index+pd.Timedelta(days=horizon)
    x["target_weekday"] = target_dates.dayofweek
    x["season_sin"] = np.sin(2*np.pi*target_dates.dayofyear/365.25)
    x["season_cos"] = np.cos(2*np.pi*target_dates.dayofyear/365.25)
    data = x.copy()
    data["target"] = y.shift(-horizon)
    data["target_capacity"] = group.capacity.shift(-horizon)
    data["target_date"] = target_dates
    data["last"] = y
    data["weekday"] = y.shift((-horizon) % 7)
    return data, list(x.columns)

def training_rows(data, cutoff):
    return data[(data.index < cutoff) & data.target_date.le(cutoff) & data.target.notna()]

def fit_shelter(train, columns):
    # Predict change rather than absolute count so trees can follow a changing system size.
    model = HistGradientBoostingRegressor(max_iter=100, max_leaf_nodes=15,
        learning_rate=0.06, l2_regularization=10, min_samples_leaf=25,
        early_stopping=False, random_state=42)
    model.fit(train[columns], train.target-train["last"])
    return model

def shelter_predict(model, data, columns):
    return np.maximum(0, model.predict(data[columns])+data["last"].to_numpy())

def shelter_candidate_predictions(model, data, columns):
    ml = shelter_predict(model, data, columns)
    return {"last": data["last"].to_numpy(), "weekday": data.weekday.to_numpy(),
            "boosting": ml, "ensemble": (ml+data["last"].to_numpy())/2}

def forecast_shelter(root, threshold=0.95):
    root = Path(root)
    raw = pd.read_csv(root/"data/processed/shelter_daily.csv", parse_dates=["date"])
    metrics, predictions, forecasts, selections = [], [], [], []
    latest = raw.date.max()
    holdout_start = latest-pd.Timedelta(days=90)
    modeldir = root/"artifacts/models"
    modeldir.mkdir(parents=True, exist_ok=True)
    for (sector, unit), group in raw.groupby(["sector", "unit"]):
        if group.date.max() != latest or len(group) < 730:
            selections.append({"sector": sector, "unit": unit, "status": "insufficient_or_stale_history"})
            continue
        for horizon in [7, 14, 30]:
            data, columns = shelter_features(group, horizon)
            data = data.dropna(subset=columns+["last", "weekday"])
            dev_records = []
            for fold, offset in enumerate([270, 180, 90]):
                cutoff = holdout_start-pd.Timedelta(days=offset)
                train = training_rows(data, cutoff)
                validation = data[(data.index >= cutoff) & (data.index < cutoff+pd.Timedelta(days=60))
                                  & data.target.notna()]
                if len(train) < 365 or len(validation) < 14:
                    continue
                model = fit_shelter(train, columns)
                for candidate, estimated in shelter_candidate_predictions(model, validation, columns).items():
                    metrics.append({"task": "shelter", "sector": sector, "unit": unit, "horizon": horizon,
                        "stage": "development", "fold": fold, "model": candidate,
                        **scores(validation.target, estimated)})
                    dev_records.extend({"candidate": candidate, "error": float(y-p), "origin": date,
                        "target_date": date+pd.Timedelta(days=horizon)} for date, y, p in
                        zip(validation.index, validation.target, estimated))
            development = pd.DataFrame(dev_records)
            if development.empty:
                selections.append({"sector": sector, "unit": unit, "horizon": horizon, "status": "no_valid_folds"})
                continue
            winner = development.groupby("candidate").error.apply(lambda e: e.abs().mean()).idxmin()
            residuals = development[development.candidate.eq(winner)].error.abs()
            radius = float(np.quantile(residuals, .9, method="higher"))
            train = training_rows(data, holdout_start)
            test = data[(data.index >= holdout_start) & data.target.notna() & data.target_capacity.gt(0)]
            model = fit_shelter(train, columns)
            candidates = shelter_candidate_predictions(model, test, columns)
            selected_mae = scores(test.target, candidates[winner])["mae"]
            baseline_mae = scores(test.target, candidates["last"])["mae"]
            observed_coverage = float(np.mean(np.abs(test.target-candidates[winner]) <= radius))
            validation_status = ("fails_baseline" if selected_mae > baseline_mae+1e-9 else
                "fails_interval_coverage" if observed_coverage < .9 else "passes_initial_checks")
            for candidate, estimated in candidates.items():
                lower, upper = np.maximum(0, estimated-radius), estimated+radius
                metrics.append({"task": "shelter", "sector": sector, "unit": unit, "horizon": horizon,
                    "stage": "holdout", "fold": -1, "model": candidate, **scores(test.target, estimated),
                    # Historical capacity is used only to score the scenario alert, never as a feature.
                    **alert_scores(test.target, estimated, test.target_capacity, threshold),
                    "interval_coverage": float(np.mean((test.target >= lower) & (test.target <= upper))) if candidate == winner else None,
                    "interval_width": float(np.mean(upper-lower)) if candidate == winner else None})
                if candidate == winner:
                    predictions.extend({"sector": sector, "unit": unit, "horizon_days": horizon,
                        "origin": str(date.date()), "target_date": str((date+pd.Timedelta(days=horizon)).date()),
                        "actual": float(y), "predicted": float(p), "lower90": float(lo), "upper90": float(hi),
                        "target_capacity": float(cap), "model": winner} for date,y,p,lo,hi,cap in
                        zip(test.index,test.target,estimated,lower,upper,test.target_capacity))
            full = data[data.target.notna() & data.target_date.le(latest)]
            final_model = fit_shelter(full, columns)
            current = data[data.index == latest]
            if current.empty:
                selections.append({"sector": sector, "unit": unit, "horizon": horizon,
                    "status": "latest_features_incomplete", "selected": winner})
                continue
            predicted = float(shelter_candidate_predictions(final_model, current, columns)[winner][0])
            capacity = float(current.capacity.iloc[0])
            lower, upper = max(0, predicted-radius), predicted+radius
            alert = "pressure" if predicted/capacity >= threshold else "below_threshold"
            forecasts.append({"sector": sector, "unit": unit, "horizon_days": horizon,
                "data_cutoff": str(latest.date()), "target_date": str((latest+pd.Timedelta(days=horizon)).date()),
                "predicted": predicted, "lower90": lower, "upper90": upper,
                "capacity_assumption": capacity, "capacity_basis": "unchanged_since_data_cutoff",
                "occupancy_ratio": predicted/capacity, "threshold": threshold, "pressure_scenario": alert,
                "status": "research_only", "validation_status": validation_status,
                "baseline_forecast": float(current["last"].iloc[0]),
                "holdout_mae": selected_mae, "baseline_holdout_mae": baseline_mae,
                "holdout_interval_coverage": observed_coverage,
                "threshold_status": "provisional_team_review_required", "model": winner,
                "model_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "interval_method": "development_absolute_errors_empirical90_unverified_coverage"})
            selections.append({"sector": sector, "unit": unit, "horizon": horizon, "selected": winner,
                "development_n": len(residuals), "training_n": len(full), "holdout_n": len(test),
                "radius90": radius, "status": "trained"})
            joblib.dump({"estimator": final_model, "selected": winner, "features": columns,
                "radius90": radius, "data_cutoff": str(latest.date()), "version": VERSION},
                modeldir/f"shelter_{sector.replace(' ', '_')}_{unit}_{horizon}.joblib")
            print(f"Shelter {sector}/{unit} +{horizon}: {winner}", flush=True)
    pd.DataFrame(metrics).to_csv(root/"artifacts/shelter_metrics.csv", index=False)
    pd.DataFrame(predictions).to_csv(root/"artifacts/shelter_backtest.csv", index=False)
    pd.DataFrame(forecasts).to_csv(root/"artifacts/shelter_forecasts.csv", index=False)
    (root/"artifacts/shelter_selection.json").write_text(json.dumps(selections, indent=2), encoding="utf-8")
    return forecasts

def backlog_design(series, horizon):
    x = pd.DataFrame({f"lag{lag}": series.shift(lag) for lag in [0,1,2,3]})
    x["change"] = series.diff()
    x["trend4"] = series.diff(4)/4
    x["quarter_sin"] = np.sin(2*np.pi*(np.arange(len(x))+horizon)/4)
    x["quarter_cos"] = np.cos(2*np.pi*(np.arange(len(x))+horizon)/4)
    return x

def backlog_at(series, horizon, origin):
    x = backlog_design(series, horizon)
    y = series.shift(-horizon)-series
    valid = x.notna().all(axis=1) & y.notna()
    # Training labels must end on or before the current origin.
    train = valid & (np.arange(len(series))+horizon <= origin)
    current = x.iloc[[origin]]
    last = float(series.iloc[origin])
    drift = max(0, last + float(x.trend4.iloc[origin])*horizon)
    result = {"last": last, "drift4": drift}
    model = None
    if train.sum() >= 8:
        model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
        model.fit(x[train], y[train])
        result["ridge"] = max(0, last+float(model.predict(current)[0]))
    return result, model, list(x.columns)

def forecast_backlog(root):
    root = Path(root)
    raw = pd.read_csv(root/"data/processed/waitlist.csv", parse_dates=["date"])
    # Complete quarterly grid, including unreleased observations. No interpolation.
    series = raw.set_index("date").backlog.asfreq("QS")
    latest = series.last_valid_index()
    series = series.loc[:latest]
    holdout_start = len(series)-6
    metrics, forecasts, backtest = [], [], []
    for horizon in [1,2,4]:
        records = []
        for origin in range(12, len(series)-horizon):
            if series.iloc[max(0,origin-4):origin+1].isna().any() or pd.isna(series.iloc[origin+horizon]):
                continue
            candidates, _, _ = backlog_at(series, horizon, origin)
            for candidate, predicted in candidates.items():
                records.append({"origin_index": origin, "origin": str(series.index[origin].date()),
                    "target_date": str(series.index[origin+horizon].date()), "model": candidate,
                    "actual": float(series.iloc[origin+horizon]), "predicted": predicted,
                    # Selection outcomes must be observable before the holdout boundary.
                    "stage": "development" if origin+horizon < holdout_start else
                        "holdout" if origin >= holdout_start else "purged"})
        frame = pd.DataFrame(records)
        if frame.empty:
            continue
        development = frame[frame.stage.eq("development")]
        counts = development.groupby("model").size()
        # Compare candidates on the same origins to avoid giving short-history ML an advantage.
        eligible = counts[counts == counts.max()].index
        development = development[development.model.isin(eligible)]
        if development.empty:
            continue
        errors = development.assign(error=lambda d: (d.actual-d.predicted).abs())
        winner = errors.groupby("model").error.mean().idxmin()
        radius = float(np.quantile(errors[errors.model.eq(winner)].error, .9, method="higher"))
        for (stage,candidate), part in frame[frame.stage.ne("purged")].groupby(["stage","model"]):
            item = {"horizon_quarters": horizon, "stage": stage, "model": candidate,
                **scores(part.actual, part.predicted), "selected": candidate == winner}
            if stage == "holdout" and candidate == winner:
                item["interval_coverage"] = float(((part.actual >= (part.predicted-radius).clip(lower=0)) &
                    (part.actual <= part.predicted+radius)).mean())
            metrics.append(item)
        chosen = frame[frame.model.eq(winner) & frame.stage.eq("holdout")].copy()
        chosen["horizon_quarters"] = horizon
        chosen["lower90"] = (chosen.predicted-radius).clip(lower=0)
        chosen["upper90"] = chosen.predicted+radius
        backtest.extend(chosen.to_dict("records"))
        candidates, model, columns = backlog_at(series, horizon, len(series)-1)
        if winner not in candidates:
            continue
        predicted = candidates[winner]
        target = (latest.to_period("Q")+horizon).start_time
        test_winner = frame[frame.model.eq(winner) & frame.stage.eq("holdout")]
        test_baseline = frame[frame.model.eq("last") & frame.stage.eq("holdout")]
        selected_mae = scores(test_winner.actual,test_winner.predicted)["mae"]
        baseline_mae = scores(test_baseline.actual,test_baseline.predicted)["mae"]
        observed_coverage = float((np.abs(test_winner.actual-test_winner.predicted) <= radius).mean())
        forecasts.append({"horizon_quarters": horizon, "data_period": str(latest.to_period("Q")),
            "target_period": str(target.to_period("Q")), "predicted_backlog": predicted,
            "change_from_latest": predicted-float(series.iloc[-1]),
            "lower90": max(0,predicted-radius), "upper90": predicted+radius, "model": winner,
            "development_n": int(counts.get(winner,0)), "model_version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(), "geography": "Toronto citywide",
            "status": "research_only_small_sample_revised_history",
            "validation_status": "fails_baseline" if selected_mae > baseline_mae+1e-9 else
                "fails_interval_coverage" if observed_coverage < .9 else "passes_initial_checks",
            "baseline_forecast": float(series.iloc[-1]), "holdout_mae": selected_mae,
            "baseline_holdout_mae": baseline_mae, "holdout_interval_coverage": observed_coverage,
            "wait_time_status": "unsupported_missing_target", "future_rgi_supply_status": "missing"})
        joblib.dump({"estimator": model, "selected": winner, "features": columns,
            "radius90": radius, "version": VERSION, "data_period": str(latest.to_period("Q"))},
            root/f"artifacts/models/backlog_{horizon}.joblib")
    pd.DataFrame(metrics).to_csv(root/"artifacts/backlog_metrics.csv",index=False)
    pd.DataFrame(forecasts).to_csv(root/"artifacts/backlog_forecasts.csv",index=False)
    pd.DataFrame(backtest).to_csv(root/"artifacts/backlog_backtest.csv",index=False)
    return forecasts

def cluster_programs(root, threshold=.95):
    root = Path(root)
    raw = pd.read_csv(root/"data/processed/shelter_programs.csv", parse_dates=["date"])
    raw = raw[raw.date > raw.date.max()-pd.Timedelta(days=90)]
    raw = raw[raw.valid]
    records = []
    for (program,location,unit), group in raw.groupby(["program","location","unit"]):
        if len(group) < 60:
            continue
        group = group.sort_values("date")
        records.append({"program": program, "location": location, "unit": unit,
            "mean_occupancy": group.occupancyrate.mean(), "variability": group.occupancyrate.std(),
            "pressure_share": group.occupancyrate.ge(threshold).mean(),
            "log_capacity": math.log1p(group.capacity.median()),
            "capacity_change": (group.capacity.iloc[-1]-group.capacity.iloc[0])/max(1,group.capacity.iloc[0]),
            "observations": len(group)})
    profiles = pd.DataFrame(records)
    summaries = []
    if profiles.empty:
        return []
    # Compare rooms with rooms and beds with beds.
    for unit, group in profiles.groupby("unit"):
        if len(group) < 6:
            continue
        columns = ["mean_occupancy","variability","pressure_share","log_capacity","capacity_change"]
        scaler = StandardScaler()
        x = scaler.fit_transform(group[columns])
        k = min(3, len(group)-1)
        model = KMeans(n_clusters=k, n_init=20, random_state=42).fit(x)
        alternate = KMeans(n_clusters=k, n_init=20, random_state=7).fit_predict(x)
        profiles.loc[group.index,"cluster"] = model.labels_
        summaries.append({"unit": unit, "programs": len(group), "clusters": k,
            "silhouette": float(silhouette_score(x, model.labels_)),
            "seed_stability_ari": float(adjusted_rand_score(model.labels_,alternate)),
            "interpretation": "exploratory recent operational profiles; not spatial or demographic vulnerability",
            "temporal_stability": "not_evaluated"})
        joblib.dump({"scaler": scaler, "model": model, "features": columns}, root/f"artifacts/models/clusters_{unit}.joblib")
    profiles.to_csv(root/"artifacts/program_clusters.csv", index=False)
    (root/"artifacts/cluster_metrics.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    return summaries

def run(root, threshold=.95):
    # Avoid uncontrolled CPU oversubscription and keep runs practical on team laptops.
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")
    with threadpool_limits(limits=2):
        shelter = forecast_shelter(root, threshold)
        backlog = forecast_backlog(root)
        clusters = cluster_programs(root, threshold)
    return {"shelter_forecasts": len(shelter), "backlog_forecasts": len(backlog), "cluster_groups": len(clusters)}
