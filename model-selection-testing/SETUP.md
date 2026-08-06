# MLflow Integration Setup Complete

## What Was Configured

### MLflow Deployment on OpenShift
Deployed MLflow 3.9.0rc0 to the OpenShift cluster at `https://api.cluster-mlphp-1.dyn.redhatworkshops.io:6443`

**Components:**
- **MLflow Server** - Tracking server with web UI
- **PostgreSQL** - Backend metadata store
- **MinIO** - S3-compatible artifact storage

**Access URLs:**
- MLflow UI: https://mlflow-route-mlflow.apps.cluster-mlphp-1.dyn.redhatworkshops.io
- MinIO Console: https://mlflow-minio-console-mlflow.apps.cluster-mlphp-1.dyn.redhatworkshops.io
- Internal URI (from within cluster): http://mlflow-service.mlflow.svc.cluster.local:5000

### Harness Configuration
The testing harness in `model-selection-testing/` has been configured to connect to:

1. **Automation Orchestrator (AO)**
   - URL: set `AO_BASE_URL` in `.env` (local only; do not commit)
   - Credentials: set `AO_USERNAME` / `AO_PASSWORD` in `.env` (do not commit)

2. **MLflow**
   - Tracking URI: set `MLFLOW_TRACKING_URI` in `.env`

Copy from `.env.example` if present, then fill in local values. Never commit real credentials.

## How It Works

The harness (`harness/run.py`) will:

1. **Connect to AO** - Authenticate and fetch workflow definitions
2. **Swap models** - Change which LLM a workflow node uses
3. **Run tests** - Execute workflows with different models/prompts
4. **Score results** - Evaluate outputs using programmatic and LLM judges
5. **Log to MLflow** - Send all metrics, scores, and artifacts to MLflow

All evaluation results will appear in MLflow under the experiment prefix `atrotter-testing/`.

## Usage

### Quick Test (Programmatic Scorers Only)

```bash
cd harness
source ../venv/bin/activate

# Test one model on one step without LLM judges
python3 run.py model-sweep \
  --workflow ticket_classifier \
  --step classify_bug \
  --model claude-sonnet-4-6 \
  --iterations 1 \
  --skip-judges
```

### Full Model Comparison

```bash
# Compare all models on all scenarios for one step (3 iterations each)
python3 run.py model-sweep \
  --workflow ticket_classifier \
  --step classify_bug
```

### End-to-End Workflow Test

```bash
# Run full workflow with all nodes, compare models
python3 run.py workflow-sweep \
  --workflow ticket_classifier
```

## Viewing Results in MLflow

1. Open the MLflow UI: https://mlflow-route-mlflow.apps.cluster-mlphp-1.dyn.redhatworkshops.io
2. Navigate to the experiment for your workflow/node (e.g., `atrotter-testing/ticket_classifier/classifier`)
3. Compare runs by:
   - Sorting by `model` tag
   - Viewing metrics (latency, error rate, success rate)
   - Checking LLM judge scores in the Traces tab
   - Comparing response quality across models

## What Gets Logged

For each test run, MLflow receives:
- **Metrics**: avg/min/max latency, error rate, success rate
- **Tags**: model, provider, step, mode, workflow
- **Artifacts**: prompt text, ground truth inputs/outputs
- **Traces**: Full request/response with LLM judge evaluations
- **Scores**: Programmatic scores (schema compliance, latency) and LLM judge scores (correctness, completeness, safety, guidelines)

## Next Steps

1. **Add LLM Judge Credentials**: Edit `.env` and add your `OPENAI_API_KEY` and `OPENAI_BASE_URL` to enable LLM judges
2. **Update Workflow IDs**: In `harness/config.yaml`, replace the workflow_id and node_id placeholders with your actual AO workflow UUIDs
3. **Run Tests**: Execute model sweeps or workflow sweeps as shown above
4. **Analyze Results**: Use the MLflow UI to compare model performance

## Troubleshooting

### SSL Certificate Errors
If you get SSL errors connecting to MLflow from the harness, you can:

Option 1: Set environment variable to skip SSL verification (development only):
```bash
export MLFLOW_TRACKING_INSECURE_TLS=true
```

Option 2: Use the internal cluster URL if running from a pod in the cluster:
```bash
MLFLOW_TRACKING_URI=http://mlflow-service.mlflow.svc.cluster.local:5000
```

### Connection Timeouts
The harness polls AO for execution results. If workflows take a long time, you may need to increase timeouts in `ao_client.py`.

### MLflow Experiment Not Found
On first run, MLflow will automatically create experiments. If you see "Experiment not found" errors, the harness will create them.
