"""AO Model Evaluation Dashboard.

Reads evaluation results from MLflow and provides interactive model comparison
across quality dimensions and cost. Primary comparison tool for the pilot.

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


def load_harness_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    return {}


def get_model_pricing(config: dict) -> dict:
    pricing = {}
    for m in config.get("models", []):
        inp = m.get("cost_per_1m_input_tokens")
        out = m.get("cost_per_1m_output_tokens")
        if inp is not None and out is not None:
            pricing[m["name"]] = {
                "provider": m.get("provider", "unknown"),
                "cost_per_1m_input": inp,
                "cost_per_1m_output": out,
            }
    return pricing


def parse_experiment_name(name: str) -> dict | None:
    parts = name.strip("/").split("/")
    if len(parts) == 3:
        return {"prefix": parts[0], "workflow": parts[1], "node_id": parts[2]}
    return None


def get_scorer_columns(runs: pd.DataFrame) -> list[str]:
    """Find scorer metric columns (pattern: metrics.*/mean)."""
    return [
        c for c in runs.columns
        if c.startswith("metrics.") and c.endswith("/mean")
        and not any(p in c.lower() for p in EXCLUDED_METRIC_PATTERNS)
    ]


def scorer_display_name(col: str) -> str:
    return col.replace("metrics.", "").replace("/mean", "").replace("_", " ").title()


def safe_metric(runs: pd.DataFrame, col: str) -> pd.Series:
    full = f"metrics.{col}"
    if full in runs.columns:
        return runs[full]
    return pd.Series([float("nan")] * len(runs), index=runs.index)


def pass_fail_label(val) -> str:
    if pd.isna(val):
        return ""
    return "PASS" if val >= 0.5 else "FAIL"


def pass_fail_color(val) -> str:
    if pd.isna(val):
        return ""
    if val >= 0.5:
        return "background-color: #c6efce; color: #006100"
    return "background-color: #ffc7ce; color: #9c0006"


@st.cache_data(ttl=300)
def get_experiments() -> pd.DataFrame:
    experiments = mlflow.search_experiments()
    rows = []
    for exp in experiments:
        parsed = parse_experiment_name(exp.name)
        if parsed:
            rows.append({
                "experiment_id": exp.experiment_id,
                "name": exp.name,
                **parsed,
            })
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def get_runs(experiment_ids: list) -> pd.DataFrame:
    if not experiment_ids:
        return pd.DataFrame()
    return mlflow.search_runs(experiment_ids=experiment_ids)


@st.cache_data(ttl=300)
def get_trace_assessments(experiment_id: str, run_id: str) -> list[dict]:
    """Fetch judge assessments for a run's traces."""
    client = mlflow.MlflowClient()
    try:
        traces = client.search_traces(
            experiment_ids=[experiment_id], max_results=100
        )
    except Exception:
        return []

    results = []
    for t in traces:
        source_run = t.info.request_metadata.get("mlflow.sourceRun", "")
        if source_run != run_id:
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
                "trace_id": t.info.request_id,
            })
    return results


