# Lightwell Fast Path Demo - Setup Guide

## Infrastructure Required

| Component | Purpose | How to Provision |
|-----------|---------|------------------|
| AAP 2.7 with AO | Controller + automation orchestrator | `aws-aap-containerized` repo or existing AAP install |
| Event-Driven Ansible | Route JFrog, CI/CD, and GitLab events to AO | EDA rulebook activation with AO endpoint |
| ServiceNow | Incident tracking for manual and auto-patch paths | Developer instance or existing SNOW environment |
| AI credential | Lightwell CI/CD handover Task agent | Any supported model; tested with `claude-sonnet-4-6` |
| Demo VM (optional) | RHEL target for optional remediation playbooks | EC2 t3.small if using disk/process cleanup templates |

## Architecture

```text
Manual / EDA triggers (JFrog, CI/CD, GitLab)
  → SBOM/VEX Correlation TPA
  → Governance Route Decision (switch)
    ├── Auto Patch Approved    → Gather Context (AAP workflow) → Create Incident → Lightwell Agent handover
    ├── Manual Patching        → Create Incident ServiceNow
    ├── Application Patched    → Gather Evidence → Check Compliance → Update/Close Incident
    └── Fallback               → Create Incident ServiceNow (Fallback)
```

## Step-by-Step Setup

### 1. Provision Demo VM (optional)

Only required if you register the optional RHEL remediation job templates from `remediate_disk_cleanup.yml` and `remediate_process_cleanup.yml`.

```bash
aws ec2 create-key-pair --key-name ao-lightwell-demo-key \
  --query 'KeyMaterial' --output text --region us-east-1 > ao-lightwell-demo-key.pem
chmod 600 ao-lightwell-demo-key.pem
```

### 2. Configure ServiceNow

1. **ServiceNow instance** — use a developer instance or existing environment.
2. **Service account** — create credentials for incident create/update operations.
3. **Test incident** — for the Application Patched path, pass `incident_number` on the trigger payload or let the simulate playbook create one.

### 3. Configure AAP Project and Inventory

1. **Project** — sync this repo (or `lightwell-fast-path/` subtree) from Git.
2. **Inventory** — localhost for correlation, incident, evidence, and compliance playbooks.
3. **ServiceNow credentials** — store as extra vars or a custom credential type:

| Variable | Purpose |
|----------|---------|
| `snow_instance_url` | ServiceNow instance base URL |
| `snow_username` | ServiceNow service account username |
| `snow_password` | ServiceNow service account password |
| `snow_caller_id` | Caller sys_id for incident creation |

### 4. Register Job Templates

Create job templates from `lightwell-fast-path/playbooks/`:

| Name | Playbook | Runs on | Notes |
|------|----------|---------|-------|
| Lightwell \| SBOM/VEX Correlation TPA | `sbom_vex_correlation.yml` | localhost | Publishes `governance_route` artifact for switch routing |
| Lightwell \| Create ServiceNow Incident | `create_snow_incident.yml` | localhost | Creates incident from patch publication context |
| Lightwell \| Gather Patching Evidence | `gather_patching_evidence.yml` | localhost | Collects deployment evidence artifacts |
| Lightwell \| Check Compliance Posture | `check_compliance_posture.yml` | localhost | Validates compliance from upstream evidence |
| Lightwell \| Update ServiceNow Ticket | `update_snow_ticket.yml` | localhost | Updates or closes incidents via SNOW API |

**Also register an AAP workflow template:**

| Name | Description |
|------|-------------|
| Lightwell \| Gather Context | Multi-job workflow for CMDB/CVE/VEX context — referenced by the auto-patch path |

Enable **Prompt on launch → Extra Variables** on job templates so AO can pass values from upstream nodes and triggers.

### 5. Configure AI Credential

1. Create an AI credential in automation orchestrator for your chosen model provider.
2. Attach the credential to the **Lightwell Agent handover for CI/CD** node after import.

### 6. Configure EDA Triggers

Configure EDA rulebook activations to route events to the automation orchestrator endpoints for:

| Trigger | Typical event source |
|---------|---------------------|
| EDA Notification from JFrog | Artifact published to JFrog Artifactory |
| EDA Notification CI/CD | Pipeline completion or promotion event |
| EDA Notification Gitlab | GitLab deployment or pipeline webhook |

Each activation should POST artifact metadata (`artifact_name`, `artifact_version`, optional `cve_ids`, `pipeline_url`, `deployment_status`) to the corresponding AO EDA trigger.

### 7. Import AO Workflow

Import the workflow JSON into automation orchestrator:

| File | Description |
|------|-------------|
| [`ao/lightwell-fast-path.json`](ao/lightwell-fast-path.json) | Four-way governance switch with manual and EDA triggers |

**After import, update environment-specific values:**

1. `credential_id` on each node — replace placeholder credential IDs
2. `integration_id` on AAP job nodes — replace `REPLACE_WITH_INTEGRATION_ID` if present
3. ServiceNow credentials in extra vars — replace `YOUR_SNOW_*` placeholders
4. Re-select AI tools on the Lightwell Agent node if using MCP integrations
5. Confirm the **Lightwell \| Gather Context** workflow template name matches your AAP registration

### 8. Configure the Switch Node

| Switch port | Condition | Path |
|-------------|-----------|------|
| Auto Patch Approved | `governance_route == 'auto_patch_approved'` | Gather Context → Create Incident → Lightwell Agent |
| Manual Patching required | `governance_route == 'manual_patching_required'` | Create Incident ServiceNow |
| Application Patched/Deployed | `governance_route == 'application_patched'` | Gather Evidence → Check Compliance → Update/Close Incident |
| Fallback | default | Create Incident ServiceNow (Fallback) |

The switch routes on `${sbom_vex_correlation.artifacts.governance_route}` — configured in the imported workflow JSON.

## Verification

```bash
# Test auto-patch approved path
# Manual trigger with clean artifact (no CVEs)
# Expected: Correlation → Gather Context → Create Incident → Lightwell Agent handover

# Test manual patching path
# Manual trigger with CVE list or governance_route override
# Expected: Correlation → Create Incident ServiceNow

# Test application patched path
# GitLab trigger with deployment_status=deployed and incident_number set
# Expected: Correlation → Gather Evidence → Check Compliance → Close Incident

# Run simulate playbook for all three scenarios
ansible-playbook lightwell-fast-path/test/snow-incident-simulate.yml \
  -e "scenario_list=['auto_patch_approved','manual_patching_required','application_patched']" \
  -e "@extra_vars.yml"
```

Set these environment variables for the simulate playbook:

| Variable | Purpose |
|----------|---------|
| `AO_BASE_URL` | Automation orchestrator base URL |
| `AO_CLIENT_ID` | Service account client ID |
| `AO_CLIENT_SECRET` | Service account client secret |
| `SNOW_INSTANCE_URL` | ServiceNow instance URL |
| `SNOW_USERNAME` | ServiceNow username |
| `SNOW_PASSWORD` | ServiceNow password |
| `SNOW_CALLER_ID` | ServiceNow caller sys_id |

## Ports Reference

| Service | Port | Purpose |
|---------|------|---------|
| AAP Gateway | 443 / 8444 | AAP UI and API |
| AO UI | 8080 | Automation orchestrator web interface |
| ServiceNow | 443 | SNOW API for ticket operations |
| SSH | 22 | Ansible connection to demo VM (optional) |
