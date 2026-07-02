# Certificate Lifecycle Management Demos

AI-driven certificate lifecycle management using automation orchestrator. The AI agent discovers available job templates, analyzes the certificate type and history, selects the correct renewal strategy, and routes intelligently without hardcoded logic.

## Demos

| Demo | What it shows |
|---|---|
| [Intelligent Cert Lifecycle](cert-lifecycle/) | Two cert types (PEM + Java keystore), AI routing, Splunk integration, operator approval |
| [Expiry Threshold Routing](cert-expiry-switch/) | Coming soon — switch on days remaining |
| [Risk-Based Routing](risk-based-routing/) | Coming soon — AI-assessed risk tier |
| [Proactive Assessment](proactive-assessment/) | Coming soon — scheduled estate-wide scan |

## The Story

A Splunk alert fires when a TLS certificate expires. Automation orchestrator receives it, an AI agent analyzes the certificate details and queries AAP to select the correct renewal template, an operator approves, and AAP renews and validates automatically.

What makes it intelligent: the same workflow handles a PEM certificate on nginx and a Java keystore on an API server. The agent figures out which is which and selects the right template. No conditions. No hardcoded routing.

## Workflow (Intelligent Cert Lifecycle)

```mermaid
flowchart LR
  A[Splunk Cert Alert] --> B[Plan Renewal]
  B --> C[Approve Renewal]
  C -->|approved| D[Run Renewal Job]
  C -->|rejected| E[Renewal Rejected]
  D --> F[Validate Renewal]
```