def main():
    st.set_page_config(page_title="AO Model Evaluation Dashboard", layout="wide")

    config = load_harness_config()
    model_pricing = get_model_pricing(config)

    experiments = get_experiments()
    if experiments.empty:
        st.warning("No experiments found in MLflow. Run some model sweeps first.")
        return

    PLACEHOLDER_WORKFLOWS = [
        "ticket_enrichment",
        "cert_rotation",
        "credential_revocation",
        "ssh_key_rotation",
    ]

    active_workflows = sorted(experiments["workflow"].unique())
    all_workflows = active_workflows + [
        w for w in PLACEHOLDER_WORKFLOWS if w not in active_workflows
    ]

    # Sidebar: workflow list only
    with st.sidebar:
        st.header("Workflows")
        for w in all_workflows:
            is_selected = st.session_state.get("selected_workflow", all_workflows[0]) == w
            label = f"**{w}**" if is_selected else w
            if st.button(label, key=f"wf_{w}", use_container_width=True, type="primary" if is_selected else "secondary"):
                st.session_state["selected_workflow"] = w
                st.rerun()
        workflow = st.session_state.get("selected_workflow", all_workflows[0])
        st.divider()
        st.caption(f"MLflow: {MLFLOW_URI}")

    if workflow in PLACEHOLDER_WORKFLOWS and workflow not in active_workflows:
        st.title(f"{workflow}")
        st.info("No evaluation data yet. Run model sweeps for this workflow first.")
        return

    workflow_exps = experiments[experiments["workflow"] == workflow]
    nodes = sorted(workflow_exps["node_id"].unique())
    exp_ids = workflow_exps["experiment_id"].tolist()
    runs = get_runs(exp_ids)

    if runs.empty:
        st.title(f"{workflow}")
        st.info("No runs found for this workflow.")
        return

    runs = runs.merge(
        workflow_exps[["experiment_id", "node_id"]],
        on="experiment_id",
        how="left",
    )

    # Title and filters in main content area
    st.title(f"{workflow}")

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_node = st.selectbox("Agent Node", nodes)
    with filter_col2:
        all_models = sorted(runs["params.model"].dropna().unique())
        selected_models = st.multiselect(
            "Models", all_models, default=all_models
        )

    runs = runs[runs["params.model"].isin(selected_models)]

    if runs.empty:
        st.info("No runs match filters.")
        return

    tab1, tab2, tab3, tab4 = st.tabs([
        "Model Comparison", "Cost Calculator", "Model Scorecard", "Run Details"
    ])

    scorer_cols = get_scorer_columns(runs)

    with tab1:
        render_model_comparison(runs, selected_node, scorer_cols)

    with tab2:
        render_cost_calculator(runs, nodes, model_pricing)

    with tab3:
        render_model_scorecard(runs, selected_node, scorer_cols, model_pricing, workflow_exps)

    with tab4:
        render_run_details(runs, workflow_exps)


def render_model_comparison(runs: pd.DataFrame, selected_node: str, scorer_cols: list):
    st.header("Model Comparison")

    node_runs = runs[runs["node_id"] == selected_node].copy()
    if node_runs.empty:
        st.info("No runs for this node.")
        return

    model_col = "params.model"
    provider_col = "params.provider"
    if model_col not in node_runs.columns:
        st.warning("Runs missing model parameter.")
        return

    agg_dict = {
        "num_runs": ("run_id", "count"),
        "avg_cost_usd": ("metrics.avg_estimated_cost_usd", "mean"),
        "avg_tokens": ("metrics.avg_total_tokens", "mean"),
    }
    for sc in scorer_cols:
        key = scorer_display_name(sc)
        agg_dict[key] = (sc, "mean")

    summary = node_runs.groupby([model_col, provider_col]).agg(**agg_dict).reset_index()
    summary.rename(columns={
        model_col: "Model",
        provider_col: "Provider",
        "num_runs": "Runs",
        "avg_cost_usd": "Avg Cost (USD)",
        "avg_tokens": "Avg Tokens",
    }, inplace=True)

    scorer_display_names = [scorer_display_name(sc) for sc in scorer_cols]

    # Build display with PASS/FAIL labels
    display_df = summary.copy()
    for name in scorer_display_names:
        display_df[name] = display_df[name].apply(pass_fail_label)

    format_map = {
        "Avg Cost (USD)": "${:.6f}",
        "Avg Tokens": "{:.0f}",
    }

    def apply_pass_fail_colors(row):
        styles = [""] * len(row)
        for i, col_name in enumerate(row.index):
            if col_name in scorer_display_names:
                val = row[col_name]
                if val == "PASS":
                    styles[i] = "background-color: #c6efce; color: #006100"
                elif val == "FAIL":
                    styles[i] = "background-color: #ffc7ce; color: #9c0006"
        return styles

    styled = display_df.style.format(format_map, na_rep="").apply(
        apply_pass_fail_colors, axis=1
    )

    st.dataframe(styled, width="stretch", hide_index=True)

    metric_options = ["Avg Cost (USD)"] + scorer_display_names
    chart_metric = st.selectbox(
        "Chart metric", metric_options, key="comparison_chart_metric"
    )

    if chart_metric in scorer_display_names:
        chart_source = summary.copy()
    else:
        chart_source = summary.copy()

    chart_data = chart_source.dropna(subset=[chart_metric])
    if not chart_data.empty:
        chart = alt.Chart(chart_data).mark_bar().encode(
            x=alt.X("Model:N", sort="-y"),
            y=alt.Y(f"{chart_metric}:Q", title=chart_metric),
            color="Model:N",
            tooltip=["Model", "Provider", chart_metric],
        ).properties(height=400)
        st.altair_chart(chart, width="stretch")


