# EDA setup — Unknown issue xSOS RCA

Use this checklist to test the SQS → rulebook → job template flow end to end.

The rulebook AAP syncs from the repo is:

`extensions/eda/rulebooks/event-driven-xsos-rca.yml`

## 1. AWS queue (one-time)

```bash
cd incident-remediation/event-driven-xsos-rca
ansible-playbook -i inventory/hosts.yml playbooks/setup_sqs_queue.yml
```

Copy the queue URL into `group_vars/all.yml` as `sqs_queue_url`.

## 2. AAP job templates

Create three job templates against this repo (same inventory/credential as your other demos):

| Job template name | Playbook |
|---|---|
| `Linux - Setup xSOS SQS Queue` | `incident-remediation/event-driven-xsos-rca/playbooks/setup_sqs_queue.yml` |
| `Linux - Publish Unknown Issue Event` | `incident-remediation/event-driven-xsos-rca/playbooks/publish_unknown_issue_event.yml` |
| `Linux - Run xSOS Analysis` | `incident-remediation/event-driven-xsos-rca/playbooks/run_xsos_analysis.yml` |

For **`Linux - Run xSOS Analysis`** (required for EDA):

- Enable **Prompt on launch → Extra Variables** so `ansible_eda` is passed through
- Limit can stay blank; the playbook targets `target_host` from the event

## 3. EDA project sync

1. **Automation Decisions → Projects → Create/Sync** your Git project
2. Confirm the rulebook appears: **Unknown issue xSOS RCA**
3. Use a **de-supported** decision environment (AAP 2.5+ / 2.6) so `amazon.aws.aws_sqs_queue` is available

If your decision environment is older, change the source plugin in the rulebook to `ansible.eda.aws_sqs_queue` (deprecated but still works on `de-minimal`).

## 4. AWS credential for EDA

Create an **Amazon Web Services** credential with permissions:

- `sqs:ReceiveMessage`
- `sqs:DeleteMessage`
- `sqs:GetQueueUrl`
- `sqs:GetQueueAttributes`

Attach it to the rulebook activation. The SQS plugin uses standard boto credential resolution (credential injectors, instance role, or `~/.aws/credentials` in the decision environment).

## 5. Rulebook activation

**Automation Decisions → Rulebook activations → Create**

| Field | Value |
|---|---|
| Rulebook | Unknown issue xSOS RCA |
| Decision environment | de-supported (or your custom DE with `amazon.aws`) |
| Project | Your synced Git project |
| Credential | AWS credential from step 4 |
| Extra vars | See below |

**Activation extra vars** (adjust for your environment):

```yaml
aws_region: us-east-1
xsos_event_queue_name: aap-unknown-issue-rca
xsos_job_template_name: Linux - Run xSOS Analysis
aap_organization: Default
```

Enable the activation and confirm it reaches **Running** status.

## 6. Test the flow

```bash
ansible-playbook -i inventory/hosts.yml playbooks/publish_unknown_issue_event.yml \
  -e issue_summary="Smoke test — unknown API latency"
```

Expected result:

1. Message lands in SQS
2. Rulebook activation consumes it within ~5 seconds (long poll)
3. AAP launches **Linux - Run xSOS Analysis** with `target_host`, `issue_id`, and `issue_summary` from the event
4. xSOS report written under `/var/tmp/xsos-rca/` on the target host

## Troubleshooting

| Symptom | Check |
|---|---|
| Activation not running | Decision environment includes `amazon.aws`; AWS credential attached |
| No job launched | Rule condition uses `event.body.*` — payload must include `"requested_action": "xsos_analysis"` |
| Job runs on wrong host | `event.body.host` must match an inventory hostname (e.g. `rca-target`) |
| Job missing event context | Enable **Prompt on launch → Extra Variables** on the analysis job template |
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

The SQS plugin wraps this as `event.body` in the rulebook.
