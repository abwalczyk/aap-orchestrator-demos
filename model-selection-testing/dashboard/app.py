"""AO Model Evaluation Dashboard.

Reads evaluation results from MLflow and provides interactive model comparison
across quality dimensions and cost.

Usage:
    streamlit run dashboard/app.py
"""

import os
from pathlib import Path

import altair as alt
import mlflow
import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5001")
mlflow.set_tracking_uri(MLFLOW_URI)

CONFIG_PATH = Path(__file__).parent.parent / "harness" / "config.yaml"

EXCLUDED_METRIC_PATTERNS = ["token", "cost", "latency", "error", "success"]

#Placeholders for future worflows
PLACEHOLDER_WORKFLOWS = [
    "ticket_enrichment",
    "cert_rotation",
    "credential_revocation",
    "ssh_key_rotation",
]

PASS_STYLE = "background-color: #c6efce; color: #006100"
FAIL_STYLE = "background-color: #ffc7ce; color: #9c0006"


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_scorer_columns(runs: pd.DataFrame) -> list[str]:
    return [
        c for c in runs.columns
        if c.startswith("metrics.") and c.endswith("/mean")
        and not any(p in c.lower() for p in EXCLUDED_METRIC_PATTERNS)
    ]


def scorer_display_name(col: str) -> str:
    return col.replace("metrics.", "").replace("/mean", "").replace("_", " ").title()


def pass_fail_label(val) -> str:
    if pd.isna(val):
        return ""
    return "PASS" if val >= 0.5 else "FAIL"


def safe_metric(runs: pd.DataFrame, col: str) -> pd.Series:
    full = f"metrics.{col}"
    if full in runs.columns:
        return runs[full]
    return pd.Series([float("nan")] * len(runs), index=runs.index)


def apply_pass_fail_style(row, scorer_cols):
    styles = [""] * len(row)
    for i, col_name in enumerate(row.index):
        if col_name in scorer_cols:
            if row[col_name] == "PASS":
                styles[i] = PASS_STYLE
            elif row[col_name] == "FAIL":
                styles[i] = FAIL_STYLE
    return styles


def assessment_badge(value):
    if value in ("yes", 1, 1.0, True):
        return ":green[PASS]"
    if value in ("no", 0, 0.0, False):
        return ":red[FAIL]"
    return f":orange[{value}]"


def parse_experiment_name(name: str) -> dict | None:
    parts = name.strip("/").split("/")
    if len(parts) == 3:
        return {"prefix": parts[0], "workflow": parts[1], "node_id": parts[2]}
    return None


# ── Data fetching ────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_experiments() -> pd.DataFrame:
    rows = []
    for exp in mlflow.search_experiments():
        parsed = parse_experiment_name(exp.name)
        if parsed:
            rows.append({"experiment_id": exp.experiment_id, **parsed})
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def get_runs(experiment_ids: list) -> pd.DataFrame:
    if not experiment_ids:
        return pd.DataFrame()
    return mlflow.search_runs(experiment_ids=experiment_ids)


@st.cache_data(ttl=300)
def get_trace_assessments(experiment_id: str, run_id: str) -> list[dict]:
    client = mlflow.MlflowClient()
    try:
        traces = client.search_traces(
            experiment_ids=[experiment_id], max_results=100
        )
    except Exception:
        return []

    results = []
    for t in traces:
        if t.info.request_metadata.get("mlflow.sourceRun", "") != run_id:
            continue
        full = client.get_trace(t.info.request_id)
        for a in full.info.assessments or []:
            if not hasattr(a, "feedback") or a.feedback is None:
                continue
            results.append({
                "scorer": a.name,
                "value": a.feedback.value,
                "rationale": a.rationale or "",
                "source_type": a.source.source_type if a.source else "",
            })
    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="AO Model Evaluation Dashboard", layout="wide")

    experiments = get_experiments()
    if experiments.empty:
        st.warning("No experiments found in MLflow. Run some model sweeps first.")
        return

    active_workflows = sorted(experiments["workflow"].unique())
    all_workflows = active_workflows + [
        w for w in PLACEHOLDER_WORKFLOWS if w not in active_workflows
    ]

    with st.sidebar:
        st.header("Workflows", anchor=False)
        for w in all_workflows:
            is_selected = st.session_state.get("selected_workflow", all_workflows[0]) == w
            if st.button(
                f"**{w}**" if is_selected else w,
                key=f"wf_{w}",
                use_container_width=True,
                type="primary" if is_selected else "secondary",
            ):
                st.session_state["selected_workflow"] = w
                st.rerun()
        workflow = st.session_state.get("selected_workflow", all_workflows[0])
        st.divider()
        st.caption(f"MLflow: {MLFLOW_URI}")

    if workflow not in active_workflows:
        st.title(workflow)
        st.info("No evaluation data yet. Run model sweeps for this workflow first.")
        return

    workflow_exps = experiments[experiments["workflow"] == workflow]
    nodes = sorted(workflow_exps["node_id"].unique())
    runs = get_runs(workflow_exps["experiment_id"].tolist())

    if runs.empty:
        st.title(workflow)
        st.info("No runs found for this workflow.")
        return

    runs = runs.merge(
        workflow_exps[["experiment_id", "node_id"]],
        on="experiment_id",
        how="left",
    )

    st.title(workflow)

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_node = st.selectbox("Agent Node", nodes)
    with filter_col2:
        all_models = sorted(runs["params.model"].dropna().unique())
        selected_models = st.multiselect("Models", all_models, default=all_models)

    runs = runs[runs["params.model"].isin(selected_models)]
    if runs.empty:
        st.info("No runs match filters.")
        return

    scorer_cols = get_scorer_columns(runs)

    tab1, tab2, tab3, tab4 = st.tabs([
        "Model Comparison", "Cost Calculator", "Model Scorecard", "Run Details"
    ])
    with tab1:
        render_model_comparison(runs, selected_node, scorer_cols)
    with tab2:
        render_cost_calculator(runs, nodes)
    with tab3:
        render_model_scorecard(runs, selected_node, scorer_cols, workflow_exps)
    with tab4:
        render_run_details(runs, workflow_exps)


