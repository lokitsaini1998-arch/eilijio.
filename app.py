"""
app.py — Streamlit front end for the trade analysis engine.

Run locally:      streamlit run app.py
Deploy:            push this folder to a GitHub repo, then deploy on
                    https://share.streamlit.io (Streamlit Community Cloud, free).

Password: set an APP_PASSWORD secret (see README.md) so the app isn't open
to anyone with the link if you deploy it publicly.
"""

import streamlit as st
import pandas as pd
import engine as eng

st.set_page_config(page_title="Trade Analysis Dashboard", layout="wide")

# =============================================================================
# PASSWORD GATE
# =============================================================================
def check_password():
    def password_entered():
        correct = st.secrets.get("APP_PASSWORD", None)
        if correct is None:
            # No password configured (e.g. running locally without secrets.toml) — allow through.
            st.session_state["authed"] = True
            return
        st.session_state["authed"] = (st.session_state.get("pw_input", "") == correct)

    if st.session_state.get("authed", False):
        return True

    st.title("Trade Analysis Dashboard")
    st.text_input("Password", type="password", key="pw_input", on_change=password_entered)
    if "authed" in st.session_state and not st.session_state["authed"]:
        st.error("Incorrect password.")
    return False


if not check_password():
    st.stop()

# =============================================================================
# SIDEBAR — data + CL inputs
# =============================================================================
st.sidebar.header("1. Base Data")
uploaded = st.sidebar.file_uploader("Upload your base workbook (.xlsx)", type=["xlsx"])

st.sidebar.header("2. CL Values")
st.sidebar.caption("Tolerance widens automatically if a CL zone is thin on samples.")

cl_values = []
for label, _ in eng.instrument_config:
    val = st.sidebar.number_input(f"{label} CL", min_value=0.0, max_value=10.0,
                                   value=0.00, step=0.01, format="%.2f", key=f"cl_{label}")
    cl_values.append(val)

run_clicked = st.sidebar.button("Run Analysis + Trade Score", type="primary", use_container_width=True)

# =============================================================================
# MAIN AREA
# =============================================================================
st.title("Trade Analysis Dashboard")

if uploaded is None:
    st.info("Upload your base workbook in the sidebar to get started.")
    st.stop()

if not run_clicked and "scores" not in st.session_state:
    st.info("Enter your CL values in the sidebar and click **Run Analysis + Trade Score**.")
    st.stop()

if run_clicked:
    with st.spinner("Running adaptive tolerance, reliability, and scoring..."):
        xl = eng.load_base_workbook(uploaded.getvalue())
        summary_percent, reliability_df, avg_results, rec_avg_results, bucket_summary, data, sheets1 = \
            eng.run_analysis(xl, cl_values)
        scores, sheets2 = eng.run_trade_scoring(
            summary_percent, reliability_df, avg_results, rec_avg_results, bucket_summary, cl_values, data)
        all_sheets = {**sheets1, **sheets2}

    st.session_state["scores"] = scores
    st.session_state["all_sheets"] = all_sheets
    st.session_state["xlsx_bytes"] = eng.build_excel_bytes(all_sheets)

scores = st.session_state["scores"]
all_sheets = st.session_state["all_sheets"]

# --- Top-line verdict cards ---
top = scores.iloc[0]
verdict = top["Verdict"]
verdict_color = {
    "PRIMARY TRADE": "success",
    "BACKUP WATCH": "info",
    "LOW CONFIDENCE - INSUFFICIENT DATA": "warning",
    "NO SETUP - AVOID": "error",
    "AVOID": "error",
    "WEAK - PAPER ONLY": "warning",
}.get(verdict, "info")

st.subheader(f"Top Pick: {scores.index[0]}")
getattr(st, verdict_color)(
    f"**{verdict}** — Composite {top['Composite']:.1f}/100 · "
    f"Wilson 95% CI {top['Wilson_Lower']*100:.1f}%–{top['Wilson_Upper']*100:.1f}% · "
    f"N={int(top['N_Samples'])}"
)

st.download_button(
    "Download full report (.xlsx)",
    data=st.session_state["xlsx_bytes"],
    file_name="Combined_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()

# --- All 7 instruments, quick view ---
st.subheader("All Instruments")
quick = scores[["Composite", "Setup_Score", "Profit_Score", "Consistency_Score",
                 "Wilson_Lower", "N_Samples", "Rank", "Verdict"]].copy()
quick["Wilson_Lower"] = quick["Wilson_Lower"].apply(lambda x: f"{x*100:.1f}%" if pd.notna(x) else "N/A")
st.dataframe(quick, use_container_width=True)

st.divider()

# --- Full sheet-by-sheet view, same structure as the Excel report ---
st.subheader("Full Report (every sheet)")
tab_names = list(all_sheets.keys())
tabs = st.tabs(tab_names)
for tab, name in zip(tabs, tab_names):
    with tab:
        st.dataframe(all_sheets[name], use_container_width=True)
