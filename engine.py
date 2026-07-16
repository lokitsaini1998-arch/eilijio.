"""
engine.py — Analysis engine, framework-independent.

Same logic as combined_report_v2.py (adaptive tolerance, Wilson CI, recency
weighting, walk-forward check, correlation layer, trade scoring), refactored
to accept an already-open pd.ExcelFile instead of a local file path — so it
works identically whether the source is a file on disk or an uploaded file
in a web app.
"""

import io
from datetime import datetime

import numpy as np
import pandas as pd

# =============================================================================
# CONFIG
# =============================================================================
instrument_config = [
    ("BNF", "BNF"),
    ("NF", "NF"),
    ("ICICI", "ICICI"),
    ("SBI", "SBI"),
    ("KTK", "KTK"),
    ("AXISBANK", "XSB"),
    ("HDFC", "HDFC"),
]

cld_types = ["MT", "OR", "CH", "CR", "CHRA", "CRA", "MTRA", "RA", "TR", "TRC", "ST"]
FAVORABLE_SET = {"TR", "TRC", "ST"}

BASE_TOLERANCE = 0.005
MIN_SAMPLES = 20
MAX_TOLERANCE = 0.05
TOL_STEP = 0.005
HALF_LIFE_DAYS = 730
MIN_WILSON_LOWER = 0.55
WALK_FORWARD_YEARS_BACK = 2
CORR_THRESHOLD = 0.35

BUCKETS = [round(i * 0.30, 2) for i in range(18)]
RANGES = [f"{BUCKETS[i]:.2f}% - {BUCKETS[i+1]:.2f}%" for i in range(len(BUCKETS) - 1)]


def _bucket_index(val):
    if val < 0:
        return -1
    for i in range(len(BUCKETS) - 1):
        if BUCKETS[i] <= val * 100 < BUCKETS[i + 1]:
            return i
    return len(BUCKETS) - 2


# =============================================================================
# DATA LOADING
# =============================================================================
def load_base_workbook(file_source):
    """file_source: a path string, bytes, or file-like object (e.g. Streamlit's
    UploadedFile). Returns a pd.ExcelFile that can be parsed repeatedly."""
    if isinstance(file_source, (bytes, bytearray)):
        file_source = io.BytesIO(file_source)
    return pd.ExcelFile(file_source)


def load_instrument_sheet(xl: pd.ExcelFile, sheet):
    if sheet not in xl.sheet_names:
        return None
    df = xl.parse(sheet, usecols="A:D", header=0)
    df.columns = ["Date", "CL", "CLD", "C%"][: len(df.columns)]
    if "C%" not in df.columns:
        df["C%"] = np.nan
    df["CL"] = pd.to_numeric(df["CL"], errors="coerce")
    df["C%"] = pd.to_numeric(df["C%"], errors="coerce")
    df["CLD"] = df["CLD"].astype(str).str.strip().str.upper()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "CL", "CLD"])
    df = df[df["CLD"].str.lower() != "nan"]
    df = df[df["CLD"] != ""]
    df["CL"] = df["CL"].round(2)
    return df.reset_index(drop=True)


