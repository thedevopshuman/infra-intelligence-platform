# Your first IIP learning session

This walkthrough is for the **local, disposable learning preview**, not a
production installation. It shows how IIP turns AI usage metadata into cost
estimates and reviewable opportunities, then introduces an evidence-backed
infrastructure investigation.

You need Docker Desktop running with Docker Compose v2 available, a POSIX shell
(macOS/Linux, or a WSL2 Linux shell on Windows),
and a copy of this release's source. Run the commands below from the folder
containing `Makefile`. The learning launcher runs its Python tools in Docker;
you do not need to install Python or its packages on your computer. The `make`
commands require Make on your host. Without Make, use `sh scripts/learning.sh
up`, `sh scripts/learning.sh status`, and `sh scripts/learning.sh down` for the
equivalent operations.
The Docker context must use a local Unix socket; native Windows named-pipe,
SSH, and TCP contexts are not accepted. Docker's configured proxies may be used
while downloading/building dependencies; proxy environment variables are
cleared in the running learning containers.

You do **not** need a Kubernetes cluster, an AWS account, model-provider keys,
or a paid AI subscription. Keep Docker Desktop's Kubernetes feature disabled
unless you need it for a separate exercise. The first launch needs Internet
access to download images and build dependencies; the demo does not make AWS,
Bedrock, OpenAI, or other inference calls.

## 1. Start the learning environment

```bash
make learning-up
```

Allow the initial image downloads and build to finish. A successful start
means the launcher has also checked the synthetic usage ledger, asynchronous
cost processing, telemetry backends, and provisioned dashboard. Wait for its
ready message; containers merely appearing in Docker Desktop is not enough.

The setup uses the `iip-learning` Compose project. In Docker Desktop, expand
that project to see eight long-running services: PostgreSQL, the API, the
isolated usage receiver, the workflow worker, the OpenTelemetry Collector,
Prometheus, Loki, and Grafana. One-off setup containers may also appear while
the launcher is working.

Check the services at any time:

```bash
make learning-status
```

This is a local simulation with deliberately known demo credentials,
plaintext internal connections, and disposable storage. Do not expose its
ports to your network, use a remote Docker host, connect customer workloads,
or put real secrets or customer data into it.

## 2. Read the AI Economics view

