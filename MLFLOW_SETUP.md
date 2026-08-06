# MLflow on OpenShift - Setup Complete

## Instance Details

**MLflow URL:** https://mlflow-mlflow.apps.cluster-p8mds-1.dyn.redhatworkshops.io

**Namespace:** mlflow

**OpenShift Cluster:** https://console-openshift-console.apps.cluster-p8mds-1.dyn.redhatworkshops.io

## Architecture

The MLflow deployment consists of:

1. **PostgreSQL Database** - Backend store for MLflow metadata
   - Image: `registry.redhat.io/rhel8/postgresql-13:1-101`
   - Storage: 10Gi persistent volume
   - Credentials: see OpenShift secret / cluster admin (do not commit)

2. **MLflow Tracking Server**
   - **Version: MLflow 3.14.0** (latest stable)
   - Base Image: `python:3.11-slim`
   - Backend: PostgreSQL
   - Artifacts: Stored in pod (for now - see notes below)

3. **OAuth Proxy** - Provides OpenShift authentication
   - Image: `registry.redhat.io/openshift4/ose-oauth-proxy:v4.12.0`
   - TLS termination: reencrypt

## Access

To access MLflow UI:

1. Navigate to: https://mlflow-mlflow.apps.cluster-p8mds-1.dyn.redhatworkshops.io
2. Login with your OpenShift credentials (kubeadmin or your workshop user).
   Get the password from the cluster provisioner / workshop credentials — do not commit it.

## Using MLflow from Python

### Setup

```bash
# Install MLflow
pip install mlflow

# Set tracking URI
export MLFLOW_TRACKING_URI=https://mlflow-mlflow.apps.cluster-p8mds-1.dyn.redhatworkshops.io
export MLFLOW_TRACKING_INSECURE_TLS=true

# For authenticated access, you'll need an OpenShift token
oc login https://api.cluster-p8mds-1.dyn.redhatworkshops.io:6443
export MLFLOW_TRACKING_TOKEN=$(oc whoami -t)
```

### Example Usage

```python
import mlflow
import os

# Set tracking URI
mlflow.set_tracking_uri("https://mlflow-mlflow.apps.cluster-p8mds-1.dyn.redhatworkshops.io")

# For authenticated access
os.environ["MLFLOW_TRACKING_INSECURE_TLS"] = "true"
os.environ["MLFLOW_TRACKING_TOKEN"] = "YOUR_OC_TOKEN"

# Start an experiment
mlflow.set_experiment("llm-evaluation")

# Log parameters and metrics
with mlflow.start_run():
    mlflow.log_param("model", "gpt-4")
    mlflow.log_metric("accuracy", 0.95)
    mlflow.log_metric("latency", 150)
```

## OpenShift CLI Access

```bash
# Login to cluster (use workshop/kubeadmin credentials from your provisioner)
oc login https://api.cluster-p8mds-1.dyn.redhatworkshops.io:6443 \
  --username=<USERNAME> \
  --password='<PASSWORD>'

# Switch to mlflow project
oc project mlflow

# View pods
oc get pods

# View logs
oc logs deployment/mlflow-server -c mlflow

# Get MLflow route
oc get route mlflow
```

## Important Notes

### Artifacts Storage

Currently, artifacts are stored in the pod's ephemeral storage. For production use, you should configure S3-compatible storage:

1. Create an S3 bucket (AWS S3, MinIO, or OpenShift Data Foundation)
2. Update the MLflow deployment to include:
   ```yaml
   env:
     - name: AWS_ACCESS_KEY_ID
       value: "YOUR_ACCESS_KEY"
     - name: AWS_SECRET_ACCESS_KEY
       value: "YOUR_SECRET_KEY"
   args:
     - '--default-artifact-root'
     - 's3://your-bucket-name/mlflow-artifacts'
   ```

### Database Credentials

PostgreSQL credentials live in the cluster (Secret in the `mlflow` namespace). Retrieve them with:

```bash
oc get secret -n mlflow -o yaml   # or the specific secret name used by the deploy
```

Connection string shape (fill from the secret):

```
postgresql://<DB_USER>:<DB_PASSWORD>@postgresql.mlflow.svc:5432/mlflow
```

For production, keep these only in a Kubernetes Secret — never commit them.

### Adding More Users

To grant access to additional users:

```bash
cat > user-access.yaml << EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: user-mlflow-access
  namespace: mlflow
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: mlflow-sa-oauth-access
subjects:
- kind: User
  name: user@redhat.com
  apiGroup: rbac.authorization.k8s.io
EOF

oc apply -f user-access.yaml
```

## Troubleshooting

### Check Pod Status
```bash
oc get pods -n mlflow
```

### View MLflow Logs
```bash
oc logs deployment/mlflow-server -c mlflow -n mlflow
```

### View OAuth Proxy Logs
```bash
oc logs deployment/mlflow-server -c oauth-proxy -n mlflow
```

### Test Database Connection
```bash
oc exec -it deployment/postgresql -n mlflow -- psql -U mlflow -d mlflow -c "SELECT 1;"
```

## Resources

- MLflow Documentation: https://mlflow.org/docs/latest/
- Red Hat MLflow on OpenShift: https://github.com/redhat-et/mlflow-openshift
- OpenShift OAuth Proxy: https://github.com/openshift/oauth-proxy

## Deployed Components

```bash
$ oc get all -n mlflow
NAME                                READY   STATUS    RESTARTS   AGE
pod/mlflow-server-bc686c649-thzbs   2/2     Running   0          5m
pod/postgresql-d6b9b686b-wfm59      1/1     Running   0          6m

NAME                    TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)    AGE
service/mlflow-server   ClusterIP   172.31.76.125    <none>        8443/TCP   6m
service/postgresql      ClusterIP   172.31.198.181   <none>        5432/TCP   6m

NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/mlflow-server   1/1     1            1           5m
deployment.apps/postgresql      1/1     1            1           6m

NAME                              HOST/PORT                                                   
route.route.openshift.io/mlflow   mlflow-mlflow.apps.cluster-p8mds-1.dyn.redhatworkshops.io
```

---

**Setup Date:** 2026-08-05  
**Deployed by:** Claude Code  
**Source:** https://github.com/redhat-et/mlflow-openshift