# ── Tab 1: Model Comparison ─────────────────────────────────────────────────

def render_model_comparison(runs: pd.DataFrame, selected_node: str, scorer_cols: list):
    st.header("Model Comparison", anchor=False)

    node_runs = runs[runs["node_id"] == selected_node]
    if node_runs.empty or "params.model" not in node_runs.columns:
        st.info("No runs for this node.")
        return

    scorer_names = [scorer_display_name(sc) for sc in scorer_cols]

    agg = {
        "Runs": ("run_id", "count"),
        "Avg Cost (USD)": ("metrics.avg_estimated_cost_usd", "mean"),
        "Avg Tokens": ("metrics.avg_total_tokens", "mean"),
    }
    for sc in scorer_cols:
        agg[scorer_display_name(sc)] = (sc, "mean")

    summary = (
        node_runs.groupby(["params.model", "params.provider"])
        .agg(**agg)
        .reset_index()
        .rename(columns={"params.model": "Model", "params.provider": "Provider"})
    )

    display_df = summary.copy()
    for name in scorer_names:
        display_df[name] = display_df[name].apply(pass_fail_label)

    styled = display_df.style.format(
        {"Avg Cost (USD)": "${:.6f}", "Avg Tokens": "{:.0f}"}, na_rep=""
    ).apply(apply_pass_fail_style, scorer_cols=scorer_names, axis=1)

    st.dataframe(styled, width="stretch", hide_index=True)

    metric_options = ["Avg Cost (USD)"] + scorer_names
    chart_metric = st.selectbox("Chart metric", metric_options, key="comparison_chart_metric")

    chart_data = summary.dropna(subset=[chart_metric])
    if not chart_data.empty:
        chart = alt.Chart(chart_data).mark_bar().encode(
            x=alt.X("Model:N", sort="-y"),
            y=alt.Y(f"{chart_metric}:Q", title=chart_metric),
            color="Model:N",
            tooltip=["Model", "Provider", chart_metric],
        ).properties(height=400)
        st.altair_chart(chart, width="stretch")


# ── Tab 2: Cost Calculator ──────────────────────────────────────────────────

def render_cost_calculator(runs: pd.DataFrame, nodes: list):
    st.header("Workflow Cost Calculator", anchor=False)
    st.caption(
        "Select a model for each agent node to estimate total workflow cost per execution."
    )

    if "params.model" not in runs.columns:
        st.warning("No model data available.")
        return

    selections = {}
    cols = st.columns(min(len(nodes), 3))
    for i, node in enumerate(nodes):
        with cols[i % len(cols)]:
            models = sorted(runs[runs["node_id"] == node]["params.model"].dropna().unique())
            selections[node] = st.selectbox(node, models, key=f"calc_{node}")

    st.divider()

    rows = []
    total_cost = 0.0
    total_tokens = 0

    for node in nodes:
        node_model_runs = runs[
            (runs["node_id"] == node) & (runs["params.model"] == selections[node])
        ]
        avg_cost = safe_metric(node_model_runs, "avg_estimated_cost_usd").mean()
        avg_tokens = safe_metric(node_model_runs, "avg_total_tokens").mean()
        if pd.isna(avg_tokens):
            avg_tokens = 0.0
        total_cost += avg_cost if pd.notna(avg_cost) else 0
        total_tokens += int(avg_tokens)
        rows.append({
            "Node": node,
            "Selected Model": selections[node],
            "Avg Tokens": f"{avg_tokens:.0f}",
            "Avg Cost per Call": f"${avg_cost:.6f}" if pd.notna(avg_cost) else "N/A",
        })

    rows.append({
        "Node": "TOTAL", "Selected Model": "",
        "Avg Tokens": str(total_tokens),
        "Avg Cost per Call": f"${total_cost:.6f}",
    })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Cost per Execution", f"${total_cost:.6f}")
    with col2:
        n = st.number_input("Number of Executions", min_value=1, value=1, step=1, key="exec_count")
        st.metric(f"Total Cost ({n} executions)", f"${total_cost * n:.4f}")


