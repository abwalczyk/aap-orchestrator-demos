# AO Model Selection Testing Harness

A testing harness that evaluates LLM performance in Automation Orchestrator (AO) workflows. It swaps models or prompts into AO agent nodes, runs them against known inputs, scores the responses, and logs everything to MLflow for comparison.

## What It Does

The harness has three jobs:

1. **Calls AO** to run an agent node with a specific model and input
2. **Scores the response** using programmatic checks and LLM judges
3. **Logs results to MLflow** so you can compare models side by side

## Two Modes

**Model sweep**: Hold the prompt constant, swap models. Tests how different LLMs perform on the same task.

```bash
python3 run.py model-sweep --workflow ticket_classifier --step classify_bug
```

**Prompt sweep**: Hold the model constant, swap prompts from MLflow Prompt Registry. Tests how different prompt versions affect the same model's output.

```bash
python3 run.py prompt-sweep --workflow ticket_classifier --step classify_bug --prompt-name my_prompt
```

## Project Structure

```
model-selection-testing/
  .env                  # Your credentials (gitignored, copy from .env.example)
  .env.example          # Template showing required environment variables
  harness/
    run.py              # Main entry point, orchestrates everything
    ao_client.py        # Talks to the AO REST API
    scorers.py          # Defines how responses get graded
    config.yaml         # Models, workflows, and judge configuration
  ground_truth/         # Expected inputs/outputs for each test case
    ticket_classifier/
    ticket_enrichment/
    cert_rotation/
    ...
```

## How the Files Work Together

### ao_client.py

Handles all communication with AO's REST API. Six methods:

- `login()` authenticates and stores a JWT token
- `get_workflow()` fetches a workflow definition by UUID
- `swap_model()` changes which LLM an agent node uses (returns the original so it can be restored)
- `swap_prompt()` changes an agent node's system prompt (same pattern as swap_model)
- `test_node()` fires a per-step test via `POST /api/v1/workflows/{id}/test` and measures latency
- `restore_workflow()` puts the workflow back to its original state after testing

### scorers.py

Defines how agent responses get graded. Two categories:

**Programmatic scorers** (always run, no API key needed):
- `SchemaComplianceScorer`: Are all expected JSON fields present in the response?
- `LatencyThresholdScorer`: Was the response time within acceptable limits?
- `ResponseQualityScorer`: Basic checks (non-empty, no errors, structured output, has content)

**LLM judges** (need an LLM endpoint, skipped with `--skip-judges`):
- `Correctness` (MLflow built-in): Did the model correctly identify the issue, systems, severity?
- `Completeness` (MLflow built-in): Did it address all elements of the prompt?
- `Safety` (MLflow built-in): Did it flag risks, include rollback steps?
- `Guidelines("routing")`: Did it choose the correct path with consistent rationale?
- `Guidelines("actionability")`: Is the output specific, schema-conformant, and usable?
- `Guidelines("tool_usage")`: Were correct tools called with no hallucinated results?

The judges are all MLflow's built-in scorers. The `build_llm_judges()` function just points them at the right LLM endpoint and handles cross-model judging (Claude judges non-Anthropic models, GPT judges Anthropic models, so a model never grades itself).

### run.py

The orchestrator. Here's what each function does:

- `load_config()` / `load_ground_truth()`: File loaders for YAML config and JSON ground truth
- `extract_output()`: Parses AO's response (AO returns the model's answer as a JSON string inside `result.content`, this strips whitespace and parses it into a dict)
- `setup()`: Connects to AO and MLflow, loads the config
- `resolve_step()`: Looks up the workflow and step you specified on the command line, finds the matching entry in config.yaml with the workflow ID and node ID
- `run_node()`: The core execution function. Calls `ao.test_node()` to launch the test, `ao.poll_execution()` to wait for it to finish, finds the right node's output, and calls `extract_output()` to parse it
- `build_rows()`: Formats results into the shape MLflow's `genai_evaluate()` expects (inputs, outputs, expectations)
- `log_to_mlflow()`: Creates an MLflow run, logs latency metrics (avg/min/max, error rate, success rate), saves artifacts (prompt text, ground truth), and runs all scorers
- `get_node_prompt()`: Fetches the current system prompt from a node in AO
- `model_sweep()` / `prompt_sweep()`: The two modes (see below)
- `main()`: Parses CLI arguments and calls the right mode

### config.yaml

Lists which models to test, which workflows/steps exist (with workflow IDs, node IDs, and ground truth file paths), which LLM to use as the judge, and the MLflow experiment name.

### ground_truth/

JSON files that define the test cases. Each file contains:
- `trigger_inputs`: What gets sent to the workflow (e.g. a user message)
- `pre_resolved_nodes`: Mocked outputs from earlier nodes (for testing downstream steps)
- `expected_output`: The correct response to compare against

## Call Flow

Here's what happens when you run a model sweep:

```
main()
  > model_sweep()
    > setup()                 # connect to AO + MLflow
    > resolve_step()          # find the workflow/step in config
    > load_ground_truth()     # load expected inputs/outputs
    > ao.swap_model()         # change the model on the AO node
    > run_node()              # run the test
        > ao.test_node()      # send inputs to AO
        > ao.poll_execution() # wait for the node to finish
        > extract_output()    # parse the JSON response
    > build_rows()            # format results for MLflow
    > log_to_mlflow()         # log metrics + run all scorers
    > ao.restore_workflow()   # put the original model back
```

This repeats for each model and each iteration.

## Setup

1. Copy the environment template and fill in your credentials:
   ```bash
   cp .env.example .env
   # Edit .env with your AO URL, username, password, MLflow URI, and LLM judge API key
   ```

2. Create a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install mlflow httpx pyyaml python-dotenv
   ```

3. Update `harness/config.yaml` with your workflow IDs and node IDs from AO.

## Usage

Run from the `harness/` directory with the venv activated:

```bash
cd harness
source ../venv/bin/activate
```

### Model sweep (compare models)

```bash
# Test all models on one step, 3 iterations each, with all judges
python3 run.py model-sweep --workflow ticket_classifier --step classify_bug

# Test one specific model
python3 run.py model-sweep --workflow ticket_classifier --step classify_bug --model Qwen3.6-35B-A3B

# Quick test, no LLM judges (programmatic scorers only)
python3 run.py model-sweep --workflow ticket_classifier --step classify_bug --model Qwen3.6-35B-A3B --iterations 1 --skip-judges
```

### Prompt sweep (compare prompts)

```bash
# Test all versions of a prompt from MLflow Prompt Registry
python3 run.py prompt-sweep --workflow ticket_classifier --step classify_bug --prompt-name my_classifier_prompt
```

### CLI flags

| Flag | Description |
|------|-------------|
| `--workflow` | (Required) Workflow name from config.yaml |
| `--step` | (Required) Step name within the workflow |
| `--model` | Test one model instead of all (default: all) |
| `--iterations` | How many times to repeat each test (default: 3 from config) |
| `--skip-judges` | Skip LLM judges, run programmatic scorers only |
| `--config` | Path to config file (default: config.yaml) |
| `--prompt-name` | (Prompt sweep only) Name of prompt in MLflow Prompt Registry |

## Viewing Results

Results are logged to your MLflow instance. Open the experiment in the MLflow UI to:

- Compare metrics (latency, error rate, success rate) across models
- View LLM judge assessments (pass/fail) under the Traces tab
- Click into individual traces to see the full input, output, and judge rationale