Open the [IIP console](http://127.0.0.1:18082/console). In **Connect your
workspace**, paste the console token printed by the launcher and select
**Connect securely**. If the connection dialog is closed, select the identity
button at the bottom of the sidebar to reopen it. This token belongs only to
the disposable demo; it is not a customer authentication setup.

Select **AI Economics**. Keep **Time window** at **Last 24 hours**, choose
**Application** under **Group by**, and select **Load report**.

On a freshly started demo, look for:

- **15 AI requests**: these are normalized synthetic usage records, not 15
  calls to a real model.
- **11 priced records and 4 unpriced records**: a missing price stays visible;
  it does not silently become a zero-cost request.
- **10 allocated records and 5 unallocated records**: ownership comes from a
  protected application/team mapping, not an untrusted label claiming an owner.
- **Calculated cost**: a result of fixture prices and usage quantities, not
  current provider pricing or a bill you owe.

Switch **Group by** to **Team** and select **Load report** again. The same
usage is regrouped; collection and prices have not changed. The seeded
application names include `support-experience` and `commerce-ai`; their teams
include `customer-experience` and `commerce-platform`.

Read the **Evidence-backed opportunity** card. It shows the newest matching
committed finding, its rule, and the number of immutable supporting
references. **Validation required** means exactly that: this is a suggestion
to review, not an instruction that has been executed or a guaranteed saving.
The card does not open raw prompts or automatically change a model. AI cost
recommendations in this preview are deterministic rules, not an AI agent.

The exercise teaches an important distinction: a useful cost total needs
both a price and a clear account of what was left out.

## 3. See the same flow in Grafana

Open the [IIP AI FinOps dashboard](http://127.0.0.1:13000/d/iip-ai-finops).
Grafana is anonymously readable in this local profile; no login is required.

Start with these panels:

| Question | Panel | What to learn |
| --- | --- | --- |
| How much did we use? | How much usage? | Successful requests in the fixture's comparison windows. |
| What might it cost? | How much cost? | Calculated estimates from the test catalog, not invoices. |
| Where is it happening? | Where is spend happening? | Bounded service, model, region, and environment attribution. |
| What changed? | What changed? | Input tokens per request compared with the preceding window. |
| What could improve? | One potential saving | A rule-derived opportunity that still needs validation. |
| What is missing? | Can I trust the coverage? | Incomplete usage, pricing, and retry facts remain explicit. |

The dashboard also includes application/team cost, retry amplification, and a
fixture-qualified lower-cost model comparison. An increase in retries does
not automatically prove a monetary saving: without billable-attempt evidence,
the saving remains unresolved. The model comparison's suitability evidence is
synthetic, not proof that a different model meets your real workload's needs.

The underlying path is:

```text
Synthetic OTel spans → Collector → IIP receiver → PostgreSQL
                                              ↓
                               attribution + cost + finding workers
                                              ↓
                               OTel aggregates → Prometheus → Grafana
```

The [Prometheus interface](http://127.0.0.1:19091) is available for deeper
inspection. Prometheus and Grafana are replaceable reference backends; the
authoritative usage and calculated-cost records remain in IIP's PostgreSQL
store. OTLP transports telemetry, but it is not a universal query API for
every backend.

## 4. Try an infrastructure investigation

AI usage is seeded automatically; infrastructure resources are not. This
separate exercise adds a synthetic resource through the same public API used
by the console. It does **not** create a Kubernetes Deployment or contact a
cluster.

1. In the console, select **Resources**, then **Add demo resource**.
2. Find `checkout-api`. It is intentionally **degraded**, with three desired
   replicas and two available replicas in its synthetic observation.
3. Select **Investigate** in the sidebar. Choose `checkout-api` as the
   **Primary resource** and ask: `Why is checkout-api degraded?`
4. Keep **Lookback** at **Last hour**. Leave **Allow recommendations to become
   proposals** unchecked, then select **Run investigation**.
5. Read the terminal report, including any **Known unknowns**. This bounded,
   deterministic investigator uses no LLM tokens and cannot infer missing
   production events, metrics, logs, or runbooks from a single sample resource.
6. Select an **Evidence citations** entry to inspect its immutable metadata in
   **Evidence**. A citation shows what supported the report; it is not a claim
   that a real customer system was observed.

The purpose is to follow resource → scoped investigation → cited evidence,
not to demonstrate a verified production root cause. The **Actions** and
**Plugins** screens exist, but this lesson does not configure plugin execution
or perform any infrastructure mutation.

## 5. Stop or start again

When finished:

```bash
make learning-down
```

This removes the disposable learning project's containers and its demo data,
including the sample resource and investigations you added. It does not stop
unrelated Docker projects or the separate persistent community installation.
Downloaded/built images and launcher configuration may remain on disk for the
next session; the command is not a whole-machine Docker cleanup.

To get fresh time-relative sample data, run `make learning-down`, then
`make learning-up`. Starting an already running demo is deliberately refused
so old and new comparison windows cannot silently mix. PostgreSQL and the
telemetry stores use temporary storage here: restarting containers or Docker
Desktop is not a persistence test, and may require a fresh session.

## If something does not look right

- **Docker is unavailable:** start Docker Desktop, wait for its engine, then
  run `make learning-status` before retrying.
- **Another lifecycle operation holds the lock:** wait for that command to
  finish. The `iip-learning-lifecycle-lock` container is a never-started name
  reservation, not a ninth service. If a command was killed, inspect that
  helper's `iip.learning.checkout` label and confirm no lifecycle command is
  running before removing only the stale helper. Do not prune Docker globally.
- **A port is already in use:** this profile expects ports including `18082`
  and `13000`. Identify the owning service; do not stop unrelated containers
  blindly. If it is this demo, use its own `make learning-down` command. The
  older `iip-ai-finops` demo uses the same ports: intentionally stop that
  disposable profile with its documented `ai-finops-down` command before
  starting this one. The learning launcher does not clean it up for you.
- **The token is rejected:** use the token printed by this learning launcher,
  not one from another local IIP instance or the community installation.
- **AI totals are empty later:** the fixtures are a fixed snapshot, not an
  ongoing traffic generator. Check the selected window; reset the disposable
  demo for fresh time-relative data.
- **A report is incomplete:** first read its coverage and unknowns. Deliberate
  unpriced/unallocated records and missing real infrastructure telemetry are
  expected, not permission to invent prices or conclusions.
- **Overview shows event delivery backlogged/breached:** the learning profile
  deliberately disables the external event publisher. Committed outbox events
  remain pending, independently of AI-cost processing; that card is not a
  health verdict for this lesson. No external event destination is configured.
- **Startup prints two rejected span messages (HTTP 400):** the fixture sends
  two deliberately content-bearing invalid spans to prove they are rejected.
  These are expected only when followed by the verified ready message. Other
  startup failures must not be ignored.

For implementation detail, see the
[local AI FinOps reference topology](../operations/ai-finops-local-demo.md).
For an empty installation with durable storage and generated credentials, see
the separate [community installation](../operations/community-installation.md);
it is still a pre-release preview, not an automatic next step for customer
production. The [public v1 plan](../roadmap/public-v1-release-plan.md) tracks the
remaining production and public-release qualifications.
