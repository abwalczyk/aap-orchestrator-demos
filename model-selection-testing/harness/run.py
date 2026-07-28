"""AO Model Evaluation Harness.

Three modes:
  1. Model sweep: hold prompt constant, swap models on one node, score each.
  2. Prompt sweep: hold model constant, swap prompts from MLflow Prompt Registry.
  3. Workflow sweep: run full workflow end-to-end, swap models on all agentic nodes.

Usage:
    python3 run.py model-sweep --workflow ticket_classifier --step classify_bug
    python3 run.py model-sweep --workflow ticket_classifier --step classify_bug --model claude-sonnet-4-6
    python3 run.py model-sweep --workflow ticket_classifier --step classify_bug --skip-judges
    python3 run.py prompt-sweep --workflow ticket_classifier --step classify_bug --prompt-name my_prompt
    python3 run.py workflow-sweep --workflow ticket_classifier
    python3 run.py workflow-sweep --workflow ticket_classifier --model claude-sonnet-4-6 --iterations 1
"""

import argparse
import json
import os
import sys
from pathlib import Path

import mlflow
import yaml
from dotenv import load_dotenv
from mlflow.genai import evaluate as genai_evaluate

from ao_client import AOClient
from scorers import build_programmatic_scorers, build_llm_judges
from tokens import compute_token_metrics


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_ground_truth(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def extract_output(response: dict) -> dict:
    """Parse structured output from an AO node response."""
    result = response.get("result", {})
    content = result.get("content", response)
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content.strip())
        except (json.JSONDecodeError, ValueError):
            pass
    return {"raw_content": str(content)[:500]}


def setup(config_path: str) -> tuple:
    """Load config, connect to AO, connect to MLflow. Returns (config, ao)."""
    load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
    config = load_config(Path(__file__).parent / config_path)

    ao_base = os.getenv("AO_BASE_URL")
    ao_user = os.getenv("AO_USERNAME")
    ao_pass = os.getenv("AO_PASSWORD")
    if not all([ao_base, ao_user, ao_pass]):
        print("Error: AO_BASE_URL, AO_USERNAME, AO_PASSWORD required in .env")
        sys.exit(1)

    ao = AOClient(ao_base)
    ao.login(ao_user, ao_pass)
    print(f"Connected to AO at {ao_base}")

    mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5001")
    mlflow.set_tracking_uri(mlflow_uri)
    print(f"MLflow tracking: {mlflow_uri}")

    return config, ao


def resolve_workflow(config: dict, workflow_name: str) -> dict:
    """Look up a workflow by name from config. Returns the workflow dict."""
    for w in config["workflows"]:
        if w["name"] == workflow_name:
            return w
    print(f"Error: workflow '{workflow_name}' not found in config")
    print(f"Available: {[w['name'] for w in config['workflows']]}")
    sys.exit(1)


def resolve_step(config: dict, workflow_name: str, step_name: str) -> tuple:
    """Look up the workflow and step from config. Returns (workflow, step)."""
    workflow = None
    for w in config["workflows"]:
        if w["name"] == workflow_name:
            workflow = w
            break
    if not workflow:
        print(f"Error: workflow '{workflow_name}' not found in config")
        print(f"Available: {[w['name'] for w in config['workflows']]}")
        sys.exit(1)

    step = None
    for s in workflow["steps"]:
        if s["name"] == step_name:
            step = s
            break
    if not step:
        print(f"Error: step '{step_name}' not found in workflow '{workflow_name}'")
        print(f"Available: {[s['name'] for s in workflow['steps']]}")
        sys.exit(1)

    return workflow, step


