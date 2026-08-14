# Glossary

| Term | Meaning |
| --- | --- |
| Resource | A tenant-scoped infrastructure object with stable platform identity and changing observations. |
| Observation | A source-attributed view of a resource at a point in time. |
| Resource graph | Resources plus typed, time-aware relationships between them. |
| Event | An immutable fact in a CloudEvents-compatible envelope. |
| Timeline | Ordered events, changes, evidence, and actions associated with resources or an investigation. |
| Evidence | Source-attributed, content-hashed material used to support or refute a hypothesis. |
| Investigation | A bounded process that gathers evidence and produces ranked hypotheses and recommendations. |
| Agent | A declared, budgeted reasoning capability with explicit inputs, outputs, tools, and authority. |
| Tool | A narrowly scoped capability an agent may call. |
| Plugin | A separately versioned extension that contributes observations, events, evidence, actions, or a surface. |
| Integration | Configuration and adapter logic for one external system. It may be packaged as a plugin. |
| Action | A proposed or executed mutation of an external system. |
| Authority | The maximum allowed level: read, propose, approve, or execute. |
| Tenant | The isolation and ownership boundary for data, policy, credentials, budgets, and audit. |
| Correlation ID | Identifier grouping events from one incident, workflow, or user request. |
| Causation ID | Identifier of the event or action that directly produced another event. |

