# IIP learning preview — learning-v0.84.0

**Channel:** Public source-only prerelease; not production v1

**License:** Apache-2.0 for original project material; dependencies retain their own licenses

## What you can do

Start a disposable local stack with Docker Desktop, explore synthetic AI usage,
calculated cost, app/team attribution, incomplete coverage, and evidence-backed
opportunities. Add a synthetic infrastructure resource in the console, run a
bounded read-only investigation, and follow its evidence citations. The AI
economics rules and sample investigator do not invoke an LLM.

## Install

1. Install/start Docker Desktop with Compose v2. A recent Docker Engine with
   Compose v2 on Linux can also run the profile; native Windows requires WSL2
   and its Linux shell. macOS Docker Desktop is the initially exercised host.
   Start with 4 CPUs, 6 GB RAM, and 10 GB free disk as a practical learning
   budget, not a measured minimum or production sizing recommendation.
2. Download the `iip-learning-v0.84.0-source.tar.gz` asset and its adjacent
   `SHA256SUMS` from the GitHub prerelease. Use `shasum -a 256 -c SHA256SUMS`
   (macOS) or `sha256sum -c SHA256SUMS` (Linux) in their download folder.
   This detects download corruption; it is not an organizational signature.
3. Extract it and enter `infra-intelligence-platform-learning-v0.84.0`.
4. Run `sh scripts/learning.sh up`. First launch downloads upstream images and
   builds the Python application locally; no IIP container registry login is
   required. Do not use `sudo` on macOS.
5. Follow [your first learning session](../learning/first-session.md).

Alternatively, clone the selected tag:

```bash
git clone --branch learning-v0.84.0 --depth 1 https://github.com/thedevopshuman/infra-intelligence-platform.git
cd infra-intelligence-platform
sh scripts/learning.sh up
```

Endpoints: [console](http://127.0.0.1:18082/console),
[Grafana](http://127.0.0.1:13000/d/iip-ai-finops), and
[Prometheus](http://127.0.0.1:19091). The launcher prints the known demo token.
`sh scripts/learning.sh status` lists services; `sh scripts/learning.sh down`
removes only this checkout's learning containers and their disposable data.
The source folder and downloaded images remain. Stop then start to reset the
time-relative samples; running the launcher twice will not silently reseed.

## Scope and limits

| Included in this lesson | Not claimed by this release |
| --- | --- |
| Synthetic Bedrock/OpenAI-shaped OTel spans | Live AWS/OpenAI calls, real current prices, invoice reconciliation |
| Usage/cost/attribution records and three deterministic findings | Automatic cost savings, FinOps agent or model-quality certification |
| Local resource → investigation → evidence exercise | Real-cluster discovery or production root-cause proof |
| Eight local containers and a guided source build | Published/signed production images, offline installation, registry or SDK publication |
| Loopback endpoints and existing tenant/contract checks | Secure customer onboarding, enterprise identity, Internet exposure or production support |
| Clean-source verification plus learning runtime checks | Full local release qualification, customer certification, HA, backups/upgrades or security audit |

Known credentials, anonymous Grafana, internal plaintext connections, database
trust authentication and ephemeral storage are **intentional demo settings**.
Other users/processes on the same machine can access the loopback services.
Never load customer data or attach real applications. Restarting Docker may
lose data. Input/output model content is not collected by default; the demo
includes deliberate invalid test spans to verify rejection, not customer
prompts. Libraries/images are downloaded separately under their own licenses;
this source-only release does not redistribute vendor image layers.

The API/SDK contracts remain `v1alpha1` and may change. Internal `0.84.0` is a
development version, not 84 publicly supported releases. This is not an
in-place upgrade path from the older demo or the persistent community profile.

## Next milestones

The [public v1 plan](../roadmap/public-v1-release-plan.md) remains active:
persistent backup/recovery and lifecycle, real Bedrock qualification, supported
deployment/upgrade semantics, dependency/security review, and verified signed
distribution. The website remains a separate informational project, not a
central database for customer telemetry.