def run_node(ao: AOClient, workflow_id: str, step: dict, ground_truth: dict) -> dict:
    """Execute a single node test against AO. Returns {output, latency_ms, status}."""
    try:
        response = ao.test_node(
            workflow_id=workflow_id,
            target_node_id=step["node_id"],
            trigger_inputs=ground_truth.get("trigger_inputs", {}),
            pre_resolved_nodes=ground_truth.get("pre_resolved_nodes", {}),
        )
        execution_id = response.get("id")
        if execution_id:
            execution = ao.poll_execution(execution_id)
            latency_ms = execution.get("_wall_clock_ms", 0)
            for activity in execution.get("activities", []):
                if activity.get("activity_id") == step["node_id"]:
                    status = activity.get("status", "unknown")
                    if status == "completed":
                        output = extract_output(activity.get("output_data", {}))
                    else:
                        output = {"error": activity.get("error_details", status)}
                    return {"output": output, "latency_ms": latency_ms, "status": status}
            return {"output": {"error": "node not found in activities"}, "latency_ms": latency_ms, "status": "unknown"}
        else:
            return {
                "output": extract_output(response),
                "latency_ms": response.get("_latency_ms", 0),
                "status": "direct",
            }
    except Exception as e:
        return {"output": {"error": str(e)}, "latency_ms": 0, "status": "error"}


def build_rows(results: list, step: dict, workflow_name: str,
               model_name: str, provider: str, ground_truth: dict,
               extra_params: dict = None) -> list:
    """Format AO results into MLflow evaluation rows."""
    rows = []
    for i, r in enumerate(results):
        row = {
            "inputs": {
                "workflow": workflow_name,
                "step": step["name"],
                "node_id": step["node_id"],
                "model": model_name,
                "provider": provider,
                "iteration": i + 1,
                "latency_ms": r["latency_ms"],
                "trigger_inputs": ground_truth.get("trigger_inputs", {}),
            },
            "outputs": r["output"] if isinstance(r["output"], dict) else {"raw": str(r["output"])},
            "expectations": {
                "expected_response": json.dumps(ground_truth.get("expected_output", {})),
            },
        }
        if extra_params:
            row["inputs"].update(extra_params)
        rows.append(row)
    return rows


def log_to_mlflow(rows: list, run_name: str, params: dict, scorers: list,
                  node_prompt: str = "", ground_truth: dict = None,
                  token_metrics: list = None):
    """Create an MLflow run, log metrics and artifacts, run scorers."""
    with mlflow.start_run(run_name=run_name):
        for k, v in params.items():
            mlflow.log_param(k, v)

        tag_keys = {"model", "provider", "step", "mode"}
        for k in tag_keys:
            if k in params:
                mlflow.set_tag(k, params[k])

        latencies = [r["inputs"]["latency_ms"] for r in rows]
        error_count = sum(1 for r in rows if "error" in r["outputs"])
        mlflow.log_metric("avg_latency_ms", sum(latencies) / len(latencies))
        mlflow.log_metric("max_latency_ms", max(latencies))
        mlflow.log_metric("min_latency_ms", min(latencies))
        mlflow.log_metric("error_count", error_count)
        mlflow.log_metric("error_rate", error_count / len(rows))
        mlflow.log_metric("success_rate", 1 - (error_count / len(rows)))

        if token_metrics:
            all_input = [t["input_tokens"] for t in token_metrics]
            all_output = [t["output_tokens"] for t in token_metrics]
            all_total = [t["total_tokens"] for t in token_metrics]
            mlflow.log_metric("avg_input_tokens", sum(all_input) / len(all_input))
            mlflow.log_metric("avg_output_tokens", sum(all_output) / len(all_output))
            mlflow.log_metric("avg_total_tokens", sum(all_total) / len(all_total))

            costs = [t["estimated_cost"] for t in token_metrics
                     if t["estimated_cost"] is not None]
            if costs:
                mlflow.log_metric("avg_estimated_cost_usd", sum(costs) / len(costs))
                mlflow.log_metric("total_estimated_cost_usd", sum(costs))

            for i, t in enumerate(token_metrics):
                mlflow.log_metric("input_tokens", t["input_tokens"], step=i)
                mlflow.log_metric("output_tokens", t["output_tokens"], step=i)
                mlflow.log_metric("total_tokens", t["total_tokens"], step=i)
                if t["estimated_cost"] is not None:
                    mlflow.log_metric("estimated_cost_usd", t["estimated_cost"], step=i)

        if node_prompt:
            mlflow.log_param("prompt_hash", hash(node_prompt) % (10**8))
            mlflow.log_text(node_prompt, "node_prompt.txt")
        if ground_truth:
            mlflow.log_text(json.dumps(ground_truth, indent=2), "ground_truth.json")

        run_id = mlflow.active_run().info.run_id
        genai_evaluate(data=rows, scorers=scorers)

        traces = mlflow.search_traces(
            run_id=run_id, return_type="list", include_spans=False
        )
        trace_tags = {k: v for k, v in params.items()
                      if k in ("model", "provider", "step", "mode", "node_id")}
        for i, t in enumerate(traces):
            for tk, tv in trace_tags.items():
                mlflow.set_trace_tag(t.info.trace_id, tk, str(tv))
            if token_metrics and i < len(token_metrics):
                tm = token_metrics[i]
                mlflow.set_trace_tag(t.info.trace_id, "input_tokens", str(tm["input_tokens"]))
                mlflow.set_trace_tag(t.info.trace_id, "output_tokens", str(tm["output_tokens"]))
                mlflow.set_trace_tag(t.info.trace_id, "total_tokens", str(tm["total_tokens"]))
                if tm["estimated_cost"] is not None:
                    mlflow.set_trace_tag(t.info.trace_id, "estimated_cost_usd", f"{tm['estimated_cost']:.6f}")