def render_cost_calculator(runs: pd.DataFrame, nodes: list, model_pricing: dict):
    st.header("Workflow Cost Calculator")
    st.caption(
        "Select a model for each agent node to estimate total workflow cost "
        "per execution."
    )

    model_col = "params.model"
    if model_col not in runs.columns:
        st.warning("No model data available.")
        return

    selections = {}
    cols = st.columns(min(len(nodes), 3))

    for i, node in enumerate(nodes):
        col = cols[i % len(cols)]
        node_runs = runs[runs["node_id"] == node]
        models = sorted(node_runs[model_col].dropna().unique())
        with col:
            selections[node] = st.selectbox(
                f"{node}", models, key=f"calc_{node}"
            )

    st.divider()

    rows = []
    total_cost = 0.0
    total_tokens = 0

    for node in nodes:
        model = selections[node]
        node_model_runs = runs[
            (runs["node_id"] == node) & (runs[model_col] == model)
        ]

        avg_cost = safe_metric(node_model_runs, "avg_estimated_cost_usd").mean()
        avg_tokens = safe_metric(node_model_runs, "avg_total_tokens").mean()

        if pd.isna(avg_tokens):
            avg_tokens = 0.0

        total_cost += avg_cost if pd.notna(avg_cost) else 0
        total_tokens += int(avg_tokens)

        cost_display = f"${avg_cost:.6f}" if pd.notna(avg_cost) else "N/A"
        rows.append({
            "Node": node,
            "Selected Model": model,
            "Avg Tokens": f"{avg_tokens:.0f}",
            "Avg Cost per Call": cost_display,
        })

    rows.append({
        "Node": "TOTAL",
        "Selected Model": "",
        "Avg Tokens": str(total_tokens),
        "Avg Cost per Call": f"${total_cost:.6f}",
    })

    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Cost per Execution", f"${total_cost:.6f}")
    with col2:
        exec_count = st.number_input(
            "Number of Executions", min_value=1, value=1, step=1, key="exec_count"
        )
        st.metric(
            f"Total Cost ({exec_count} executions)",
            f"${total_cost * exec_count:.4f}",
        )


