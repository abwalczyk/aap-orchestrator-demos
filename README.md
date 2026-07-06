# AAP automation orchestrator demos

Hands-on demos for **Ansible Automation Platform automation orchestrator (AO)** — intelligent workflows that combine Ansible playbooks, AI agents, approvals, and event-driven triggers.

**[Browse demos on GitHub Pages →](https://ansible-tmm.github.io/aap-orchestrator-demos/)**

**[Developer setup & technical docs →](DEVELOPER.md)**

## What is automation orchestrator?

Automation orchestrator is the workflow engine in AAP for visual, multi-step automation:

- **AI agent nodes** — reason and decide using LLMs
- **AAP job template nodes** — run Ansible playbooks
- **Approval nodes** — human-in-the-loop governance
- **Event triggers** — react to Splunk, Prometheus, webhooks, and more
- **Switch nodes** — route on a value, not just success/failure

## Demo catalog

| Demo | Status | Description |
|---|---|---|
| [RHEL CVE Remediation](cve-remediation/) | **Active** | AI triage via Lightspeed MCP → auto-patch dev, approve prod, or investigate with Mattermost report |
| [Disk Utilization & Remediation](disk-utilization/) | **Active** | Check disk usage → switch on % → continue, cleanup, EBS expand, or fallback → Mattermost notify |
| [Intelligent Cert Lifecycle](cert-rotation/cert-lifecycle/) | **Active** | AI agent picks PEM vs keystore renewal; operator approves; AAP renews and validates |
| [Service State Routing](service-health/) | Coming soon | Check service → switch on state → log OK, start, restart, or install |
| [Patch Severity Routing](patch-management/) | Coming soon | Scan patches → switch on severity → patch now, schedule, batch, or compliant |
| [Expiry Threshold Routing](cert-rotation/cert-expiry-switch/) | Coming soon | Cert countdown switch on days remaining (no AI) |
| [Request Type Routing](user-lifecycle/) | Coming soon | User lifecycle form → switch on request type |
| [Risk-Based Routing](cert-rotation/risk-based-routing/) | Coming soon | AI risk-tier routing for certificate renewal |
| [Proactive Assessment](cert-rotation/proactive-assessment/) | Coming soon | Scheduled scan-before-expiry workflows |
| [AI Incident Triage](incident-remediation/ai-incident-triage/) | Coming soon | AI-assisted incident response |
| [Multi-Service Correlation](incident-remediation/multi-service-correlation/) | Coming soon | Correlate alerts across services before remediation |

See the [demo marketplace](https://ansible-tmm.github.io/aap-orchestrator-demos/) for the full list including backup, subscription, and kernel compliance scaffolds.

## Use cases by folder

| Folder | Focus |
|---|---|
| [cert-rotation/](cert-rotation/) | Certificate lifecycle — AI routing and expiry switches |
| [cve-remediation/](cve-remediation/) | Intelligent CVE patching with Lightspeed MCP and approval gates |
| [disk-utilization/](disk-utilization/) | Proportional disk remediation with switch routing |
| [service-health/](service-health/) | Service state check → four remediation paths |
| [patch-management/](patch-management/) | Patch severity switch routing |
| [user-lifecycle/](user-lifecycle/) | Identity request type routing |
| [backup-management/](backup-management/) | Backup result routing (partial ≠ fail) |
| [subscription-management/](subscription-management/) | RHEL subscription state routing |
| [kernel-compliance/](kernel-compliance/) | Kernel compliance switch routing |
| [incident-remediation/](incident-remediation/) | AI triage and multi-service correlation |

## Quick links

- **Browse all demos (cards + filters):** https://ansible-tmm.github.io/aap-orchestrator-demos/
- **Setup guides:** [DEVELOPER.md](DEVELOPER.md)