def get_node_prompt(ao: AOClient, workflow_id: str, node_id: str) -> str:
    """Fetch the current system prompt from a workflow node."""
    workflow = ao.get_workflow(workflow_id)
    definition = workflow.get("version", {}).get("workflow_definition", {})
    for node in definition.get("nodes", []):
        if node.get("id") == node_id:
            return node.get("config", {}).get("system_prompt", "")
    return ""


# ---------------------------------------------------------------------------
# Mode 1: Model Sweep
# ---------------------------------------------------------------------------

def model_sweep(args):
    """Hold prompt constant, swap models, score each on one step."""
    config, ao = setup(args.config)
    iterations = args.iterations or config.get("iterations", 3)
    judge_config = config.get("judges", {})
    gt_base = Path(__file__).parent.parent

    workflow, step = resolve_step(config, args.workflow, args.step)
    print(f"\nWorkflow: {workflow['name']}, Step: {step['name']} ({step['node_id']})")

    prefix = config.get("mlflow", {}).get("experiment_prefix", "ao-eval")
    experiment_path = f"{prefix}/{workflow['name']}/{step['node_id']}"
    mlflow.set_experiment(experiment_path)
    mlflow.set_experiment_tag("workflow_type", workflow["name"])
    print(f"MLflow experiment: {experiment_path}")

    gt_path = gt_base / step["ground_truth"]
    if not gt_path.exists():
        print(f"Error: ground truth not found: {gt_path}")
        sys.exit(1)
    ground_truth = load_ground_truth(gt_path)

    models = config["models"]
    if args.model != "all":
        models = [m for m in models if m["name"] == args.model]
        if not models:
            print(f"Error: model '{args.model}' not found in config")
            print(f"Available: {[m['name'] for m in config['models']]}")
            sys.exit(1)

    print(f"Testing {len(models)} model(s), {iterations} iteration(s) each\n")

    try:
        for model in models:
            print(f"  Model: {model['name']} ({model['provider']})")

            try:
                original_def = ao.swap_model(
                    workflow["workflow_id"], step["node_id"], model["name"],
                    credential_id=model.get("credential_id"),
                )
                node_prompt = get_node_prompt(ao, workflow["workflow_id"], step["node_id"])
                print(f"  Swapped to {model['name']}")
            except Exception as e:
                print(f"  Failed to swap model: {e}")
                continue

            try:
                results = []
                token_data = []
                for i in range(iterations):
                    print(f"    Iteration {i + 1}/{iterations}...", end=" ", flush=True)
                    r = run_node(ao, workflow["workflow_id"], step, ground_truth)
                    print(f"status={r['status']} latency={r['latency_ms']:.0f}ms")
                    results.append(r)
                    token_data.append(compute_token_metrics(
                        node_prompt, ground_truth.get("trigger_inputs", {}),
                        r["output"], model,
                    ))

                rows = build_rows(results, step, workflow["name"],
                                  model["name"], model["provider"], ground_truth)

                scorers = build_programmatic_scorers()
                if not args.skip_judges:
                    scorers.extend(build_llm_judges(judge_config, model["provider"]))

                run_name = f"{workflow['name']}/{step['name']}/{model['name']}"
                try:
                    log_to_mlflow(
                        rows, run_name,
                        params={
                            "mode": "model_sweep",
                            "workflow": workflow["name"],
                            "step": step["name"],
                            "model": model["name"],
                            "provider": model["provider"],
                            "iterations": len(rows),
                        },
                        scorers=scorers,
                        node_prompt=node_prompt,
                        ground_truth=ground_truth,
                        token_metrics=token_data,
                    )
                    mode_label = "programmatic only" if args.skip_judges else "all scorers"
                    print(f"  Logged to MLflow ({mode_label})")
                except Exception as e:
                    print(f"  MLflow logging failed: {e}")
            finally:
                ao.restore_workflow(workflow["workflow_id"], original_def)
                print(f"  Restored original workflow\n")
    finally:
        ao.close()

    print("Done. View results in MLflow.")