def render_model_scorecard(
    runs: pd.DataFrame,
    selected_node: str,
    scorer_cols: list,
    model_pricing: dict,
    workflow_exps: pd.DataFrame = None,
):
    st.header("Model Scorecard")

    node_runs = runs[runs["node_id"] == selected_node].copy()
    if node_runs.empty:
        st.info("No runs for this node.")
        return

    model_col = "params.model"
    if model_col not in node_runs.columns:
        return

    scorer_names = [scorer_display_name(sc) for sc in scorer_cols]

    agg_dict = {
        "num_runs": ("run_id", "count"),
        "avg_cost": ("metrics.avg_estimated_cost_usd", "mean"),
    }
    for sc in scorer_cols:
        agg_dict[scorer_display_name(sc)] = (sc, "mean")

    summary = node_runs.groupby(model_col).agg(**agg_dict).reset_index()
    summary.rename(columns={model_col: "Model", "num_runs": "Runs"}, inplace=True)

    if scorer_names:
        summary["Overall Pass Rate"] = summary[scorer_names].mean(axis=1)
        total_dims = len(scorer_names)
        summary["_dims_passed"] = summary[scorer_names].apply(
            lambda row: int((row >= 0.5).sum()), axis=1
        )
        summary["Pass Rate"] = summary["_dims_passed"].apply(
            lambda v: f"{v}/{total_dims}"
        )
        summary["Cost per Passing Run"] = summary.apply(
            lambda r: r["avg_cost"] / r["Overall Pass Rate"]
            if pd.notna(r["avg_cost"]) and r["Overall Pass Rate"] > 0
            else float("nan"),
            axis=1,
        )

    # Recommendations
    if scorer_names and not summary.empty:
        col1, col2, col3 = st.columns(3)

        best_quality = summary.loc[summary["Overall Pass Rate"].idxmax()]
        with col1:
            dims_str = best_quality["Pass Rate"]
            st.metric(
                "Best Quality",
                best_quality["Model"],
                f"{dims_str} dimensions",
            )

        above_80 = summary[
            summary[scorer_names].min(axis=1) >= 0.8
        ]
        if not above_80.empty:
            best_value = above_80.loc[above_80["avg_cost"].idxmin()]
            with col2:
                st.metric(
                    "Best Value (all dims PASS)",
                    best_value["Model"],
                    f"${best_value['avg_cost']:.6f}/call",
                )
        else:
            with col2:
                st.metric("Best Value (all dims PASS)", "None qualify", "")

        above_60 = summary[
            summary[scorer_names].min(axis=1) >= 0.6
        ]
        if not above_60.empty:
            budget = above_60.loc[above_60["avg_cost"].idxmin()]
            with col3:
                st.metric(
                    "Budget Pick (all dims > 60%)",
                    budget["Model"],
                    f"${budget['avg_cost']:.6f}/call",
                )
        else:
            with col3:
                st.metric("Budget Pick (all dims > 60%)", "None qualify", "")

        st.divider()

    # Score matrix with PASS/FAIL
    display_cols = ["Model", "Runs"] + scorer_names
    if "Pass Rate" in summary.columns:
        display_cols.append("Pass Rate")
    display_cols.append("avg_cost")

    display_df = summary[display_cols].copy()
    display_df.rename(columns={"avg_cost": "Avg Cost (USD)"}, inplace=True)

    # Convert scorer columns to PASS/FAIL
    for name in scorer_names:
        display_df[name] = summary[name].apply(pass_fail_label)

    format_map = {"Avg Cost (USD)": "${:.6f}"}

    def apply_colors(row):
        styles = [""] * len(row)
        for i, col_name in enumerate(row.index):
            if col_name in scorer_names:
                val = row[col_name]
                if val == "PASS":
                    styles[i] = "background-color: #c6efce; color: #006100"
                elif val == "FAIL":
                    styles[i] = "background-color: #ffc7ce; color: #9c0006"
        return styles

    st.dataframe(
        display_df.style.format(format_map, na_rep="").apply(apply_colors, axis=1),
        width="stretch",
        hide_index=True,
    )

    # Horizontal bar chart (uses raw numeric values for chart)
    if scorer_names:
        chart_data = []
        for _, row in summary.iterrows():
            for sc in scorer_names:
                chart_data.append({
                    "Model": row["Model"],
                    "Dimension": sc,
                    "Score": row[sc] if pd.notna(row[sc]) else 0,
                    "Result": "PASS" if pd.notna(row[sc]) and row[sc] >= 0.5 else "FAIL",
                })
        chart_df = pd.DataFrame(chart_data)

        chart = alt.Chart(chart_df).mark_bar().encode(
            x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 1]), title="Score"),
            y=alt.Y("Model:N"),
            color=alt.Color(
                "Result:N",
                scale=alt.Scale(
                    domain=["PASS", "FAIL"],
                    range=["#2ecc71", "#e74c3c"],
                ),
            ),
            row=alt.Row("Dimension:N", title=""),
            tooltip=["Model", "Dimension", "Score", "Result"],
        ).properties(height=80, width=500)
        st.altair_chart(chart)

    # Judge Explanations per model
    if workflow_exps is not None:
        st.divider()
        st.subheader("Judge Explanations")

        model_col = "params.model"
        for model_name in sorted(node_runs[model_col].dropna().unique()):
            model_runs = node_runs[node_runs[model_col] == model_name]
            with st.expander(f"**{model_name}**"):
                for _, run_row in model_runs.iterrows():
                    run_id = run_row["run_id"]
                    exp_id = run_row["experiment_id"]
                    run_label = run_row.get("tags.mlflow.runName", run_id[:8])
                    assessments = get_trace_assessments(exp_id, run_id)
                    if not assessments:
                        continue
                    st.markdown(f"**{run_label}** (`{run_id[:8]}`)")
                    for a in assessments:
                        val = a["value"]
                        if val in ("yes", 1, 1.0, True):
                            badge = ":green[PASS]"
                        elif val in ("no", 0, 0.0, False):
                            badge = ":red[FAIL]"
                        else:
                            badge = f":orange[{val}]"
                        source_label = (
                            "LLM Judge"
                            if a["source_type"] == "LLM_JUDGE"
                            else "Code"
                        )
                        with st.expander(
                            f"{badge} **{a['scorer']}** ({source_label})"
                        ):
                            st.write(a["rationale"])
                    st.markdown("---")