# =============================================================================
# STATS ENGINE
# =============================================================================
def wilson_ci(successes, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = (z * np.sqrt((p * (1 - p) + z**2 / (4 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def adaptive_tolerance(df, cl_target, base_tol=BASE_TOLERANCE, min_samples=MIN_SAMPLES,
                        max_tol=MAX_TOLERANCE, step=TOL_STEP):
    cl_target = round(float(cl_target), 2)
    tol = base_tol
    matches = df[(df["CL"] >= cl_target - tol) & (df["CL"] <= cl_target + tol)]
    while len(matches) < min_samples and tol < max_tol:
        tol += step
        matches = df[(df["CL"] >= cl_target - tol) & (df["CL"] <= cl_target + tol)]
    widened = tol > base_tol
    return round(tol, 4), matches, widened


def recency_weighted_prob(matches, favorable_set=FAVORABLE_SET,
                           half_life_days=HALF_LIFE_DAYS, ref_date=None):
    if len(matches) == 0:
        return None
    if ref_date is None:
        ref_date = matches["Date"].max()
    age_days = (ref_date - matches["Date"]).dt.days.clip(lower=0)
    w = 0.5 ** (age_days / half_life_days)
    fav = matches["CLD"].isin(favorable_set).astype(float)
    w_sum = w.sum()
    return float((w * fav).sum() / w_sum) if w_sum > 0 else None


def recency_weighted_avg(matches, value_col="C%", half_life_days=HALF_LIFE_DAYS, ref_date=None):
    if len(matches) == 0 or value_col not in matches:
        return None
    if ref_date is None:
        ref_date = matches["Date"].max()
    age_days = (ref_date - matches["Date"]).dt.days.clip(lower=0)
    w = 0.5 ** (age_days / half_life_days)
    vals = pd.to_numeric(matches[value_col], errors="coerce")
    mask = vals.notna()
    w, vals = w[mask], vals[mask]
    return float((w * vals).sum() / w.sum()) if w.sum() > 0 else None


def walk_forward_check(df, cl_target, tol, favorable_set=FAVORABLE_SET,
                        years_back=WALK_FORWARD_YEARS_BACK):
    if df.empty:
        return None
    split_date = df["Date"].max() - pd.DateOffset(years=years_back)
    train = df[df["Date"] < split_date]
    test = df[df["Date"] >= split_date]
    cl_target = round(float(cl_target), 2)
    tm = train[(train["CL"] >= cl_target - tol) & (train["CL"] <= cl_target + tol)]
    tt = test[(test["CL"] >= cl_target - tol) & (test["CL"] <= cl_target + tol)]
    if len(tm) == 0 or len(tt) == 0:
        return None
    train_prob = tm["CLD"].isin(favorable_set).mean()
    test_prob = tt["CLD"].isin(favorable_set).mean()
    return {
        "split_date": split_date.date(),
        "train_prob": train_prob, "train_n": len(tm),
        "test_prob": test_prob, "test_n": len(tt),
        "gap": test_prob - train_prob,
    }


def build_favorable_series(df, favorable_set=FAVORABLE_SET):
    return df.groupby("Date")["CLD"].first().isin(favorable_set).astype(int)


def build_correlation_matrix(data_dict, favorable_set=FAVORABLE_SET):
    series = {name: build_favorable_series(df, favorable_set)
              for name, df in data_dict.items() if df is not None}
    if len(series) < 2:
        return pd.DataFrame()
    return pd.DataFrame(series).corr()


def flag_correlated_triggers(verdicts: dict, corr_matrix, corr_threshold=CORR_THRESHOLD):
    triggered = [k for k, v in verdicts.items() if v in ("PRIMARY TRADE", "BACKUP WATCH")]
    flags = []
    for i, a in enumerate(triggered):
        for b in triggered[i + 1:]:
            if a in corr_matrix.index and b in corr_matrix.columns:
                c = corr_matrix.loc[a, b]
                if pd.notna(c) and c >= corr_threshold:
                    flags.append((a, b, round(float(c), 2)))
    return flags


def enhanced_lookup(df, cl_target):
    if df is None or df.empty:
        return {
            "cl_target": cl_target, "tolerance_used": None, "tolerance_widened": None,
            "n_samples": 0, "raw_prob": 0.0, "wilson_lower": 0.0, "wilson_upper": 0.0,
            "recency_prob": None, "avg_c_pct": None, "recency_avg_c_pct": None,
            "tradeable": False, "reason": "no data", "matches": df if df is not None else pd.DataFrame(),
        }
    tol, matches, widened = adaptive_tolerance(df, cl_target)
    n = len(matches)
    successes = matches["CLD"].isin(FAVORABLE_SET).sum() if n else 0
    raw_prob = successes / n if n else 0.0
    lo, hi = wilson_ci(successes, n)
    rec_prob = recency_weighted_prob(matches)
    avg_c = matches["C%"].mean() * 100 if n and matches["C%"].notna().any() else None
    rec_avg_c = recency_weighted_avg(matches) if n else None
    if rec_avg_c is not None:
        rec_avg_c *= 100
    tradeable = (n >= MIN_SAMPLES) and (lo >= MIN_WILSON_LOWER)
    reason = ("insufficient samples" if n < MIN_SAMPLES else
              "Wilson lower bound below threshold" if lo < MIN_WILSON_LOWER else "OK")
    return {
        "cl_target": cl_target, "tolerance_used": tol, "tolerance_widened": widened,
        "n_samples": n, "raw_prob": raw_prob, "wilson_lower": lo, "wilson_upper": hi,
        "recency_prob": rec_prob, "avg_c_pct": avg_c, "recency_avg_c_pct": rec_avg_c,
        "tradeable": tradeable, "reason": reason, "matches": matches,
    }


# =============================================================================
# CORE ANALYSIS
# =============================================================================
def run_analysis(xl: pd.ExcelFile, cl_values):
    data = {label: load_instrument_sheet(xl, sheet) for label, sheet in instrument_config}
    labels = [label for label, _ in instrument_config]
    column_names = [f"{label} (CL = {cl:.3f} \u00b1adaptive)" for label, cl in zip(labels, cl_values)]

    output_detailed = pd.DataFrame(index=cld_types, columns=column_names)
    reliability_rows = []
    lookups = {}

    for label, cl, col in zip(labels, cl_values, column_names):
        df = data[label]
        result = enhanced_lookup(df, cl)
        lookups[label] = result
        if df is None:
            for cld in cld_types:
                output_detailed.at[cld, col] = "No Data"
        else:
            counts = result["matches"]["CLD"].value_counts()
            for cld in cld_types:
                output_detailed.at[cld, col] = int(counts.get(cld, 0))

        reliability_rows.append({
            "Instrument": label, "CL_Target": cl,
            "Tolerance_Used": result["tolerance_used"], "Tolerance_Widened": result["tolerance_widened"],
            "N_Samples": result["n_samples"], "Raw_Favorable_Prob": result["raw_prob"],
            "Wilson_Lower_95": result["wilson_lower"], "Wilson_Upper_95": result["wilson_upper"],
            "Recency_Weighted_Prob": result["recency_prob"], "Avg_C%": result["avg_c_pct"],
            "Recency_Weighted_Avg_C%": result["recency_avg_c_pct"],
            "Tradeable": result["tradeable"], "Reason": result["reason"],
        })

    try:
        output_detailed.loc["Total"] = output_detailed.apply(
            lambda col: col.astype(int).sum() if col.apply(lambda v: str(v).isdigit()).all() else "N/A"
        )
    except Exception:
        output_detailed.loc["Total"] = "N/A"

    reliability_df = pd.DataFrame(reliability_rows).set_index("Instrument")

    summary_index = ["MT", "OR", "RA", "TRnC", "ST"]
    summary_counts = pd.DataFrame(index=summary_index, columns=labels)
    for label, col in zip(labels, column_names):
        if isinstance(output_detailed.at["MT", col], str):
            summary_counts.loc[:, label] = "Error"
            continue
        summary_counts.at["MT", label] = output_detailed.at["MT", col]
        summary_counts.at["ST", label] = output_detailed.at["ST", col]
        summary_counts.at["OR", label] = output_detailed.loc[["OR", "CH", "CR"], col].sum()
        summary_counts.at["RA", label] = output_detailed.loc[["RA", "CHRA", "CRA", "MTRA"], col].sum()
        summary_counts.at["TRnC", label] = output_detailed.loc[["TR", "TRC"], col].sum()

    num_cols = [c for c in summary_counts.columns if (summary_counts[c] != "Error").all()]
    for col in num_cols:
        summary_counts.loc["Total", col] = int(
            pd.to_numeric(summary_counts.loc[summary_index, col], errors="coerce").sum())

    summary_percent_final = pd.DataFrame(index=["MT", "OR", "RA", "TR+ST", "ST"], columns=labels)
    totals = summary_counts.loc["Total"]
    for idx in ["MT", "OR", "RA", "ST"]:
        for col in labels:
            if summary_counts.at[idx, col] == "Error" or totals.get(col, 0) in ("Error", 0):
                summary_percent_final.at[idx, col] = 0.0
            else:
                summary_percent_final.at[idx, col] = float(summary_counts.at[idx, col]) / float(totals[col])
    for col in labels:
        if summary_counts.at["TRnC", col] == "Error" or summary_counts.at["ST", col] == "Error" or totals.get(col, 0) in ("Error", 0):
            summary_percent_final.at["TR+ST", col] = 0.0
        else:
            combined = float(summary_counts.at["TRnC", col]) + float(summary_counts.at["ST", col])
            summary_percent_final.at["TR+ST", col] = combined / float(totals[col]) if float(totals[col]) > 0 else 0.0

    avg_results, rec_avg_results, bucket_summary = {}, {}, {}
    for label in labels:
        r = lookups[label]
        avg_results[label] = round(r["avg_c_pct"], 2) if r["avg_c_pct"] is not None else None
        rec_avg_results[label] = round(r["recency_avg_c_pct"], 2) if r["recency_avg_c_pct"] is not None else None
        counts = [0] * len(RANGES)
        if r["n_samples"] > 0:
            for c in r["matches"]["C%"].dropna():
                idx = _bucket_index(c)
                if 0 <= idx < len(counts):
                    counts[idx] += 1
        bucket_summary[label] = counts

    avg_df = pd.DataFrame({
        "Instrument": labels,
        "Avg_C%": [avg_results[l] for l in labels],
        "Recency_Weighted_Avg_C%": [rec_avg_results[l] for l in labels],
        "N_Samples": [lookups[l]["n_samples"] for l in labels],
    })
    bucket_df = pd.DataFrame(bucket_summary, index=RANGES).T
    bucket_df.index.name = "Instrument"

    display_percent = summary_percent_final.copy()
    for col in labels:
        display_percent[col] = display_percent[col].apply(lambda x: f"{x*100:.2f}%" if pd.notna(x) else "0.00%")

    sheets = {
        "CLD_Detailed": output_detailed,
        "CLD_Summary_Counts": summary_counts,
        "CLD_Summary_Percent": display_percent,
        "Reliability": reliability_df,
        "C%_Averages": avg_df,
        "C%_Bucket_Counts": bucket_df,
    }
    return summary_percent_final, reliability_df, avg_results, rec_avg_results, bucket_summary, data, sheets


# =============================================================================
# TRADE SCORING ENGINE
# =============================================================================
def run_trade_scoring(summary_percent, reliability_df, avg_results, rec_avg_results,
                       bucket_summary, cl_values, data):
    labels = [label for label, _ in instrument_config]
    scores = pd.DataFrame(index=labels)

    scores["MT"] = [summary_percent.at["MT", s] if s in summary_percent.columns else 0 for s in labels]
    scores["OR"] = [summary_percent.at["OR", s] if s in summary_percent.columns else 0 for s in labels]
    scores["RA"] = [summary_percent.at["RA", s] if s in summary_percent.columns else 0 for s in labels]
    scores["TR+ST_raw"] = [summary_percent.at["TR+ST", s] if s in summary_percent.columns else 0 for s in labels]
    scores["ST"] = [summary_percent.at["ST", s] if s in summary_percent.columns else 0 for s in labels]

    scores["Wilson_Lower"] = reliability_df["Wilson_Lower_95"].reindex(labels)
    scores["Wilson_Upper"] = reliability_df["Wilson_Upper_95"].reindex(labels)
    scores["Recency_Prob"] = reliability_df["Recency_Weighted_Prob"].reindex(labels)
    scores["N_Samples"] = reliability_df["N_Samples"].reindex(labels)
    scores["Tolerance_Widened"] = reliability_df["Tolerance_Widened"].reindex(labels)
    scores["Tradeable_Reliability"] = reliability_df["Tradeable"].reindex(labels)

    scores["TR+ST"] = scores["Recency_Prob"].where(scores["Recency_Prob"].notna(), scores["TR+ST_raw"])

    scores["Avg_C%"] = [avg_results.get(s) for s in labels]
    scores["Recency_Avg_C%"] = [rec_avg_results.get(s) for s in labels]
    scores["Total_Samples"] = [sum(bucket_summary.get(s, [0])) for s in labels]
    scores["Profitable_Days"] = [sum(bucket_summary.get(s, [0])[2:]) for s in labels]
    scores["Low_Bracket_Days"] = [bucket_summary.get(s, [0])[0] for s in labels]

    favorable = scores["TR+ST"] * 0.60 + scores["ST"] * 0.40
    unfavorable = scores["OR"] * 0.15 + scores["RA"] * 0.15 + scores["MT"] * 0.10
    scores["Setup_Score"] = ((favorable - unfavorable) * 100).clip(0, 100)

    profit_base_input = scores["Recency_Avg_C%"].where(scores["Recency_Avg_C%"].notna(), scores["Avg_C%"])
    profit_base = (profit_base_input / 1.5 * 100).clip(0, 100)
    reliability_factor = scores["Total_Samples"].apply(lambda x: min(1.0, x / 10) if pd.notna(x) and x > 0 else 0)
    scores["Profit_Score"] = profit_base.fillna(0) * 0.70 + reliability_factor * 30

    profit_ratio = (scores["Profitable_Days"] / scores["Total_Samples"]).fillna(0)
    low_ratio = (scores["Low_Bracket_Days"] / scores["Total_Samples"]).fillna(0)
    penalty = (low_ratio - 0.4).clip(lower=0) * 100
    scores["Consistency_Score"] = (profit_ratio * 100 - penalty).clip(0, 100)

    no_data_mask = scores["Avg_C%"].isna()
    scores.loc[no_data_mask, ["Profit_Score", "Consistency_Score"]] = 0
    scores.loc[no_data_mask, "Setup_Score"] = scores.loc[no_data_mask, "Setup_Score"].fillna(0) * 0.3

    scores["Composite"] = (scores["Setup_Score"].fillna(0) * 0.45 +
                            scores["Profit_Score"].fillna(0) * 0.35 +
                            scores["Consistency_Score"].fillna(0) * 0.20)

    scores["Rank"] = scores["Composite"].rank(ascending=False, method="dense").astype(int)
    scores = scores.sort_values("Rank")

    def verdict(row):
        if not row["Tradeable_Reliability"]:
            return "LOW CONFIDENCE - INSUFFICIENT DATA"
        if row["Rank"] == 1 and row["Composite"] >= 35:
            return "PRIMARY TRADE"
        elif row["Composite"] >= 30:
            return "BACKUP WATCH"
        elif row["Setup_Score"] < 10:
            return "NO SETUP - AVOID"
        elif row["Composite"] < 20:
            return "AVOID"
        else:
            return "WEAK - PAPER ONLY"

    scores["Verdict"] = scores.apply(verdict, axis=1)

    corr_matrix = build_correlation_matrix(data)
    verdict_dict = scores["Verdict"].to_dict()
    correlated_pairs = flag_correlated_triggers(verdict_dict, corr_matrix)

    wf_rows = []
    for label, cl in zip(labels, cl_values):
        df = data.get(label)
        tol = reliability_df.at[label, "Tolerance_Used"] if label in reliability_df.index else None
        if df is None or tol is None:
            continue
        wf = walk_forward_check(df, cl, tol)
        if wf:
            wf_rows.append({
                "Instrument": label, "CL": cl, "Split_Date": wf["split_date"],
                "Train_Prob": wf["train_prob"], "Train_N": wf["train_n"],
                "Test_Prob": wf["test_prob"], "Test_N": wf["test_n"], "Gap": wf["gap"],
                "Flag": "WARNING - edge may be decaying" if abs(wf["gap"]) > 0.15 else "stable",
            })
    wf_df = pd.DataFrame(wf_rows) if wf_rows else pd.DataFrame(
        [{"Note": "Not enough train/test samples to run walk-forward check for current CL values"}])

    warnings = []
    for label in scores.index:
        row = scores.loc[label]
        if not row["Tradeable_Reliability"]:
            warnings.append([label, "CRITICAL", f"Reliability gate failed — {reliability_df.at[label,'Reason']}"])
        if pd.notna(row["N_Samples"]) and 0 < row["N_Samples"] < MIN_SAMPLES:
            warnings.append([label, "WARNING", f"Only {int(row['N_Samples'])} samples even after adaptive widening"])
        if row.get("Tolerance_Widened"):
            warnings.append([label, "INFO", "CL tolerance had to widen beyond base to find enough samples"])
        if row["Setup_Score"] < 10:
            warnings.append([label, "CRITICAL", "Setup Score < 10 — no tradeable pattern"])
        if row["Total_Samples"] > 0 and row["Low_Bracket_Days"] / row["Total_Samples"] > 0.5:
            warnings.append([label, "WARNING", ">50% of days stuck in 0.00-0.30% bracket — zombie stock"])
    for a, b, c in correlated_pairs:
        warnings.append([f"{a}+{b}", "WARNING", f"Correlated triggers (corr={c}) — size down, not two independent trades"])
    for _, wf_row in wf_df.iterrows():
        if wf_row.get("Flag") == "WARNING - edge may be decaying":
            warnings.append([wf_row["Instrument"], "WARNING",
                              f"Walk-forward gap {wf_row['Gap']*100:+.1f}pp — edge may be decaying"])
    if not warnings:
        warnings = [["ALL", "OK", "No critical warnings today"]]
    warn_df = pd.DataFrame(warnings, columns=["Instrument", "Severity", "Warning"])

    scorecard_cols = ["TR+ST_raw", "Recency_Prob", "Wilson_Lower", "Wilson_Upper", "ST", "OR", "RA",
                       "Avg_C%", "Recency_Avg_C%", "N_Samples", "Tolerance_Widened",
                       "Setup_Score", "Profit_Score", "Consistency_Score",
                       "Composite", "Rank", "Tradeable_Reliability", "Verdict"]
    scorecard = scores[scorecard_cols].copy()

    pairs_df = pd.DataFrame(correlated_pairs, columns=["Instrument_A", "Instrument_B", "Correlation"]) \
        if correlated_pairs else pd.DataFrame([{"Note": "No correlated pairs flagged among today's triggered instruments"}])

    top = scores.iloc[0]
    confidence = "HIGH" if top["Composite"] >= 40 and top["Tradeable_Reliability"] else \
                 "MEDIUM" if top["Composite"] >= 30 and top["Tradeable_Reliability"] else "LOW"
    rec_data = pd.DataFrame({
        "Item": ["Analysis Date", "Top Recommended Instrument", "Composite Score", "Setup Score",
                 "Profit Score", "Consistency Score", "Wilson 95% CI (favorable outcome)", "Sample Size",
                 "Recommended Action", "Confidence Level", "Correlated With Other Triggers"],
        "Value": [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), scores.index[0],
            f"{top['Composite']:.2f} / 100", f"{top['Setup_Score']:.2f} / 100",
            f"{top['Profit_Score']:.2f} / 100", f"{top['Consistency_Score']:.2f} / 100",
            f"{top['Wilson_Lower']*100:.1f}% - {top['Wilson_Upper']*100:.1f}%" if pd.notna(top["Wilson_Lower"]) else "N/A",
            int(top["N_Samples"]) if pd.notna(top["N_Samples"]) else 0, top["Verdict"], confidence,
            "; ".join(f"{a}-{b} (corr {c})" for a, b, c in correlated_pairs if scores.index[0] in (a, b)) or "None flagged",
        ]
    })

    sheets = {
        "Trade_Scorecard": scorecard,
        "Trade_Recommendation": rec_data,
        "Quick_Rankings": scores[["Composite", "Setup_Score", "Profit_Score", "Consistency_Score",
                                   "Wilson_Lower", "N_Samples", "Rank", "Verdict"]],
        "Correlation_Matrix": corr_matrix,
        "Correlated_Pairs": pairs_df,
        "Walk_Forward_Check": wf_df,
        "Warnings": warn_df,
    }
    return scores, sheets


def build_excel_bytes(all_sheets: dict):
    """Writes every sheet dict into one xlsx in memory and returns the bytes."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in all_sheets.items():
            df.to_excel(writer, sheet_name=name[:31])  # Excel sheet name limit
    buffer.seek(0)
    return buffer.getvalue()