# ---------------------------------------------------------------------------
# Mode 2: Prompt Sweep
# ---------------------------------------------------------------------------

def prompt_sweep(args):
    """Hold model constant, swap prompts from MLflow Prompt Registry on one step."""
    config, ao = setup(args.config)
    iterations = args.iterations or config.get("iterations", 3)
    judge_config = config.get("judges", {})
    gt_base = Path(__file__).parent.parent

    workflow, step = resolve_step(config, args.workflow, args.step)
    print(f"\nWorkflow: {workflow['name']}, Step: {step['name']} ({step['node_id']})")

    prefix = config.get("mlflow", {}).get("experiment_prefix", "ao-eval")
    experiment_path = f"{prefix}/{workflow['name']}/{step['node_id']}"
    mlflow.set_experiment(experiment_path)
    mlflow.set_experiment_tag("workflow_type", workflow["name"])
    print(f"MLflow experiment: {experiment_path}")

    gt_path = gt_base / step["ground_truth"]
    if not gt_path.exists():
        print(f"Error: ground truth not found: {gt_path}")
        sys.exit(1)
    ground_truth = load_ground_truth(gt_path)

    prompt_name = args.prompt_name

    from mlflow import MlflowClient
    client = MlflowClient()

    prompt_versions = []
    try:
        all_versions = client.search_model_versions(filter_string=f"name='{prompt_name}'")
        for v in all_versions:
            prompt_text = v.description or ""
            if hasattr(v, "source") and v.source:
                try:
                    artifact = client.download_artifacts(v.run_id, v.source) if v.run_id else None
                    if artifact:
                        with open(artifact) as f:
                            prompt_text = f.read()
                except Exception:
                    pass
            prompt_versions.append({
                "version": v.version,
                "text": prompt_text,
                "tags": dict(v.tags) if v.tags else {},
            })
    except Exception:
        try:
            for version_num in range(1, 100):
                try:
                    prompt = mlflow.load_prompt(f"prompts:/{prompt_name}/{version_num}")
                    prompt_versions.append({
                        "version": str(version_num),
                        "text": prompt.template if hasattr(prompt, "template") else str(prompt),
                        "tags": {},
                    })
                except Exception:
                    break
        except Exception as e:
            print(f"Error loading prompt versions: {e}")
            sys.exit(1)

    if not prompt_versions:
        print(f"No versions found for prompt '{prompt_name}'")
        sys.exit(1)

    print(f"Found {len(prompt_versions)} prompt version(s) for '{prompt_name}'")

    # Use first model in config, or the one specified
    models = config["models"]
    if args.model != "all":
        models = [m for m in models if m["name"] == args.model]
    model = models[0]
    print(f"Using model: {model['name']} ({model['provider']})")

    try:
        original_def = ao.swap_model(
            workflow["workflow_id"], step["node_id"], model["name"]
        )
    except Exception as e:
        print(f"Failed to set model: {e}")
        ao.close()
        sys.exit(1)

    try:
        for pv in prompt_versions:
            print(f"\n  Prompt v{pv['version']}: {pv['text'][:60]}...")

            try:
                ao.swap_prompt(
                    workflow["workflow_id"], step["node_id"], pv["text"]
                )
            except Exception as e:
                print(f"    Failed to swap prompt: {e}")
                continue

            results = []
            token_data = []
            for i in range(iterations):
                print(f"    Iteration {i + 1}/{iterations}...", end=" ", flush=True)
                r = run_node(ao, workflow["workflow_id"], step, ground_truth)
                print(f"status={r['status']} latency={r['latency_ms']:.0f}ms")
                results.append(r)
                token_data.append(compute_token_metrics(
                    pv["text"], ground_truth.get("trigger_inputs", {}),
                    r["output"], model,
                ))

            rows = build_rows(
                results, step, workflow["name"],
                model["name"], model["provider"], ground_truth,
                extra_params={"prompt_version": pv["version"]},
            )

            scorers = build_programmatic_scorers()
            if not args.skip_judges:
                scorers.extend(build_llm_judges(judge_config, model["provider"]))

            run_name = f"{workflow['name']}/{step['name']}/prompt-v{pv['version']}"
            try:
                log_to_mlflow(
                    rows, run_name,
                    params={
                        "mode": "prompt_sweep",
                        "workflow": workflow["name"],
                        "step": step["name"],
                        "model": model["name"],
                        "provider": model["provider"],
                        "prompt_name": prompt_name,
                        "prompt_version": pv["version"],
                        "iterations": len(rows),
                    },
                    scorers=scorers,
                    node_prompt=pv["text"],
                    ground_truth=ground_truth,
                    token_metrics=token_data,
                )
                mode_label = "programmatic only" if args.skip_judges else "all scorers"
                print(f"    Logged to MLflow ({mode_label})")
            except Exception as e:
                print(f"    MLflow logging failed: {e}")
    finally:
        ao.restore_workflow(workflow["workflow_id"], original_def)
        print(f"\n  Restored original workflow")
        ao.close()

    print("Done. View results in MLflow.")


