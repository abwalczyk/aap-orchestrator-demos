# ✅ MLflow is Ready

## Access
**MLflow UI:** https://mlflow-route-mlflow.apps.cluster-p8mds-1.dyn.redhatworkshops.io

## What's Deployed (mlflow namespace)
- ✅ MLflow 3.9.0rc0 - Tracking server with UI
- ✅ PostgreSQL - Backend metadata store  
- ✅ MinIO - S3-compatible artifact storage

## Use with Your Harness

The `.env` file is configured with:
```
MLFLOW_TRACKING_URI=https://mlflow-route-mlflow.apps.cluster-p8mds-1.dyn.redhatworkshops.io
```

### Run Tests

```bash
cd harness
source ../venv/bin/activate

# Quick test
python3 run.py model-sweep \
  --workflow ticket_classifier \
  --step classify_bug \
  --model claude-sonnet-4-6 \
  --iterations 1 \
  --skip-judges
```

All metrics, scores, and artifacts will be logged to MLflow automatically.