# ── Tab 3: Model Scorecard ──────────────────────────────────────────────────

def render_model_scorecard(
    runs: pd.DataFrame,
    selected_node: str,
    scorer_cols: list,
    workflow_exps: pd.DataFrame,
):
    st.header("Model Scorecard", anchor=False)

    node_runs = runs[runs["node_id"] == selected_node]
    if node_runs.empty or "params.model" not in node_runs.columns:
        st.info("No runs for this node.")
        return

    scorer_names = [scorer_display_name(sc) for sc in scorer_cols]

    agg = {"Runs": ("run_id", "count"), "avg_cost": ("metrics.avg_estimated_cost_usd", "mean")}
    for sc in scorer_cols:
        agg[scorer_display_name(sc)] = (sc, "mean")

    summary = (
        node_runs.groupby("params.model").agg(**agg).reset_index()
        .rename(columns={"params.model": "Model"})
    )

    if scorer_names:
        summary["Overall Pass Rate"] = summary[scorer_names].mean(axis=1)
        total_dims = len(scorer_names)
        summary["Pass Rate"] = summary[scorer_names].apply(
            lambda row: f"{int((row >= 0.5).sum())}/{total_dims}", axis=1
        )

    # Recommendations
    if scorer_names and not summary.empty:
        col1, col2, col3 = st.columns(3)

        best = summary.loc[summary["Overall Pass Rate"].idxmax()]
        with col1:
            st.metric(
                "Best Quality :grey_question:", best["Model"],
                f"{best['Pass Rate']} dimensions",
                help="Model with the highest overall pass rate across all evaluation dimensions, regardless of cost.",
            )

        for col, label, threshold, help_text in [
            (col2, "Best Value", 0.8,
             "Cheapest model where every evaluation dimension scores above 80%."),
            (col3, "Budget Pick", 0.6,
             "Cheapest model where every evaluation dimension scores above 60%."),
        ]:
            qualified = summary[summary[scorer_names].min(axis=1) >= threshold]
            with col:
                if not qualified.empty:
                    pick = qualified.loc[qualified["avg_cost"].idxmin()]
                    st.metric(
                        f"{label} :grey_question:", pick["Model"],
                        f"${pick['avg_cost']:.6f}/call", help=help_text,
                    )
                else:
                    st.metric(
                        f"{label} :grey_question:", "None qualify", "",
                        help=f"{help_text} Currently no model passes all dimensions at that threshold.",
                    )

        st.divider()

    # Score matrix
    display_cols = ["Model", "Runs"] + scorer_names
    if "Pass Rate" in summary.columns:
        display_cols.append("Pass Rate")
    display_cols.append("avg_cost")

    display_df = summary[display_cols].copy()
    display_df.rename(columns={"avg_cost": "Avg Cost (USD)"}, inplace=True)
    for name in scorer_names:
        display_df[name] = summary[name].apply(pass_fail_label)

    st.dataframe(
        display_df.style.format({"Avg Cost (USD)": "${:.6f}"}, na_rep="")
        .apply(apply_pass_fail_style, scorer_cols=scorer_names, axis=1),
        width="stretch", hide_index=True,
    )

    # Dimension chart
    if scorer_names:
        chart_data = pd.DataFrame([
            {
                "Model": row["Model"], "Dimension": sc,
                "Score": row[sc] if pd.notna(row[sc]) else 0,
                "Result": "PASS" if pd.notna(row[sc]) and row[sc] >= 0.5 else "FAIL",
            }
            for _, row in summary.iterrows()
            for sc in scorer_names
        ])
        chart = alt.Chart(chart_data).mark_bar().encode(
            x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 1]), title="Score"),
            y="Model:N",
            color=alt.Color("Result:N", scale=alt.Scale(
                domain=["PASS", "FAIL"], range=["#2ecc71", "#e74c3c"],
            )),
            row=alt.Row("Dimension:N", title=""),
            tooltip=["Model", "Dimension", "Score", "Result"],
        ).properties(height=80, width=500)
        st.altair_chart(chart)

    # Judge explanations (scorecard dimensions only)
    if scorer_names:
        visible_scorers = {name.lower().replace(" ", "_") for name in scorer_names}
        st.divider()
        st.subheader("Judge Explanations", anchor=False)

        for model_name in sorted(node_runs["params.model"].dropna().unique()):
            model_runs = node_runs[node_runs["params.model"] == model_name]
            with st.expander(f"**{model_name}**"):
                for _, run_row in model_runs.iterrows():
                    assessments = [
                        a for a in get_trace_assessments(run_row["experiment_id"], run_row["run_id"])
                        if a["scorer"].lower() in visible_scorers
                    ]
                    if not assessments:
                        continue
                    run_label = run_row.get("tags.mlflow.runName", run_row["run_id"][:8])
                    st.markdown(f"**{run_label}** (`{run_row['run_id'][:8]}`)")
                    for a in assessments:
                        source = "LLM Judge" if a["source_type"] == "LLM_JUDGE" else "Code"
                        with st.expander(f"{assessment_badge(a['value'])} **{a['scorer']}** ({source})"):
                            st.write(a["rationale"])
                    st.markdown("---")