# ---------------------------------------------------------------------------
# Mode 3: Workflow Sweep
# ---------------------------------------------------------------------------

def workflow_sweep(args):
    """Run the full workflow end-to-end, swap models across all agentic nodes."""
    config, ao = setup(args.config)
    iterations = args.iterations or config.get("iterations", 3)
    judge_config = config.get("judges", {})
    gt_base = Path(__file__).parent.parent
    prefix = config.get("mlflow", {}).get("experiment_prefix", "ao-eval")

    workflow = resolve_workflow(config, args.workflow)
    print(f"\nWorkflow: {workflow['name']} (full workflow sweep)")

    steps_by_node = {}
    ground_truths = {}
    for step in workflow["steps"]:
        node_id = step["node_id"]
        if node_id not in steps_by_node:
            steps_by_node[node_id] = []
        steps_by_node[node_id].append(step)

        gt_path = gt_base / step["ground_truth"]
        if gt_path.exists():
            ground_truths[step["name"]] = load_ground_truth(gt_path)

    trigger_inputs = {}
    for step in workflow["steps"]:
        gt = ground_truths.get(step["name"], {})
        if gt.get("trigger_inputs"):
            trigger_inputs = gt["trigger_inputs"]
            break

    models = config["models"]
    if args.model != "all":
        models = [m for m in models if m["name"] == args.model]
        if not models:
            print(f"Error: model '{args.model}' not found in config")
            print(f"Available: {[m['name'] for m in config['models']]}")
            sys.exit(1)

    print(f"Testing {len(models)} model(s), {iterations} iteration(s) each")
    print(f"Trigger input: {json.dumps(trigger_inputs)[:100]}\n")

    try:
        for model in models:
            print(f"  Model: {model['name']} ({model['provider']})")

            try:
                original_def = ao.swap_all_agentic_models(
                    workflow["workflow_id"], model["name"],
                    credential_id=model.get("credential_id"),
                )
                print(f"  Swapped all agentic nodes to {model['name']}")
            except Exception as e:
                print(f"  Failed to swap models: {e}")
                continue

            try:
                for i in range(iterations):
                    print(f"    Iteration {i + 1}/{iterations}...", flush=True)

                    try:
                        response = ao.run_workflow(
                            workflow["workflow_id"], trigger_inputs
                        )
                        execution = ao.poll_execution(response["id"])
                        wall_clock = execution.get("_wall_clock_ms", 0)
                        print(f"    Workflow completed in {wall_clock:.0f}ms")
                    except Exception as e:
                        print(f"    Workflow execution failed: {e}")
                        continue

                    for activity in execution.get("activities", []):
                        node_id = activity.get("activity_id")
                        status = activity.get("status")

                        if node_id not in steps_by_node:
                            continue
                        if status == "skipped":
                            print(f"      {node_id}: skipped (not on active path)")
                            continue
                        if status != "completed":
                            print(f"      {node_id}: {status}")
                            continue

                        step = steps_by_node[node_id][0]
                        gt = ground_truths.get(step["name"], {})
                        output = extract_output(activity.get("output_data", {}))

                        result = {
                            "output": output,
                            "latency_ms": wall_clock,
                            "status": status,
                        }
                        rows = build_rows(
                            [result], step, workflow["name"],
                            model["name"], model["provider"], gt,
                        )

                        experiment_path = f"{prefix}/{workflow['name']}/{node_id}"
                        mlflow.set_experiment(experiment_path)
                        mlflow.set_experiment_tag("workflow_type", workflow["name"])

                        scorers = build_programmatic_scorers()
                        if not args.skip_judges:
                            scorers.extend(
                                build_llm_judges(judge_config, model["provider"])
                            )

                        node_prompt = get_node_prompt(
                            ao, workflow["workflow_id"], node_id
                        )
                        token_data = [compute_token_metrics(
                            node_prompt, gt.get("trigger_inputs", {}),
                            output, model,
                        )]
                        run_name = (
                            f"{workflow['name']}/{step['name']}/{model['name']}"
                        )

                        try:
                            log_to_mlflow(
                                rows, run_name,
                                params={
                                    "mode": "workflow_sweep",
                                    "workflow": workflow["name"],
                                    "step": step["name"],
                                    "node_id": node_id,
                                    "model": model["name"],
                                    "provider": model["provider"],
                                    "iterations": 1,
                                },
                                scorers=scorers,
                                node_prompt=node_prompt,
                                ground_truth=gt,
                                token_metrics=token_data,
                            )
                            print(f"      {node_id}: logged to MLflow")
                        except Exception as e:
                            print(f"      {node_id}: MLflow logging failed: {e}")
            finally:
                ao.restore_workflow(workflow["workflow_id"], original_def)
                print(f"  Restored original workflow\n")
    finally:
        ao.close()

    print("Done. View results in MLflow.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AO Model Evaluation Harness")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # Shared arguments for node-level modes (model-sweep, prompt-sweep)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--workflow", required=True, help="Workflow name from config")
    shared.add_argument("--step", required=True, help="Step name within the workflow")
    shared.add_argument("--model", default="all", help="Model name or 'all'")
    shared.add_argument("--iterations", type=int, default=None)
    shared.add_argument("--skip-judges", action="store_true",
                        help="Skip LLM judges, run programmatic scorers only")
    shared.add_argument("--config", default="config.yaml")

    # Shared arguments for workflow-level modes (no --step)
    shared_workflow = argparse.ArgumentParser(add_help=False)
    shared_workflow.add_argument("--workflow", required=True,
                                 help="Workflow name from config")
    shared_workflow.add_argument("--model", default="all",
                                 help="Model name or 'all'")
    shared_workflow.add_argument("--iterations", type=int, default=None)
    shared_workflow.add_argument("--skip-judges", action="store_true",
                                 help="Skip LLM judges, run programmatic scorers only")
    shared_workflow.add_argument("--config", default="config.yaml")

    # Model sweep
    subparsers.add_parser("model-sweep", parents=[shared],
                          help="Swap models, hold prompt constant")

    # Prompt sweep
    ps = subparsers.add_parser("prompt-sweep", parents=[shared],
                               help="Swap prompts from MLflow, hold model constant")
    ps.add_argument("--prompt-name", required=True,
                    help="Name of the prompt in MLflow Prompt Registry")

    # Workflow sweep
    subparsers.add_parser("workflow-sweep", parents=[shared_workflow],
                          help="Run full workflow end-to-end, swap models on all nodes")

    args = parser.parse_args()

    if args.mode == "model-sweep":
        model_sweep(args)
    elif args.mode == "prompt-sweep":
        prompt_sweep(args)
    elif args.mode == "workflow-sweep":
        workflow_sweep(args)


if __name__ == "__main__":
    main()
