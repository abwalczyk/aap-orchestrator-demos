# EDA setup — Unknown issue xSOS RCA

Use this checklist to test the SQS → EDA rulebook → automation orchestrator webhook flow end to end.

The rulebook AAP syncs from the repo is:

`extensions/eda/rulebooks/event-driven-xsos-rca.yml`

## 1. AWS queue (one-time)

```bash
cd event-driven-xsos-rca
ansible-playbook -i inventory/hosts.yml playbooks/setup_sqs_queue.yml
```

Copy the queue URL into `group_vars/all.yml` as `sqs_queue_url`.

## 2. Automation orchestrator workflow

Create (or import) an AO workflow with a **webhook trigger** that accepts any JSON payload (`additionalProperties: true`).

The workflow should read fields from the webhook payload, for example:

- `host` → target for the xSOS AAP job
- `issue_id`, `summary`, `severity`, `source`

Then run **Linux - Run xSOS Analysis** (or equivalent) with those values.

Copy from the AO **EDA webhook trigger** UI:

- **URL** → `ao_webhook_url` (full POST endpoint)
- **Bearer token** → `ao_webhook_token` (only if the connection instructions require one)

Example URL shape for EDA triggers:

```text
http://<ao-host>:8080/api/v1/webhooks/eda/<webhook-path-uuid>
```

Nostromo demo:

```text
http://54.159.25.87:8080/api/v1/webhooks/eda/357ce7cf-0138-409f-ad36-f2a9c2a97c50
```

## 3. AAP job templates

| Job template name | Playbook |
|---|---|
| `Linux - Setup xSOS SQS Queue` | `event-driven-xsos-rca/playbooks/setup_sqs_queue.yml` |
| `Linux - Publish Unknown Issue Event` | `event-driven-xsos-rca/playbooks/publish_unknown_issue_event.yml` |
| `Linux - Run xSOS Analysis` | `event-driven-xsos-rca/playbooks/run_xsos_analysis.yml` |

`Linux - Run xSOS Analysis` is launched by the AO workflow, not directly by EDA.

## 4. EDA project sync

1. **Automation Decisions → Projects → Sync** your Git project
2. Confirm the rulebook appears: **Unknown issue xSOS RCA**
3. Use a **de-supported** decision environment (AAP 2.5+ / 2.6) so `amazon.aws.aws_sqs_queue` is available

## 5. AWS credential for EDA

Create an **Amazon Web Services** credential with permissions:

- `sqs:ReceiveMessage`
- `sqs:DeleteMessage`
- `sqs:GetQueueUrl`
- `sqs:GetQueueAttributes`

Attach it to the rulebook activation.

## 6. Rulebook activation

**Automation Decisions → Rulebook activations → Create**

| Field | Value |
|---|---|
| Rulebook | Unknown issue xSOS RCA |
| Decision environment | de-supported (or your custom DE with `amazon.aws`) |
| Project | Your synced Git project |
| Credential | AWS credential from step 5 |
| Extra vars | See below |

**Activation extra vars** (adjust for your environment):

```yaml
aws_region: us-east-1
xsos_event_queue_name: aap-unknown-issue-rca
ao_webhook_url: http://54.159.25.87:8080/api/v1/webhooks/eda/357ce7cf-0138-409f-ad36-f2a9c2a97c50
ao_webhook_token: ""
ao_webhook_validate_certs: false
```

The rulebook action uses `run_module` + `ansible.builtin.uri` to POST the SQS event body to AO. It does **not** call a playbook or job template directly.

Enable the activation and confirm it reaches **Running** status.

## 7. Test the flow

```bash
ansible-playbook -i inventory/hosts.yml playbooks/publish_unknown_issue_event.yml \
  -e issue_summary="Smoke test unknown API latency"
```

Expected result:

1. Message lands in SQS
2. Rulebook activation consumes it within ~5 seconds (long poll)
3. EDA POSTs the payload to the AO webhook URL
4. AO workflow starts and launches **Linux - Run xSOS Analysis**
5. xSOS report written under `/var/tmp/xsos-rca/` on the target host

Manual webhook test (same payload AO expects). Run from the **repo root**:

```bash
curl -X POST "http://54.159.25.87:8080/api/v1/webhooks/eda/357ce7cf-0138-409f-ad36-f2a9c2a97c50" \
  -H "Content-Type: application/json" \
  -d @event-driven-xsos-rca/test/sample_event.json
```

Or from inside `event-driven-xsos-rca/`:

```bash
curl -X POST "http://54.159.25.87:8080/api/v1/webhooks/eda/357ce7cf-0138-409f-ad36-f2a9c2a97c50" \
  -H "Content-Type: application/json" \
  -d @test/sample_event.json
```

## Troubleshooting

| Symptom | Check |
|---|---|
| Activation not running | Decision environment includes `amazon.aws`; AWS credential attached |
| No AO workflow run | Rule condition uses `event.body.*`; payload must include `"requested_action": "xsos_analysis"` |
| Webhook POST fails | `ao_webhook_url` and `ao_webhook_token` in activation extra vars; try `ao_webhook_validate_certs: false` for lab TLS |
| AO workflow runs but wrong host | `event.body.host` must match an inventory hostname |
| Queue not found | `xsos_event_queue_name` in activation extra vars matches setup playbook output |

## Event payload shape

```json
{
  "issue_id": "demo-20260706-001",
  "host": "rca-target",
  "summary": "Unknown production issue — automated RCA requested",
  "severity": "high",
  "source": "manual-demo",
  "requested_action": "xsos_analysis"
}
```

The SQS plugin exposes this as `event.body` in the rulebook. EDA forwards the same fields to the AO webhook.
