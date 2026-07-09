# Ticket Enrichment — setup checklist

🚧 **Under development.** Workflow JSON and playbooks are pending upload. This checklist reflects the intended canvas from the Arcade and workflow screenshot.

## Architecture

```text
ServiceNow (webhook) → AI Triage Agent → Auto Remediation (dynamic JT) → Resolve Incident
```

## Prerequisites (expected)

| Component | Required | Default / notes |
|-----------|----------|-----------------|
| Ansible Automation Orchestrator | Yes | Import workflow from `ao/` when available |
| ServiceNow | Yes | Ticket ingress; test incident for demos |
| ServiceNow MCP integration | Yes | Triage + resolve agents read/write ITSM |
| AAP Controller | Yes | Job templates the triage agent can name at runtime |
| AI credential | Yes | Default model `claude-sonnet-4-6` on both agentic nodes |

## Webhook trigger

- Path: `/incident-alert` (as shown on canvas)
- ServiceNow (or a bridge) POSTs incident payload to the automation orchestrator webhook URL
- Map incident fields the triage agent prompt expects (e.g. incident number, short description)

## Dynamic job template

On the **Auto Remediation** AAP job node:

1. Enable **Use expressions** for job template name (or equivalent in imported JSON)
2. Set: `${triage_agent.result.content.job_template_name}`
3. Ensure triage agent `response_schema` includes `job_template_name`
4. Register matching job templates in AAP before first run

## Setup steps (draft)

1. Configure ServiceNow MCP in automation orchestrator.
2. Configure AI model credential (Claude Sonnet or your replacement).
3. Create AAP job templates for each remediation the triage agent may select.
4. Import workflow JSON from `ao/` (pending).
5. Publish workflow; wire ServiceNow webhook to `/incident-alert`.
6. Fire a test incident and verify: triage work notes → AAP job (correct template) → resolved ticket.

## Arcade reference

[Automation Orchestrator — Ticket Enrichment](https://interact.redhat.com/share/j5xLvTSv2GAkw6szxron)
