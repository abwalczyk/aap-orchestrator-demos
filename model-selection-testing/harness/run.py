"""AO Model Evaluation Harness.

Two modes:
  1. Model sweep: hold prompt constant, swap models, score each.
  2. Prompt sweep: hold model constant, swap prompts from MLflow Prompt Registry.

Usage:
    python3 run.py model-sweep --workflow ticket_classifier --step classify_bug
    python3 run.py model-sweep --workflow ticket_classifier --step classify_bug --model Qwen3.6-35B-A3B
    python3 run.py model-sweep --workflow ticket_classifier --step classify_bug --skip-judges
    python3 run.py prompt-sweep --workflow ticket_classifier --step classify_bug --prompt-name my_prompt
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
    experiment_name = config.get("mlflow", {}).get("experiment_name", "ao-model-comparison")
    mlflow.set_experiment(experiment_name)
    print(f"MLflow tracking: {mlflow_uri} (experiment: {experiment_name})")

    return config, ao


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
                  node_prompt: str = "", ground_truth: dict = None):
    """Create an MLflow run, log metrics and artifacts, run scorers."""
    with mlflow.start_run(run_name=run_name):
        for k, v in params.items():
            mlflow.log_param(k, v)

        latencies = [r["inputs"]["latency_ms"] for r in rows]
        error_count = sum(1 for r in rows if "error" in r["outputs"])
        mlflow.log_metric("avg_latency_ms", sum(latencies) / len(latencies))
        mlflow.log_metric("max_latency_ms", max(latencies))
        mlflow.log_metric("min_latency_ms", min(latencies))
        mlflow.log_metric("error_count", error_count)
        mlflow.log_metric("error_rate", error_count / len(rows))
        mlflow.log_metric("success_rate", 1 - (error_count / len(rows)))

        if node_prompt:
            mlflow.log_param("prompt_hash", hash(node_prompt) % (10**8))
            mlflow.log_text(node_prompt, "node_prompt.txt")
        if ground_truth:
            mlflow.log_text(json.dumps(ground_truth, indent=2), "ground_truth.json")

        genai_evaluate(data=rows, scorers=scorers)


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
                    workflow["workflow_id"], step["node_id"], model["name"]
                )
                node_prompt = get_node_prompt(ao, workflow["workflow_id"], step["node_id"])
                print(f"  Swapped to {model['name']}")
            except Exception as e:
                print(f"  Failed to swap model: {e}")
                continue

            try:
                results = []
                for i in range(iterations):
                    print(f"    Iteration {i + 1}/{iterations}...", end=" ", flush=True)
                    r = run_node(ao, workflow["workflow_id"], step, ground_truth)
                    print(f"status={r['status']} latency={r['latency_ms']:.0f}ms")
                    results.append(r)

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
            for i in range(iterations):
                print(f"    Iteration {i + 1}/{iterations}...", end=" ", flush=True)
                r = run_node(ao, workflow["workflow_id"], step, ground_truth)
                print(f"status={r['status']} latency={r['latency_ms']:.0f}ms")
                results.append(r)

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
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AO Model Evaluation Harness")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # Shared arguments
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--workflow", required=True, help="Workflow name from config")
    shared.add_argument("--step", required=True, help="Step name within the workflow")
    shared.add_argument("--model", default="all", help="Model name or 'all'")
    shared.add_argument("--iterations", type=int, default=None)
    shared.add_argument("--skip-judges", action="store_true",
                        help="Skip LLM judges, run programmatic scorers only")
    shared.add_argument("--config", default="config.yaml")

    # Model sweep
    subparsers.add_parser("model-sweep", parents=[shared],
                          help="Swap models, hold prompt constant")

    # Prompt sweep
    ps = subparsers.add_parser("prompt-sweep", parents=[shared],
                               help="Swap prompts from MLflow, hold model constant")
    ps.add_argument("--prompt-name", required=True,
                    help="Name of the prompt in MLflow Prompt Registry")

    args = parser.parse_args()

    if args.mode == "model-sweep":
        model_sweep(args)
    elif args.mode == "prompt-sweep":
        prompt_sweep(args)


if __name__ == "__main__":
    main()