def render_run_details(runs: pd.DataFrame, workflow_exps: pd.DataFrame):
    st.header("Run Details")

    if runs.empty:
        st.info("No runs available. Run some model sweeps first.")
        return

    if "tags.mlflow.runName" in runs.columns:
        name_col = "tags.mlflow.runName"
    else:
        name_col = "run_id"

    run_options = runs[[name_col, "run_id", "experiment_id"]].drop_duplicates()
    labels = [
        f"{row[name_col]} ({row['run_id'][:8]})"
        for _, row in run_options.iterrows()
    ]

    selected_idx = st.selectbox(
        "Select Run", range(len(labels)), format_func=lambda i: labels[i]
    )
    selected_row = run_options.iloc[selected_idx]
    selected_run_id = selected_row["run_id"]
    selected_exp_id = selected_row["experiment_id"]
    run_row = runs[runs["run_id"] == selected_run_id].iloc[0]

    col1, col2 = st.columns(2)

    scorer_cols = get_scorer_columns(runs)
    with col1:
        st.subheader("Quality Scores")
        if scorer_cols:
            for sc in scorer_cols:
                val = run_row.get(sc)
                if pd.notna(val):
                    label = pass_fail_label(val)
                    if label == "PASS":
                        st.markdown(
                            f":green[**PASS**] {scorer_display_name(sc)}"
                        )
                    else:
                        st.markdown(
                            f":red[**FAIL**] {scorer_display_name(sc)}"
                        )
        else:
            st.info("No scorer metrics found.")

    with col2:
        st.subheader("Cost & Tokens")
        cost_metrics = {}
        for c in runs.columns:
            if not c.startswith("metrics."):
                continue
            name = c.replace("metrics.", "")
            if "latency" in name:
                continue
            val = run_row[c]
            if pd.notna(val) and (
                "cost" in name or "token" in name
            ):
                if "cost" in name:
                    cost_metrics[name] = f"${val:.6f}"
                else:
                    cost_metrics[name] = f"{val:.0f}"
        if cost_metrics:
            st.json(cost_metrics)
        else:
            st.info("No cost/token data.")

        st.subheader("Parameters")
        param_cols = [c for c in runs.columns if c.startswith("params.")]
        param_data = {
            c.replace("params.", ""): run_row[c]
            for c in param_cols
            if pd.notna(run_row[c])
        }
        st.json(param_data)

    # Judge Explanations
    st.subheader("Judge Explanations")
    assessments = get_trace_assessments(selected_exp_id, selected_run_id)

    if assessments:
        for a in assessments:
            val = a["value"]
            if val in ("yes", 1, 1.0, True):
                icon = "PASS"
                color = "green"
            elif val in ("no", 0, 0.0, False):
                icon = "FAIL"
                color = "red"
            else:
                icon = str(val)
                color = "orange"

            source_label = (
                "LLM Judge" if a["source_type"] == "LLM_JUDGE" else "Code"
            )
            header = f":{color}[{icon}] **{a['scorer']}** ({source_label})"

            with st.expander(header):
                st.write(a["rationale"])
    else:
        st.info(
            "No judge explanations available. Run evaluations with judges enabled."
        )

    # Per-iteration token usage
    st.subheader("Per-Iteration Token Usage")
    try:
        client = mlflow.MlflowClient()
        input_history = client.get_metric_history(
            selected_run_id, "input_tokens"
        )
        output_history = client.get_metric_history(
            selected_run_id, "output_tokens"
        )
        cost_history = client.get_metric_history(
            selected_run_id, "estimated_cost_usd"
        )

        if input_history:
            iter_data = []
            for inp, out in zip(input_history, output_history):
                row = {
                    "Iteration": inp.step + 1,
                    "Input Tokens": int(inp.value),
                    "Output Tokens": int(out.value),
                    "Total Tokens": int(inp.value + out.value),
                }
                iter_data.append(row)

            if cost_history:
                for i, c in enumerate(cost_history):
                    if i < len(iter_data):
                        iter_data[i]["Cost (USD)"] = f"${c.value:.6f}"

            st.dataframe(
                pd.DataFrame(iter_data),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No per-iteration token data available for this run.")
    except Exception as e:
        st.info(f"Could not load iteration history: {e}")


if __name__ == "__main__":
    main()