# ── Tab 4: Run Details ───────────────────────────────────────────────────────

def render_run_details(runs: pd.DataFrame, workflow_exps: pd.DataFrame):
    st.header("Run Details", anchor=False)

    if runs.empty:
        st.info("No runs available. Run some model sweeps first.")
        return

    name_col = "tags.mlflow.runName" if "tags.mlflow.runName" in runs.columns else "run_id"
    run_options = runs[[name_col, "run_id", "experiment_id"]].drop_duplicates()
    labels = [f"{row[name_col]} ({row['run_id'][:8]})" for _, row in run_options.iterrows()]

    selected_idx = st.selectbox("Select Run", range(len(labels)), format_func=lambda i: labels[i])
    selected = run_options.iloc[selected_idx]
    run_row = runs[runs["run_id"] == selected["run_id"]].iloc[0]

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Quality Scores", anchor=False)
        scorer_cols = get_scorer_columns(runs)
        if scorer_cols:
            for sc in scorer_cols:
                val = run_row.get(sc)
                if pd.notna(val):
                    badge = ":green[**PASS**]" if val >= 0.5 else ":red[**FAIL**]"
                    st.markdown(f"{badge} {scorer_display_name(sc)}")
        else:
            st.info("No scorer metrics found.")

    with col2:
        st.subheader("Cost & Tokens", anchor=False)
        cost_metrics = {}
        for c in runs.columns:
            if not c.startswith("metrics."):
                continue
            name = c.replace("metrics.", "")
            if "latency" in name:
                continue
            val = run_row[c]
            if pd.notna(val) and ("cost" in name or "token" in name):
                cost_metrics[name] = f"${val:.6f}" if "cost" in name else f"{val:.0f}"
        if cost_metrics:
            st.json(cost_metrics)
        else:
            st.info("No cost/token data.")

        st.subheader("Parameters", anchor=False)
        params = {
            c.replace("params.", ""): run_row[c]
            for c in runs.columns if c.startswith("params.") and pd.notna(run_row[c])
        }
        st.json(params)

    # Judge explanations (all assessments)
    st.subheader("Judge Explanations", anchor=False)
    assessments = get_trace_assessments(selected["experiment_id"], selected["run_id"])
    if assessments:
        for a in assessments:
            source = "LLM Judge" if a["source_type"] == "LLM_JUDGE" else "Code"
            with st.expander(f"{assessment_badge(a['value'])} **{a['scorer']}** ({source})"):
                st.write(a["rationale"])
    else:
        st.info("No judge explanations available. Run evaluations with judges enabled.")

    # Per-iteration token usage
    st.subheader("Per-Iteration Token Usage", anchor=False)
    try:
        client = mlflow.MlflowClient()
        input_hist = client.get_metric_history(selected["run_id"], "input_tokens")
        output_hist = client.get_metric_history(selected["run_id"], "output_tokens")
        cost_hist = client.get_metric_history(selected["run_id"], "estimated_cost_usd")

        if input_hist:
            iter_data = [
                {
                    "Iteration": inp.step + 1,
                    "Input Tokens": int(inp.value),
                    "Output Tokens": int(out.value),
                    "Total Tokens": int(inp.value + out.value),
                }
                for inp, out in zip(input_hist, output_hist)
            ]
            for i, c in enumerate(cost_hist):
                if i < len(iter_data):
                    iter_data[i]["Cost (USD)"] = f"${c.value:.6f}"
            st.dataframe(pd.DataFrame(iter_data), width="stretch", hide_index=True)
        else:
            st.info("No per-iteration token data available for this run.")
    except Exception as e:
        st.info(f"Could not load iteration history: {e}")


if __name__ == "__main__":
    main()
