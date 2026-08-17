"use strict";

const state = {
  token: "",
  session: null,
  consoleAuthentication: null,
  runtimeVersion: null,
  telemetryDeploymentHealth: null,
  telemetryExportSlo: null,
  telemetryExportBurnRate: null,
  eventDeliveryHealth: null,
  eventDeliverySlo: null,
  investigationCompletionSlo: null,
  evidenceRetention: null,
  resources: [],
  investigations: [],
  evidence: [],
  pluginInvocationStatus: null,
  actionWorkflows: [],
  actionCursor: null,
  selectedActionId: null,
  activeInvestigationId: null,
  pendingReplay: null,
  inspectedReplay: null,
};

const OIDC_TRANSACTION_KEY = "iip.console.oidc.transaction";
const REMEMBERED_TOKEN_KEY = "iip.console.token";
const OIDC_TRANSACTION_MAX_AGE_MILLIS = 10 * 60_000;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function identifier(prefix, length = 32) {
  const bytes = new Uint8Array(Math.ceil(length / 2));
  crypto.getRandomValues(bytes);
  return `${prefix}_${[...bytes].map((value) => value.toString(16).padStart(2, "0")).join("").slice(0, length)}`;
}

function base64Url(bytes) {
  let raw = "";
  bytes.forEach((value) => { raw += String.fromCharCode(value); });
  return btoa(raw).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function randomBase64Url(byteLength) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

async function pkceChallenge(verifier) {
  if (!crypto.subtle) throw new Error("authentication.pkce-unavailable");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

function validateConsoleAuthentication(document) {
  if (document?.apiVersion !== "iip.platform/v1alpha1"
      || document?.kind !== "ConsoleAuthenticationConfiguration"
      || Object.keys(document).sort().join(",") !== "apiVersion,kind,spec"
      || !["local-token", "access-token", "oidc-pkce"].includes(document?.spec?.mode)) {
    throw new Error("authentication.configuration.invalid");
  }
  if (document.spec.mode !== "oidc-pkce") {
    if (Object.keys(document.spec).join(",") !== "mode") throw new Error("authentication.configuration.invalid");
    return document;
  }
  const profile = document.spec.oidc;
  if (!profile || Object.keys(document.spec).sort().join(",") !== "mode,oidc"
      || Object.keys(profile).sort().join(",") !== "authorizationEndpoint,clientId,issuer,pkceMethod,providerLabel,redirectUri,scopes,tokenEndpoint") {
    throw new Error("authentication.configuration.invalid");
  }
  if ([profile.issuer, profile.authorizationEndpoint, profile.tokenEndpoint, profile.redirectUri]
    .some((value) => typeof value !== "string" || value.length < 1 || value.length > 2048 || /[\x00-\x20\x7f]/.test(value))) {
    throw new Error("authentication.configuration.invalid");
  }
  const issuer = new URL(profile.issuer);
  const authorizationEndpoint = new URL(profile.authorizationEndpoint);
  const tokenEndpoint = new URL(profile.tokenEndpoint);
  const redirectUri = new URL(profile.redirectUri);
  const safeRedirect = redirectUri.protocol === "https:"
    || (redirectUri.protocol === "http:" && ["localhost", "127.0.0.1"].includes(redirectUri.hostname));
  if (profile?.pkceMethod !== "S256"
      || issuer.protocol !== "https:" || issuer.username || issuer.password || issuer.search || issuer.hash
      || authorizationEndpoint.protocol !== "https:"
      || tokenEndpoint.protocol !== "https:"
      || authorizationEndpoint.username || authorizationEndpoint.password
      || tokenEndpoint.username || tokenEndpoint.password
      || authorizationEndpoint.search || authorizationEndpoint.hash
      || tokenEndpoint.search || tokenEndpoint.hash
      || !safeRedirect || redirectUri.username || redirectUri.password
      || redirectUri.origin !== window.location.origin
      || !["/console", "/console/"].includes(redirectUri.pathname)
      || redirectUri.search || redirectUri.hash
      || typeof profile.clientId !== "string" || !/^[^\s\x00-\x1f]{1,256}$/.test(profile.clientId)
      || !Array.isArray(profile.scopes) || profile.scopes.length < 1 || profile.scopes.length > 32
      || new Set(profile.scopes).size !== profile.scopes.length || !profile.scopes.includes("openid")
      || profile.scopes.some((scope) => typeof scope !== "string" || !/^[A-Za-z0-9._:/-]{1,128}$/.test(scope))
      || typeof profile.providerLabel !== "string" || !/^[^\x00-\x1f\x7f]{1,64}$/.test(profile.providerLabel)) {
    throw new Error("authentication.configuration.invalid");
  }
  return document;
}

async function loadConsoleAuthentication() {
  try {
    const response = await fetch("/v1/authentication/console", {
      headers: { accept: "application/json" },
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });
    if (!response.ok) throw new Error("authentication.configuration.unavailable");
    state.consoleAuthentication = validateConsoleAuthentication(await readBoundedJson(response));
  } catch (_error) {
    state.consoleAuthentication = {
      apiVersion: "iip.platform/v1alpha1",
      kind: "ConsoleAuthenticationConfiguration",
      spec: { mode: "access-token" },
    };
    $("#connection-error").textContent = "Single sign-on discovery is unavailable. You can still use an issued access token.";
    $("#connection-error").hidden = false;
  }
}

function configureConnectionDialog() {
  const mode = state.consoleAuthentication?.spec?.mode || "access-token";
  const oidc = state.consoleAuthentication?.spec?.oidc;
  const oidcAvailable = mode === "oidc-pkce" && oidc;
  $("#oidc-connect").hidden = !oidcAvailable;
  $("#token-caption").textContent = mode === "local-token"
    ? "Local development Bearer token"
    : "Issued access token";
  $("#token-input").placeholder = mode === "local-token"
    ? "Paste a generated local token"
    : "Paste an OIDC access token";
  if (oidcAvailable) {
    $("#oidc-button").textContent = `Continue with ${oidc.providerLabel}`;
    $("#connection-copy").textContent = "Use your organization's identity provider. Tenant, actor, and roles are derived only after the API verifies the returned access token.";
    $("#connection-help").textContent = "No password, client secret, refresh token, or identity-provider session is stored by this console.";
  } else if (mode === "local-token") {
    $("#connection-copy").textContent = "Enter a local development Bearer token. It is sent only to this same-origin API and never written to platform logs or resources.";
    $("#connection-help").innerHTML = "Run <code>make dev-up</code> to create a local stack and credentials.";
  } else {
    $("#connection-copy").textContent = "Enter an access token issued for this control plane. Browser single sign-on has not been enabled by the deployment.";
    $("#connection-help").textContent = "Ask the deployment administrator to configure the OIDC public-client profile for one-click sign-in.";
  }
}

async function beginOidcSignIn() {
  const profile = state.consoleAuthentication?.spec?.oidc;
  if (state.consoleAuthentication?.spec?.mode !== "oidc-pkce" || !profile) {
    throw new Error("authentication.configuration.invalid");
  }
  const csrfState = randomBase64Url(32);
  const verifier = randomBase64Url(64);
  const challenge = await pkceChallenge(verifier);
  const transaction = {
    state: csrfState,
    verifier,
    redirectUri: profile.redirectUri,
    remember: $("#remember-token").checked,
    createdAt: Date.now(),
  };
  sessionStorage.setItem(OIDC_TRANSACTION_KEY, JSON.stringify(transaction));
  sessionStorage.removeItem(REMEMBERED_TOKEN_KEY);
  const destination = new URL(profile.authorizationEndpoint);
  destination.searchParams.set("response_type", "code");
  destination.searchParams.set("client_id", profile.clientId);
  destination.searchParams.set("redirect_uri", profile.redirectUri);
  destination.searchParams.set("scope", profile.scopes.join(" "));
  destination.searchParams.set("state", csrfState);
  destination.searchParams.set("code_challenge", challenge);
  destination.searchParams.set("code_challenge_method", "S256");
  window.location.assign(destination.href);
}

async function readBoundedJson(response, maximumBytes = 65_536) {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > maximumBytes)) {
    throw new Error("authentication.response.invalid");
  }
  if (!response.body?.getReader) {
    const text = await response.text();
    if (new TextEncoder().encode(text).byteLength > maximumBytes) throw new Error("authentication.response.invalid");
    return JSON.parse(text);
  }
  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > maximumBytes) {
      await reader.cancel();
      throw new Error("authentication.response.invalid");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  chunks.forEach((chunk) => { bytes.set(chunk, offset); offset += chunk.byteLength; });
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

function oidcCallbackPresent() {
  const parameters = new URLSearchParams(window.location.search);
  return parameters.has("code") || parameters.has("error") || parameters.has("state");
}

async function completeOidcCallback() {
  const parameters = new URLSearchParams(window.location.search);
  const codeValues = parameters.getAll("code");
  const stateValues = parameters.getAll("state");
  const errorValues = parameters.getAll("error");
  const issuerValues = parameters.getAll("iss");
  window.history.replaceState({}, "", `${window.location.pathname}${window.location.hash}`);
  let transaction;
  try {
    transaction = JSON.parse(sessionStorage.getItem(OIDC_TRANSACTION_KEY) || "null");
  } catch (_error) {
    transaction = null;
  }
  sessionStorage.removeItem(OIDC_TRANSACTION_KEY);
  const profile = state.consoleAuthentication?.spec?.oidc;
  const transactionAge = Date.now() - transaction?.createdAt;
  if (state.consoleAuthentication?.spec?.mode !== "oidc-pkce"
      || !profile || !transaction
      || codeValues.length > 1 || stateValues.length !== 1 || errorValues.length > 1 || issuerValues.length > 1
      || transaction.state !== stateValues[0]
      || transaction.redirectUri !== profile.redirectUri
      || typeof transaction.verifier !== "string" || !/^[A-Za-z0-9_-]{43,128}$/.test(transaction.verifier)
      || !Number.isFinite(transactionAge) || transactionAge < -60_000 || transactionAge > OIDC_TRANSACTION_MAX_AGE_MILLIS
      || (issuerValues.length === 1 && issuerValues[0] !== profile.issuer)) {
    throw new Error("authentication.callback.invalid");
  }
  if (errorValues.length === 1) throw new Error("authentication.authorization.denied");
  const code = codeValues[0];
  if (codeValues.length !== 1 || typeof code !== "string" || !/^[^\s\x00-\x1f]{1,8192}$/.test(code)) {
    throw new Error("authentication.callback.invalid");
  }
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: profile.clientId,
    code,
    redirect_uri: profile.redirectUri,
    code_verifier: transaction.verifier,
  });
  const response = await fetch(profile.tokenEndpoint, {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
  });
  const tokenResponse = await readBoundedJson(response);
  const accessToken = tokenResponse?.access_token;
  if (!response.ok
      || typeof tokenResponse?.token_type !== "string"
      || tokenResponse.token_type.toLowerCase() !== "bearer"
      || typeof accessToken !== "string"
      || !/^[A-Za-z0-9._~+/-]{32,8192}=*$/.test(accessToken)) {
    throw new Error("authentication.exchange.invalid");
  }
  await connect(accessToken, transaction.remember === true);
}

function formatDate(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Unknown" : new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function clear(element) {
  while (element.firstChild) element.removeChild(element.firstChild);
}

function showNotice(message, kind = "success") {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", kind === "error");
  notice.hidden = false;
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => { notice.hidden = true; }, 6000);
}

async function api(path, options = {}) {
  if (!state.token) throw new Error("authentication.required");
  const headers = new Headers(options.headers || {});
  headers.set("authorization", `Bearer ${state.token}`);
  if (options.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (_error) {
    throw new Error("network.unavailable");
  }
  let body;
  try {
    body = await response.json();
  } catch (_error) {
    throw new Error("response.invalid");
  }
  if (!response.ok) throw new Error(body?.error?.code || `http.${response.status}`);
  return body;
}

async function checkHealth() {
  const dot = $("#api-status-dot");
  const label = $("#api-status");
  try {
    const response = await fetch("/readyz");
    if (!response.ok) throw new Error("not ready");
    dot.className = "online";
    label.textContent = state.session ? "Connected" : "API ready";
  } catch (_error) {
    dot.className = "offline";
    label.textContent = "API unavailable";
  }
}

function switchView(name) {
  const target = $(`#view-${name}`);
  if (!target) return;
  $$(".view").forEach((view) => {
    const active = view === target;
    view.hidden = !active;
    view.classList.toggle("active", active);
  });
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $("#page-title").textContent = target.dataset.title;
  $("#page-eyebrow").textContent = target.dataset.eyebrow;
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "actions" && state.token) refreshActions();
}

function updateIdentity() {
  if (!state.session) {
    $("#actor-label").textContent = "Not connected";
    $("#tenant-label").textContent = "Connect to begin";
    $("#avatar").textContent = "?";
    return;
  }
  const { actorId, tenantId } = state.session.metadata;
  $("#actor-label").textContent = actorId;
  $("#tenant-label").textContent = `Tenant · ${tenantId}`;
  $("#avatar").textContent = actorId.slice(0, 2).toUpperCase();
}

function compactIdentity(value, length = 12) {
  if (!value) return "Not supplied";
  if (value.startsWith("sha256:")) return `sha256:${value.slice(7, 7 + length)}…`;
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function renderRuntimeVersion() {
  const report = state.runtimeVersion;
  const spec = report?.spec;
  if (!spec) {
    const waiting = state.session ? "Unavailable" : "Connect to verify";
    $("#runtime-application").textContent = waiting;
    ["contracts", "storage", "revision", "chart", "image"].forEach((field) => {
      $(`#runtime-${field}`).textContent = "—";
    });
    $("#runtime-details").disabled = true;
    return;
  }
  $("#runtime-application").textContent = `v${spec.application.version}`;
  $("#runtime-contracts").textContent = spec.contracts.apiVersion.replace("iip.platform/", "");
  $("#runtime-storage").textContent = spec.storage.requiredMigration.replace(".sql", "");
  $("#runtime-revision").textContent = spec.build.mode === "development"
    ? "Development"
    : compactIdentity(spec.build.revision);
  $("#runtime-chart").textContent = spec.deployment.helmChartVersion
    ? `v${spec.deployment.helmChartVersion}`
    : "Not supplied";
  $("#runtime-image").textContent = compactIdentity(spec.deployment.imageDigest);
  $("#runtime-details").disabled = false;
}

async function refreshRuntimeVersion() {
  try {
    state.runtimeVersion = await api("/v1/system/version");
  } catch (_error) {
    state.runtimeVersion = null;
  }
  renderRuntimeVersion();
}

function renderTelemetryDeploymentHealth() {
  const spec = state.telemetryDeploymentHealth?.spec;
  const chip = $("#telemetry-health-state");
  if (!spec) {
    ["instances", "current", "stale", "metrics", "traces"].forEach((field) => {
      $(`#telemetry-health-${field}`).textContent = "—";
    });
    chip.textContent = state.session && !hasRole("platform-admin")
      ? "Platform admin required"
      : state.session ? "Unavailable" : "Connect to inspect";
    chip.className = "status-chip neutral";
    $("#telemetry-health-details").disabled = true;
    $("#telemetry-health-note").textContent = "Platform administrators can verify API and worker export outcomes without exposing endpoints or credentials.";
    return;
  }
  const summary = spec.summary;
  const enabled = (signal) => spec.instances
    .map((instance) => instance.signals.find((item) => item.signal === signal))
    .filter((item) => item?.enabled);
  const signalSummary = (signal) => {
    const items = enabled(signal);
    return items.length ? `${items.filter((item) => item.status === "healthy").length}/${items.length} healthy` : "Disabled";
  };
  $("#telemetry-health-instances").textContent = String(summary.includedInstances);
  $("#telemetry-health-current").textContent = String(summary.currentInstances);
  $("#telemetry-health-stale").textContent = String(summary.staleInstances);
  $("#telemetry-health-metrics").textContent = signalSummary("metrics");
  $("#telemetry-health-traces").textContent = signalSummary("traces");
  chip.textContent = spec.status;
  chip.className = `status-chip ${spec.status === "healthy" ? "success" : spec.status === "degraded" ? "danger" : spec.status === "awaiting-first-attempt" ? "warning" : "neutral"}`;
  $("#telemetry-health-details").disabled = false;
  $("#telemetry-health-note").textContent = summary.staleInstances
    ? `${summary.staleInstances} instance heartbeat(s) are stale; product readiness remains independent.`
    : summary.truncated
      ? "The bounded instance view is truncated and requires capacity review."
      : "Recent API and workflow-worker exporter outcomes are current.";
}

async function refreshTelemetryDeploymentHealth() {
  if (!state.session || !hasRole("platform-admin")) {
    state.telemetryDeploymentHealth = null;
    renderTelemetryDeploymentHealth();
    return;
  }
  try {
    state.telemetryDeploymentHealth = await api("/v1/operations/telemetry/deployment-export-health");
  } catch (_error) {
    state.telemetryDeploymentHealth = null;
  }
  renderTelemetryDeploymentHealth();
}

function renderTelemetryExportSlo() {
  const spec = state.telemetryExportSlo?.spec;
  const chip = $("#telemetry-slo-state");
  const renderSignal = (name) => {
    const signal = spec?.signals?.find((item) => item.signal === name);
    if (!signal) return "—";
    if (signal.attainmentBasisPoints === null) return signal.status;
    return `${(signal.attainmentBasisPoints / 100).toFixed(2)}%`;
  };
  $("#telemetry-slo-metrics").textContent = renderSignal("metrics");
  $("#telemetry-slo-traces").textContent = renderSignal("traces");
  if (!spec) {
    chip.textContent = state.session && !hasRole("platform-admin")
      ? "Platform admin required"
      : state.session ? "SLO unavailable" : "SLO unavailable";
    chip.className = "status-chip neutral";
    $("#telemetry-slo-details").disabled = true;
    return;
  }
  chip.textContent = `SLO ${spec.status}`;
  chip.className = `status-chip ${spec.status === "meeting" ? "success" : spec.status === "breached" ? "danger" : ["no-data", "insufficient-data"].includes(spec.status) ? "warning" : "neutral"}`;
  $("#telemetry-slo-details").disabled = false;
}

async function refreshTelemetryExportSlo() {
  if (!state.session || !hasRole("platform-admin")) {
    state.telemetryExportSlo = null;
    renderTelemetryExportSlo();
    return;
  }
  try {
    state.telemetryExportSlo = await api("/v1/operations/telemetry/export-slo");
  } catch (_error) {
    state.telemetryExportSlo = null;
  }
  renderTelemetryExportSlo();
}

function renderTelemetryExportBurnRate() {
  const spec = state.telemetryExportBurnRate?.spec;
  const chip = $("#telemetry-burn-rate-state");
  const renderSignal = (name) => {
    const signal = spec?.signals?.find((item) => item.signal === name);
    if (!signal) return "—";
    if (signal.long.burnRateHundredths === null) return signal.status;
    return `${(signal.long.burnRateHundredths / 100).toFixed(2)}x`;
  };
  $("#telemetry-burn-rate-metrics").textContent = renderSignal("metrics");
  $("#telemetry-burn-rate-traces").textContent = renderSignal("traces");
  if (!spec) {
    chip.textContent = state.session && !hasRole("platform-admin")
      ? "Platform admin required"
      : "Burn rate unavailable";
    chip.className = "status-chip neutral";
    $("#telemetry-burn-rate-details").disabled = true;
    return;
  }
  chip.textContent = `Burn ${spec.status}`;
  chip.className = `status-chip ${spec.status === "sustainable" ? "success" : spec.status === "critical" ? "danger" : ["no-data", "insufficient-data", "elevated"].includes(spec.status) ? "warning" : "neutral"}`;
  $("#telemetry-burn-rate-details").disabled = false;
}

async function refreshTelemetryExportBurnRate() {
  if (!state.session || !hasRole("platform-admin")) {
    state.telemetryExportBurnRate = null;
    renderTelemetryExportBurnRate();
    return;
  }
  try {
    state.telemetryExportBurnRate = await api("/v1/operations/telemetry/export-burn-rate");
  } catch (_error) {
    state.telemetryExportBurnRate = null;
  }
  renderTelemetryExportBurnRate();
}

function renderEventDeliveryHealth() {
  const report = state.eventDeliveryHealth;
  const spec = report?.spec;
  const chip = $("#delivery-state");
  if (!spec) {
    ["pending", "inflight", "retrying", "quarantined"].forEach((field) => {
      $(`#delivery-${field}`).textContent = "—";
    });
    chip.textContent = state.session && !hasRole("platform-admin")
      ? "Platform admin required"
      : state.session ? "Unavailable" : "Connect to inspect";
    chip.className = "status-chip neutral";
    $("#delivery-details").disabled = true;
    $("#delivery-note").textContent = "Platform administrators can inspect exact-tenant backlog without exposing event payloads.";
    return;
  }
  const delivery = spec.delivery;
  $("#delivery-pending").textContent = String(delivery.pendingEvents);
  $("#delivery-inflight").textContent = String(delivery.inFlightEvents);
  $("#delivery-retrying").textContent = String(delivery.retryingEvents);
  $("#delivery-quarantined").textContent = String(delivery.quarantinedEvents);
  chip.textContent = spec.status;
  chip.className = `status-chip ${spec.status === "healthy" ? "success" : spec.status === "degraded" ? "danger" : "warning"}`;
  $("#delivery-details").disabled = false;
  $("#delivery-note").textContent = delivery.pendingEvents
    ? `Oldest pending event: ${delivery.oldestPendingEventAgeSeconds ?? 0}s. Automatic retries remain bounded.`
    : delivery.quarantinedEvents
      ? "Automatic delivery stopped for quarantined events; inspect provenance before governed recovery."
      : "The tenant outbox is drained and no events are quarantined.";
}

async function refreshEventDeliveryHealth() {
  if (!state.session || !hasRole("platform-admin")) {
    state.eventDeliveryHealth = null;
    renderEventDeliveryHealth();
    return;
  }
  try {
    state.eventDeliveryHealth = await api("/v1/operations/events/delivery-health?limit=20");
  } catch (_error) {
    state.eventDeliveryHealth = null;
  }
  renderEventDeliveryHealth();
}

function basisPoints(value) {
  return value === null || value === undefined ? "—" : `${(value / 100).toFixed(2)}%`;
}

function renderEventDeliverySlo() {
  const report = state.eventDeliverySlo;
  const spec = report?.spec;
  const chip = $("#delivery-slo-state");
  if (!spec) {
    $("#delivery-slo-attainment").textContent = "—";
    chip.textContent = state.session && !hasRole("platform-admin")
      ? "SLO admin required"
      : state.session ? "SLO unavailable" : "SLO unavailable";
    chip.className = "status-chip neutral";
    $("#delivery-slo-details").disabled = true;
    $("#delivery-slo-note").textContent = "The rolling publication objective appears after connection.";
    return;
  }
  const measurement = spec.measurement;
  const objective = spec.objective;
  $("#delivery-slo-attainment").textContent = basisPoints(measurement.attainmentBasisPoints);
  chip.textContent = spec.status;
  chip.className = `status-chip ${spec.status === "meeting" ? "success" : spec.status === "breached" ? "danger" : spec.status === "insufficient-data" ? "warning" : "neutral"}`;
  $("#delivery-slo-details").disabled = false;
  if (spec.status === "no-data") {
    $("#delivery-slo-note").textContent = "No events have matured past the configured publication deadline in this window.";
  } else if (spec.status === "insufficient-data") {
    $("#delivery-slo-note").textContent = `${measurement.eligibleEvents}/${objective.minimumEligibleEvents} mature events; more samples are required before an SLO verdict.`;
  } else {
    $("#delivery-slo-note").textContent = `${basisPoints(measurement.attainmentBasisPoints)} attained vs ${basisPoints(objective.minimumAttainmentBasisPoints)} required across the ${Math.round(spec.window.durationSeconds / 60)} minute window.`;
  }
}

async function refreshEventDeliverySlo() {
  if (!state.session || !hasRole("platform-admin")) {
    state.eventDeliverySlo = null;
    renderEventDeliverySlo();
    return;
  }
  try {
    state.eventDeliverySlo = await api("/v1/operations/events/delivery-slo");
  } catch (_error) {
    state.eventDeliverySlo = null;
  }
  renderEventDeliverySlo();
}

function renderInvestigationCompletionSlo() {
  const report = state.investigationCompletionSlo;
  const spec = report?.spec;
  const chip = $("#investigation-slo-state");
  const fields = ["accepted", "eligible", "within", "misses", "attainment"];
  if (!spec) {
    fields.forEach((field) => { $(`#investigation-slo-${field}`).textContent = "—"; });
    chip.textContent = state.session && !hasRole("platform-admin")
      ? "SLO admin required"
      : "SLO unavailable";
    chip.className = "status-chip neutral";
    $("#investigation-slo-details").disabled = true;
    $("#investigation-slo-note").textContent = "The rolling useful-completion objective appears after connection.";
    return;
  }
  const measurement = spec.measurement;
  const objective = spec.objective;
  $("#investigation-slo-accepted").textContent = String(measurement.acceptedJobs);
  $("#investigation-slo-eligible").textContent = String(measurement.eligibleJobs);
  $("#investigation-slo-within").textContent = String(measurement.withinObjectiveJobs);
  $("#investigation-slo-misses").textContent = String(measurement.eligibleJobs - measurement.withinObjectiveJobs);
  $("#investigation-slo-attainment").textContent = basisPoints(measurement.attainmentBasisPoints);
  chip.textContent = spec.status;
  chip.className = `status-chip ${spec.status === "meeting" ? "success" : spec.status === "breached" ? "danger" : spec.status === "insufficient-data" ? "warning" : "neutral"}`;
  $("#investigation-slo-details").disabled = false;
  if (spec.status === "no-data") {
    $("#investigation-slo-note").textContent = "No accepted jobs have matured past the configured completion deadline in this window.";
  } else if (spec.status === "insufficient-data") {
    $("#investigation-slo-note").textContent = `${measurement.eligibleJobs}/${objective.minimumEligibleJobs} mature jobs; more samples are required before an SLO verdict.`;
  } else {
    $("#investigation-slo-note").textContent = `${basisPoints(measurement.attainmentBasisPoints)} attained vs ${basisPoints(objective.minimumAttainmentBasisPoints)} required. Late, failed, cancelled, and unfinished jobs remain misses.`;
  }
}

async function refreshInvestigationCompletionSlo() {
  if (!state.session || !hasRole("platform-admin")) {
    state.investigationCompletionSlo = null;
    renderInvestigationCompletionSlo();
    return;
  }
  try {
    state.investigationCompletionSlo = await api("/v1/operations/investigations/completion-slo");
  } catch (_error) {
    state.investigationCompletionSlo = null;
  }
  renderInvestigationCompletionSlo();
}

function renderEvidenceRetention() {
  const report = state.evidenceRetention;
  const spec = report?.spec;
  const chip = $("#evidence-retention-state");
  if (!spec) {
    ["stored", "eligible", "legal-hold", "batch", "digest"].forEach((field) => {
      $(`#evidence-retention-${field}`).textContent = "—";
    });
    chip.textContent = state.session && !hasRole("platform-admin")
      ? "Platform admin required"
      : "Policy unavailable";
    chip.className = "status-chip neutral";
    $("#evidence-retention-details").disabled = true;
    $("#evidence-retention-note").textContent = "Platform administrators can inspect retention without triggering deletion.";
    return;
  }
  const artifacts = spec.artifacts;
  $("#evidence-retention-stored").textContent = String(artifacts.storedBefore);
  $("#evidence-retention-eligible").textContent = String(artifacts.eligible);
  $("#evidence-retention-legal-hold").textContent = String(artifacts.legalHold);
  $("#evidence-retention-batch").textContent = String(spec.policy.batchSize);
  $("#evidence-retention-digest").textContent = compactIdentity(spec.policy.digest, 8);
  chip.textContent = spec.status;
  chip.className = `status-chip ${spec.status === "current" ? "success" : spec.status === "cleanup-required" ? "warning" : "neutral"}`;
  $("#evidence-retention-details").disabled = false;
  $("#evidence-retention-note").textContent = spec.status === "disabled"
    ? `${artifacts.eligible} artifact(s) are eligible, but automatic expiration is disabled by deployment policy.`
    : spec.status === "cleanup-required"
      ? `${artifacts.remainingEligible} artifact(s) remain eligible. Cleanup is bounded, tenant-scoped, and audited.`
      : "No stored artifact is currently eligible; immutable metadata and citations remain available.";
}

async function refreshEvidenceRetention() {
  if (!state.session || !hasRole("platform-admin")) {
    state.evidenceRetention = null;
    renderEvidenceRetention();
    return;
  }
  try {
    state.evidenceRetention = await api("/v1/operations/evidence/retention");
  } catch (_error) {
    state.evidenceRetention = null;
  }
  renderEvidenceRetention();
}

async function connect(token, remember) {
  state.token = token;
  try {
    state.session = await api("/v1/session");
    if (remember) sessionStorage.setItem(REMEMBERED_TOKEN_KEY, token);
    else sessionStorage.removeItem(REMEMBERED_TOKEN_KEY);
    updateIdentity();
    await Promise.all([refreshRuntimeVersion(), refreshTelemetryDeploymentHealth(), refreshTelemetryExportSlo(), refreshTelemetryExportBurnRate(), refreshEventDeliveryHealth(), refreshEventDeliverySlo(), refreshInvestigationCompletionSlo(), refreshEvidenceRetention(), refreshResources(), refreshActions()]);
    $("#connection-dialog").close();
    $("#connection-error").hidden = true;
    showNotice(`Connected as ${state.session.metadata.actorId} in tenant ${state.session.metadata.tenantId}.`);
    await checkHealth();
  } catch (error) {
    state.token = "";
    state.session = null;
    sessionStorage.removeItem(REMEMBERED_TOKEN_KEY);
    const localMode = state.consoleAuthentication?.spec?.mode === "local-token";
    const message = error.message === "authentication.invalid" || error.message === "authentication.required"
      ? localMode
        ? "The Bearer token was not accepted. Use a token generated by make dev-up."
        : "The identity provider's access token was not accepted by this control plane."
      : `Connection failed (${error.message}).`;
    $("#connection-error").textContent = message;
    $("#connection-error").hidden = false;
    updateIdentity();
    throw error;
  }
}

function resourceName(resource) {
  return resource.spec.displayName || resource.spec.externalId || resource.metadata.uid || "Unnamed resource";
}

function resourceHealth(resource) {
  return resource.status?.health || "unknown";
}

function renderAttention() {
  const container = $("#attention-list");
  clear(container);
  const attention = state.resources.filter((resource) => ["degraded", "unhealthy"].includes(resourceHealth(resource))).slice(0, 5);
  container.classList.toggle("empty-state", attention.length === 0);
  if (!attention.length) {
    container.append(node("p", "", state.resources.length ? "No current resources need attention." : "No resource observations are available yet."));
    return;
  }
  attention.forEach((resource) => {
    const item = node("div", "stack-item");
    item.append(node("span", `health-dot ${resourceHealth(resource)}`));
    const copy = node("div");
    copy.append(node("strong", "", resourceName(resource)));
    copy.append(node("small", "", `${resource.spec.provider} · ${resource.spec.type}`));
    item.append(copy);
    const button = node("button", "text-button", "Investigate →");
    button.type = "button";
    button.addEventListener("click", () => prepareInvestigation(resource.metadata.uid));
    item.append(button);
    container.append(item);
  });
}

function renderRecentInvestigations() {
  const container = $("#recent-investigations");
  clear(container);
  const reports = state.investigations.slice(0, 5);
  container.classList.toggle("empty-state", reports.length === 0);
  if (!reports.length) {
    container.append(node("p", "", "No investigation has run in this tab."));
    return;
  }
  reports.forEach((report) => {
    const item = node("div", "stack-item");
    item.append(node("span", `health-dot ${report.spec.outcome === "conclusive" ? "healthy" : "degraded"}`));
    const copy = node("div");
    copy.append(node("strong", "", report.spec.summary));
    copy.append(node("small", "", `${report.spec.outcome} · ${formatDate(report.metadata.createdAt)}`));
    item.append(copy);
    const button = node("button", "text-button", "Open →");
    button.type = "button";
    button.addEventListener("click", () => {
      switchView("investigate");
      renderInvestigation(report);
    });
    item.append(button);
    container.append(item);
  });
}

function renderMetrics() {
  $("#metric-resources").textContent = String(state.resources.length);
  $("#metric-attention").textContent = String(state.resources.filter((resource) => ["degraded", "unhealthy"].includes(resourceHealth(resource))).length);
  $("#metric-investigations").textContent = String(state.investigations.length);
  $("#metric-evidence").textContent = String(state.evidence.length);
  renderAttention();
  renderRecentInvestigations();
}

function renderResourceOptions() {
  const replayMode = $("#action-type").value === "event-delivery.requeue";
  const targets = [
    [$("#investigation-resource"), state.resources, "Choose a resource"],
    [
      $("#action-resource"),
      replayMode
        ? state.resources
        : state.resources.filter((resource) => resource.spec.provider === "kubernetes" && ["apps/deployment", "apps/statefulset", "apps/daemonset"].includes(resource.spec.type)),
      replayMode ? "Choose the event subject resource" : "Choose an observed Kubernetes workload",
    ],
  ];
  targets.forEach(([select, resources, prompt]) => {
    const selected = select.value;
    clear(select);
    const placeholder = node("option", "", resources.length ? prompt : "No eligible resources available");
    placeholder.value = "";
    select.append(placeholder);
    resources.forEach((resource) => {
      if (!resource.metadata.uid) return;
      const option = node("option", "", `${resourceName(resource)} · ${resource.spec.type}`);
      option.value = resource.metadata.uid;
      select.append(option);
    });
    if ([...select.options].some((option) => option.value === selected)) select.value = selected;
  });
  if (replayMode && state.pendingReplay && [...$("#action-resource").options].some((option) => option.value === state.pendingReplay.subject)) {
    $("#action-resource").value = state.pendingReplay.subject;
  }
}

function renderActionProposalMode() {
  const replayMode = $("#action-type").value === "event-delivery.requeue";
  $("#action-form-title").textContent = replayMode
    ? "Recover one quarantined event"
    : "Prepare a bounded restart";
  $("#action-form-note").textContent = replayMode
    ? "The proposal binds one exact quarantine generation. A different identity must approve it before one-shot execution."
    : "The proposal is bound to a completed investigation and the exact observed workload. A separate identity must approve it.";
  $("#action-resource-label").textContent = replayMode ? "Event subject resource" : "Target workload";
  $("#replay-binding").hidden = !replayMode;
  $("#replay-binding-value").textContent = state.pendingReplay
    ? `Outbox ${state.pendingReplay.outboxId} · ${compactIdentity(state.pendingReplay.eventId, 18)} · attempt ${state.pendingReplay.attempts}`
    : "Choose a quarantined event from Overview";
  $("#action-dry-run-help").textContent = replayMode
    ? "Dry-run verifies the exact generation. Live replay is high risk, preserves the event ID, and requires receiver deduplication."
    : "The default executor performs no mutation. Live execution requires protected server configuration too.";
  renderResourceOptions();
}

function showDetail(title, kicker, payload) {
  $("#detail-title").textContent = title;
  $("#detail-kicker").textContent = kicker;
  $("#detail-content").textContent = JSON.stringify(payload, null, 2);
  $("#detail-actions").hidden = true;
  $("#detail-dialog").showModal();
}

async function resourceDetail(resource, kind) {
  if (!resource.metadata.uid) return;
  try {
    const path = kind === "timeline"
      ? `/v1/resources/${encodeURIComponent(resource.metadata.uid)}/timeline?limit=50`
      : `/v1/resources/${encodeURIComponent(resource.metadata.uid)}/neighborhood?depth=1&direction=both&limit=50`;
    const payload = await api(path);
    showDetail(resourceName(resource), kind === "timeline" ? "Immutable timeline" : "Resource neighborhood", payload);
  } catch (error) {
    showNotice(`Could not retrieve resource detail (${error.message}).`, "error");
  }
}

function renderResources() {
  const query = $("#resource-search").value.trim().toLowerCase();
  const health = $("#health-filter").value;
  const resources = state.resources.filter((resource) => {
    const text = [resourceName(resource), resource.spec.provider, resource.spec.type, resource.metadata.uid].join(" ").toLowerCase();
    return (!query || text.includes(query)) && (health === "all" || resourceHealth(resource) === health);
  });
  const body = $("#resource-rows");
  clear(body);
  resources.forEach((resource) => {
    const row = node("tr");
    const identity = node("td");
    identity.append(node("strong", "", resourceName(resource)));
    identity.append(node("small", "", resource.metadata.uid || "UID assigned after ingestion"));
    row.append(identity);
    row.append(node("td", "", resource.spec.provider));
    row.append(node("td", "", resource.spec.type));
    const healthCell = node("td");
    const badge = node("span", "health-badge");
    badge.append(node("i", `health-dot ${resourceHealth(resource)}`));
    badge.append(node("span", "", resourceHealth(resource)));
    healthCell.append(badge);
    row.append(healthCell);
    row.append(node("td", "", formatDate(resource.metadata.observedAt)));
    const actions = node("td", "row-actions");
    const graph = node("button", "icon-button", "◇");
    graph.type = "button";
    graph.title = "Open neighborhood";
    graph.addEventListener("click", () => resourceDetail(resource, "neighborhood"));
    const timeline = node("button", "icon-button", "↺");
    timeline.type = "button";
    timeline.title = "Open timeline";
    timeline.addEventListener("click", () => resourceDetail(resource, "timeline"));
    actions.append(graph, timeline);
    row.append(actions);
    body.append(row);
  });
  $("#resource-empty").hidden = resources.length !== 0;
  $(".table-wrap").hidden = resources.length === 0;
  $("#resource-count").textContent = `${resources.length} resource${resources.length === 1 ? "" : "s"}`;
  renderResourceOptions();
  renderMetrics();
}

async function refreshResources() {
  if (!state.token) return;
  try {
    const response = await api("/v1/resources");
    state.resources = Array.isArray(response.items) ? response.items : [];
    renderResources();
  } catch (error) {
    showNotice(`Resource refresh failed (${error.message}).`, "error");
  }
}

async function addDemoResource() {
  if (!state.session) {
    $("#connection-dialog").showModal();
    return;
  }
  const button = $("#demo-resource-button");
  button.disabled = true;
  const now = new Date().toISOString();
  const payload = {
    apiVersion: "iip.platform/v1alpha1",
    kind: "Resource",
    metadata: {
      tenantId: state.session.metadata.tenantId,
      observedAt: now,
      observation: {
        sourceId: "kubernetes-local",
        streamId: "obs_c0dec0dec0dec0dec0dec0dec0dec0de",
        sequence: Date.now(),
        mode: "incremental",
        resourceVersion: String(Date.now()),
      },
      labels: { environment: "local-demo", team: "platform" },
    },
    spec: {
      provider: "kubernetes",
      type: "apps/deployment",
      externalId: "cluster-local/iip-demo/checkout-api",
      displayName: "checkout-api",
      attributes: { namespace: "iip-demo", replicas: 3, availableReplicas: 2 },
      relationships: [],
    },
    status: { health: "degraded", lifecycle: "active" },
  };
  try {
    await api("/v1/resources", { method: "POST", body: JSON.stringify(payload) });
    await refreshResources();
    showNotice("Demo Deployment added. It is intentionally degraded so you can investigate it.");
  } catch (error) {
    showNotice(`Could not add the demo resource (${error.message}).`, "error");
  } finally {
    button.disabled = false;
  }
}

async function cancelInvestigation() {
  const investigationId = state.activeInvestigationId;
  if (!investigationId || !state.session) return;
  const button = $("#cancel-investigation");
  button.disabled = true;
  button.textContent = "Requesting cancellation…";
  const requestedAt = new Date().toISOString();
  const payload = {
    apiVersion: "iip.platform/v1alpha1",
    kind: "InvestigationCancellationRequest",
    metadata: {
      id: identifier("can"),
      tenantId: state.session.metadata.tenantId,
      actorId: state.session.metadata.actorId,
      requestedAt,
    },
    spec: { investigationId, reasonCode: "operator-requested" },
  };
  try {
    const status = await api(`/v1/investigation-jobs/${encodeURIComponent(investigationId)}/cancel`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#investigation-state").textContent = status.spec.state === "cancelled" ? "Cancelled" : "Stopping safely";
    $("#investigation-state").className = "status-chip warning";
    showNotice("Cancellation was recorded. In-flight evidence will stop at its bounded deadline.");
  } catch (error) {
    showNotice(`Cancellation failed (${error.message}).`, "error");
    button.disabled = false;
    button.textContent = "Cancel safely";
  }
}

function pause(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function waitForInvestigationJob(investigationId, timeoutMilliseconds = 150000) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const job = await api(`/v1/investigation-jobs/${encodeURIComponent(investigationId)}`);
    const stateLabel = {
      queued: "Queued",
      running: `Running · attempt ${job.spec.attempts}`,
      "cancellation-requested": "Stopping safely",
      completed: "Completed",
      failed: "Failed",
      cancelled: "Cancelled",
    }[job.spec.state] || job.spec.state;
    $("#investigation-state").textContent = stateLabel;
    $("#investigation-state").className = `status-chip ${job.spec.state === "completed" ? "success" : job.spec.state === "failed" ? "danger" : "warning"}`;
    if (["completed", "failed", "cancelled"].includes(job.spec.state)) {
      if (job.spec.reportRef) {
        return api(`/v1/investigations/${encodeURIComponent(investigationId)}`);
      }
      if (job.spec.state === "cancelled") return null;
      throw new Error(job.spec.lastErrorCode || "investigation.job.failed");
    }
    await pause(500);
  }
  throw new Error("investigation.job.timeout");
}

function prepareInvestigation(resourceUid) {
  switchView("investigate");
  $("#investigation-resource").value = resourceUid || "";
  const resource = state.resources.find((candidate) => candidate.metadata.uid === resourceUid);
  if (resource) $("#investigation-question").value = `Why is ${resourceName(resource)} ${resourceHealth(resource)}?`;
  $("#investigation-question").focus();
}

function renderInvestigation(report) {
  const container = $("#investigation-result");
  clear(container);
  container.className = "report";
  const spec = report.spec;
  const stateChip = $("#investigation-state");
  stateChip.textContent = spec.outcome;
  stateChip.className = `status-chip ${spec.outcome === "conclusive" ? "success" : spec.outcome === "failed" ? "danger" : "warning"}`;
  container.append(node("p", "report-summary", spec.summary));
  const meta = node("div", "report-meta");
  [["Outcome", spec.outcome], ["Terminal reason", spec.terminalReason], ["Tool calls", String(spec.usage?.toolCalls ?? 0)]].forEach(([label, value]) => {
    const item = node("div");
    item.append(node("span", "", label));
    item.append(node("strong", "", value));
    meta.append(item);
  });
  container.append(meta);
  if (spec.signalPlan?.steps?.length) {
    container.append(node("h3", "", "Evidence plan"));
    const plan = node("div", "signal-plan");
    spec.signalPlan.steps.forEach((step) => {
      const item = node("div", "signal-step");
      item.append(node("strong", "", `${step.signal} · ${step.decision}`));
      item.append(node("span", "", `${step.origin === "protected-catalog" ? "Reviewed catalog" : "Request"} · ${step.reason}`));
      plan.append(item);
    });
    container.append(plan);
  }
  if (spec.hypotheses?.length) {
    container.append(node("h3", "", "Ranked hypotheses"));
    spec.hypotheses.forEach((hypothesis) => {
      const finding = node("div", "finding");
      finding.append(node("strong", "", `${hypothesis.rootCauseClass} · ${Math.round(hypothesis.confidence * 100)}%`));
      finding.append(node("p", "", hypothesis.statement));
      container.append(finding);
    });
  }
  if (spec.unknowns?.length) {
    container.append(node("h3", "", "Known unknowns"));
    spec.unknowns.forEach((unknown) => {
      const finding = node("div", "finding");
      finding.append(node("strong", "", `${unknown.impact} impact`));
      finding.append(node("p", "", unknown.statement));
      container.append(finding);
    });
  }
  if (spec.recommendations?.length) {
    container.append(node("h3", "", "Recommendations"));
    spec.recommendations.forEach((recommendation) => {
      const finding = node("div", "finding");
      finding.append(node("strong", "", `${recommendation.priority} · ${recommendation.type}`));
      finding.append(node("p", "", recommendation.description));
      container.append(finding);
    });
  }
  if (spec.evidenceIds?.length) {
    container.append(node("h3", "", "Evidence citations"));
    const citations = node("div", "citation-list");
    spec.evidenceIds.forEach((id) => {
      const citation = node("button", "citation", id);
      citation.type = "button";
      citation.addEventListener("click", () => {
        switchView("evidence");
        $("#evidence-id").value = id;
        lookupEvidence(id);
      });
      citations.append(citation);
    });
    container.append(citations);
  }
}

async function runInvestigation(event) {
  event.preventDefault();
  if (!state.session) {
    $("#connection-dialog").showModal();
    return;
  }
  const resourceUid = $("#investigation-resource").value;
  const question = $("#investigation-question").value.trim();
  if (!resourceUid || !question) return;
  const button = event.submitter;
  button.disabled = true;
  button.textContent = "Collecting evidence…";
  $("#investigation-state").textContent = "Running";
  $("#investigation-state").className = "status-chip warning";
  const end = new Date();
  const lookback = Number($("#investigation-lookback").value);
  const start = new Date(end.valueOf() - lookback * 60_000);
  const payload = {
    apiVersion: "iip.platform/v1alpha1",
    kind: "InvestigationRequest",
    metadata: {
      id: identifier("inv"),
      tenantId: state.session.metadata.tenantId,
      actorId: state.session.metadata.actorId,
      requestedAt: end.toISOString(),
      correlationId: `console-${identifier("run", 16)}`,
    },
    spec: {
      question,
      trigger: { type: "user", source: "urn:iip:console:user", summary: question },
      scope: { resourceUids: [resourceUid], timeRange: { start: start.toISOString(), end: end.toISOString() } },
      agentSelector: { id: "incident-investigator", version: "0.1.0" },
      evidenceTypes: ["kubernetes.resource-status", "kubernetes.event", "repository.context", "resource.change", "telemetry.metrics", "telemetry.logs"],
      allowedTools: ["resources/query", "events/search", "evidence/fetch", "telemetry/query"],
      budgets: { maxToolCalls: 8, maxWallTimeSeconds: 120, maxModelTokens: 0, maxCostUsd: 0, maxEvidenceItems: 16, maxIterations: 8 },
      maxAuthority: $("#allow-proposal").checked ? "propose" : "read",
      priority: "normal",
    },
  };
  state.activeInvestigationId = payload.metadata.id;
  $("#cancel-investigation").hidden = false;
  try {
    await api("/v1/investigation-jobs", { method: "POST", body: JSON.stringify(payload) });
    $("#investigation-state").textContent = "Queued";
    $("#investigation-state").className = "status-chip warning";
    showNotice("Investigation queued. A durable worker will continue even if this page closes.");
    const report = await waitForInvestigationJob(payload.metadata.id);
    if (!report) {
      showNotice("Investigation cancelled before a terminal report was needed.");
      return;
    }
    state.investigations.unshift(report);
    if (payload.spec.maxAuthority === "propose") {
      $("#action-investigation-id").value = report.metadata.id;
      $("#action-resource").value = resourceUid;
      if (state.pendingReplay?.subject === resourceUid) {
        $("#action-type").value = "event-delivery.requeue";
        renderActionProposalMode();
      }
    }
    renderInvestigation(report);
    renderMetrics();
    showNotice("Investigation completed and its terminal report was committed.");
  } catch (error) {
    $("#investigation-state").textContent = "Failed";
    $("#investigation-state").className = "status-chip danger";
    showNotice(`Investigation failed (${error.message}).`, "error");
  } finally {
    await refreshInvestigationCompletionSlo();
    state.activeInvestigationId = null;
    $("#cancel-investigation").hidden = true;
    $("#cancel-investigation").disabled = false;
    $("#cancel-investigation").textContent = "Cancel safely";
    button.disabled = false;
    button.textContent = "Run investigation";
  }
}

function renderLookup(container, values) {
  clear(container);
  container.className = "json-card";
  const list = node("dl");
  values.forEach(([label, value]) => {
    list.append(node("dt", "", label));
    list.append(node("dd", "", value ?? "—"));
  });
  container.append(list);
}

async function lookupEvidence(id) {
  try {
    const evidence = await api(`/v1/evidence/${encodeURIComponent(id)}`);
    state.evidence = [evidence, ...state.evidence.filter((item) => item.metadata.id !== id)];
    renderMetrics();
    renderLookup($("#evidence-result"), [
      ["Evidence ID", evidence.metadata.id],
      ["Type", evidence.spec.type],
      ["Summary", evidence.spec.summary],
      ["Provider", evidence.spec.source.provider],
      ["Integration", evidence.spec.source.integrationId],
      ["Content hash", evidence.spec.artifact.contentHash],
      ["Sensitivity", evidence.spec.handling.sensitivity],
      ["Retention", evidence.spec.handling.retentionClass],
      ["Recorded", formatDate(evidence.metadata.recordedAt)],
    ]);
  } catch (error) {
    showNotice(`Evidence lookup failed (${error.message}).`, "error");
  }
}

function pluginInvocationStatusClass(value) {
  if (value === "succeeded") return "success";
  if (["failed", "cancelled"].includes(value)) return "danger";
  return "warning";
}

function renderPluginInvocationStatus() {
  const status = state.pluginInvocationStatus;
  const chip = $("#plugin-invocation-state");
  const controls = $("#plugin-lifecycle-controls");
  if (!status?.spec || !status?.metadata) {
    chip.textContent = "No selection";
    chip.className = "status-chip neutral";
    controls.hidden = true;
    return;
  }

  const terminal = ["succeeded", "failed", "cancelled"].includes(status.spec.state);
  const cancellation = status.spec.cancellation;
  chip.textContent = status.spec.state;
  chip.className = `status-chip ${pluginInvocationStatusClass(status.spec.state)}`;
  renderLookup($("#plugin-invocation-result"), [
    ["Invocation ID", status.metadata.id],
    ["State", status.spec.state],
    ["Plugin", `${status.metadata.pluginId} · ${status.metadata.pluginVersion}`],
    ["Session", status.metadata.sessionId],
    ["Claimed", formatDate(status.spec.claimedAt)],
    ["Deadline", formatDate(status.spec.deadline)],
    ["Request digest", status.spec.requestDigest],
    ["Cancellation", cancellation ? `${cancellation.reasonCode} by ${cancellation.requestedBy}` : "Not requested"],
    ["Completed", status.spec.completedAt ? formatDate(status.spec.completedAt) : "Not terminal"],
    ["Result reference", status.spec.resultRef || "Not terminal"],
  ]);

  controls.hidden = terminal;
  $("#plugin-cancellation-operation").hidden = status.spec.state !== "claimed";
  const reconciliation = $("#plugin-reconciliation-operation");
  reconciliation.hidden = terminal || !hasRole("platform-admin");
  const deadlineReached = Date.now() >= Date.parse(status.spec.deadline);
  $("#plugin-reconcile").disabled = !deadlineReached;
  $("#plugin-reconciliation-help").textContent = deadlineReached
    ? "This records an unknown terminal outcome and never replays the invocation."
    : `Available after ${formatDate(status.spec.deadline)}. Reconciliation never replays the invocation.`;
}

async function lookupPluginInvocation(invocationId) {
  try {
    const status = await api(`/v1/plugin-invocations/${encodeURIComponent(invocationId)}/status`);
    state.pluginInvocationStatus = status;
    $("#plugin-invocation-id").value = status.metadata.id;
    renderPluginInvocationStatus();
  } catch (error) {
    state.pluginInvocationStatus = null;
    renderPluginInvocationStatus();
    showNotice(`Plugin invocation lookup failed (${error.message}).`, "error");
  }
}

async function cancelPluginInvocation() {
  const invocationId = state.pluginInvocationStatus?.metadata?.id;
  if (!invocationId || !state.session) return;
  const button = $("#plugin-cancel");
  button.disabled = true;
  button.textContent = "Recording intent…";
  const payload = {
    apiVersion: "iip.platform/v1alpha1",
    kind: "PluginInvocationCancellationRequest",
    metadata: {
      id: identifier("pcn"),
      tenantId: state.session.metadata.tenantId,
      actorId: state.session.metadata.actorId,
      requestedAt: new Date().toISOString(),
    },
    spec: {
      invocationId,
      reasonCode: $("#plugin-cancellation-reason").value,
    },
  };
  try {
    state.pluginInvocationStatus = await api(`/v1/plugin-invocations/${encodeURIComponent(invocationId)}/cancel`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderPluginInvocationStatus();
    showNotice("Cancellation intent is durable. The isolated runner will stop cooperatively and commit its terminal result.");
  } catch (error) {
    showNotice(`Plugin cancellation failed (${error.message}).`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Request cancellation";
  }
}

async function reconcilePluginInvocation() {
  const invocationId = state.pluginInvocationStatus?.metadata?.id;
  if (!invocationId || !state.session || !hasRole("platform-admin")) return;
  const button = $("#plugin-reconcile");
  button.disabled = true;
  button.textContent = "Closing safely…";
  const payload = {
    apiVersion: "iip.platform/v1alpha1",
    kind: "PluginInvocationReconciliationRequest",
    metadata: {
      id: identifier("prc"),
      tenantId: state.session.metadata.tenantId,
      actorId: state.session.metadata.actorId,
      requestedAt: new Date().toISOString(),
    },
    spec: {
      invocationId,
      reasonCode: $("#plugin-reconciliation-reason").value,
    },
  };
  try {
    state.pluginInvocationStatus = await api(`/v1/plugin-invocations/${encodeURIComponent(invocationId)}/reconcile`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderPluginInvocationStatus();
    showNotice("The invocation is terminal with outcome unknown. No work was replayed.");
  } catch (error) {
    showNotice(`Plugin reconciliation failed (${error.message}).`, "error");
  } finally {
    button.textContent = "Close unknown outcome";
    renderPluginInvocationStatus();
  }
}

function hasRole(role) {
  return Boolean(state.session?.spec?.roles?.includes(role));
}

function actionStatusClass(value) {
  if (["succeeded", "dry-run", "approved"].includes(value)) return "success";
  if (["failed", "rejected", "denied", "expired", "manual-reconciliation-required"].includes(value)) return "danger";
  return "warning";
}

function renderActionRows() {
  const body = $("#action-rows");
  clear(body);
  state.actionWorkflows.forEach((workflow) => {
    const proposal = workflow.spec.proposal;
    const row = node("tr");
    const identity = node("td");
    identity.append(node("strong", "", proposal.spec.actionType));
    identity.append(node("small", "", workflow.metadata.id));
    row.append(identity);
    const stateCell = node("td");
    stateCell.append(node("span", `status-chip ${actionStatusClass(workflow.spec.state)}`, workflow.spec.state));
    row.append(stateCell);
    row.append(node("td", "", proposal.spec.actionType === "event-delivery.requeue"
      ? `Outbox ${proposal.spec.parameters.outboxId}`
      : proposal.spec.parameters.workloadName));
    row.append(node("td", "", proposal.spec.dryRun ? "Dry-run" : "Live"));
    row.append(node("td", "", formatDate(proposal.metadata.createdAt)));
    const actions = node("td", "row-actions");
    const open = node("button", "text-button", "Review →");
    open.type = "button";
    open.addEventListener("click", () => selectAction(workflow.metadata.id));
    actions.append(open);
    row.append(actions);
    body.append(row);
  });
  $("#action-empty").hidden = state.actionWorkflows.length !== 0;
  $("#action-load-more").hidden = !state.actionCursor;
}

function renderActionWorkflow(workflow) {
  state.selectedActionId = workflow.metadata.id;
  const { proposal, approval, executionStatus, result, state: workflowState } = workflow.spec;
  const stateChip = $("#action-state");
  stateChip.textContent = workflowState;
  stateChip.className = `status-chip ${actionStatusClass(workflowState)}`;
  const target = proposal.spec.actionType === "event-delivery.requeue"
    ? `Outbox ${proposal.spec.parameters.outboxId} · ${compactIdentity(proposal.spec.parameters.eventId, 18)}`
    : `${proposal.spec.parameters.namespace}/${proposal.spec.parameters.workloadKind}/${proposal.spec.parameters.workloadName}`;
  renderLookup($("#action-result"), [
    ["Action ID", workflow.metadata.id],
    ["Proposer", proposal.metadata.actorId],
    ["Investigation", proposal.spec.investigationId],
    ["Target", target],
    ["Mode", proposal.spec.dryRun ? "Non-mutating dry-run" : "Explicit live request"],
    ["Risk / reversible", `${proposal.spec.risk} / ${String(proposal.spec.reversible)}`],
    ["Expires", formatDate(proposal.spec.expiresAt)],
    ["Decision", approval ? `${approval.spec.decision} by ${approval.metadata.approverId}` : "Pending independent review"],
    ["Rationale", approval?.spec?.rationale || "—"],
    ["Executor", executionStatus?.spec?.executorActorId || "—"],
    ["Verification", result?.spec?.verification?.summary || executionStatus?.spec?.summary || "Not run"],
    ["Error code", result?.spec?.errorCode || "—"],
    ["Rollback", result?.spec?.rollback ? `${result.spec.rollback.status}: ${result.spec.rollback.summary}` : "—"],
    ["Audit reference", result?.spec?.auditRef || "Pending"],
  ]);

  const canDecide = workflowState === "pending-approval" && hasRole("approver") && proposal.metadata.actorId !== state.session.metadata.actorId;
  const canExecute = workflowState === "approved" && hasRole("executor");
  $("#action-controls").hidden = false;
  $("#action-rationale-label").hidden = !canDecide;
  $("#action-approve").hidden = !canDecide;
  $("#action-reject").hidden = !canDecide;
  $("#action-execute").hidden = !canExecute;
  const help = $("#action-role-help");
  if (canDecide) help.textContent = "Your approver role can decide this proposal. The proposer cannot self-approve.";
  else if (canExecute) help.textContent = "Your executor role can claim this approved operation exactly once.";
  else if (workflowState === "pending-approval" && proposal.metadata.actorId === state.session.metadata.actorId) help.textContent = "Connect with a different approver identity to preserve separation of duties.";
  else if (workflowState === "pending-approval") help.textContent = "Connect with an approver identity to review this proposal.";
  else if (workflowState === "approved") help.textContent = "Connect with an executor identity to run this approved operation once.";
  else help.textContent = "This workflow is immutable at its current terminal or non-actionable state.";
}

async function refreshActions(append = false) {
  if (!state.token) return;
  try {
    const query = new URLSearchParams({ limit: "25" });
    if (append && state.actionCursor) query.set("cursor", state.actionCursor);
    const page = await api(`/v1/actions?${query}`);
    const incoming = Array.isArray(page.spec?.items) ? page.spec.items : [];
    if (append) {
      const known = new Set(state.actionWorkflows.map((item) => item.metadata.id));
      state.actionWorkflows.push(...incoming.filter((item) => !known.has(item.metadata.id)));
    } else {
      state.actionWorkflows = incoming;
    }
    state.actionCursor = page.spec?.page?.nextCursor || null;
    renderActionRows();
    const selected = state.actionWorkflows.find((item) => item.metadata.id === state.selectedActionId);
    if (selected) renderActionWorkflow(selected);
  } catch (error) {
    showNotice(`Action queue refresh failed (${error.message}).`, "error");
  }
}

async function selectAction(id) {
  try {
    const workflow = await api(`/v1/actions/${encodeURIComponent(id)}/workflow`);
    const index = state.actionWorkflows.findIndex((item) => item.metadata.id === id);
    if (index >= 0) state.actionWorkflows[index] = workflow;
    else state.actionWorkflows.unshift(workflow);
    renderActionRows();
    renderActionWorkflow(workflow);
  } catch (error) {
    showNotice(`Action lookup failed (${error.message}).`, "error");
  }
}

async function proposeAction(event) {
  event.preventDefault();
  const resource = state.resources.find((item) => item.metadata.uid === $("#action-resource").value);
  const investigationId = $("#action-investigation-id").value.trim();
  if (!resource || !investigationId) return;
  const actionType = $("#action-type").value;
  let parameters;
  if (actionType === "event-delivery.requeue") {
    if (!hasRole("platform-admin") || !state.pendingReplay || state.pendingReplay.subject !== resource.metadata.uid) {
      showNotice("Choose a current quarantined event and investigate its exact subject as a platform administrator.", "error");
      return;
    }
    parameters = {
      outboxId: state.pendingReplay.outboxId,
      eventId: state.pendingReplay.eventId,
      quarantinedAt: state.pendingReplay.quarantinedAt,
      attempts: state.pendingReplay.attempts,
    };
  } else {
    const workloadKinds = {
      "apps/deployment": "deployment",
      "apps/statefulset": "statefulset",
      "apps/daemonset": "daemonset",
    };
    const workloadKind = workloadKinds[resource.spec.type];
    const namespace = resource.spec.attributes?.namespace;
    const workloadName = resource.spec.externalId?.split("/").pop();
    if (!workloadKind || typeof namespace !== "string" || !workloadName) {
      showNotice("The selected resource does not have a safe Kubernetes workload identity.", "error");
      return;
    }
    parameters = { namespace, workloadKind, workloadName };
  }
  const button = event.submitter;
  button.disabled = true;
  button.textContent = "Creating immutable proposal…";
  const expiresAt = new Date(Date.now() + 30 * 60_000).toISOString();
  const payload = {
    investigationId,
    actionType,
    targetResourceUid: resource.metadata.uid,
    parameters,
    idempotencyKey: `console-${investigationId.slice(4, 12)}-${identifier("act", 16)}`,
    expiresAt,
    dryRun: $("#action-dry-run").checked,
  };
  try {
    const proposal = await api("/v1/actions/proposals", { method: "POST", body: JSON.stringify(payload) });
    await refreshActions();
    await selectAction(proposal.metadata.id);
    if (actionType === "event-delivery.requeue") {
      state.pendingReplay = null;
      renderActionProposalMode();
    }
    showNotice("Proposal committed. A different approver identity can now review it.");
  } catch (error) {
    showNotice(`Proposal failed (${error.message}).`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Create proposal";
  }
}

async function decideSelectedAction(decision) {
  const rationale = $("#action-rationale").value.trim();
  if (!state.selectedActionId || !rationale) {
    showNotice("A concise approval rationale is required.", "error");
    return;
  }
  const buttons = [$("#action-approve"), $("#action-reject")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await api(`/v1/actions/${encodeURIComponent(state.selectedActionId)}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, rationale }),
    });
    $("#action-rationale").value = "";
    await selectAction(state.selectedActionId);
    showNotice(`Proposal ${decision}. The immutable decision is now part of the workflow.`);
  } catch (error) {
    showNotice(`Decision failed (${error.message}).`, "error");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function executeSelectedAction() {
  if (!state.selectedActionId) return;
  const button = $("#action-execute");
  button.disabled = true;
  button.textContent = "Claiming once…";
  try {
    await api(`/v1/actions/${encodeURIComponent(state.selectedActionId)}/execute`, {
      method: "POST",
      body: "{}",
    });
    await selectAction(state.selectedActionId);
    await Promise.all([refreshEventDeliveryHealth(), refreshEventDeliverySlo()]);
    showNotice("Execution reached a durable terminal result. Duplicate delivery cannot repeat impact.");
  } catch (error) {
    showNotice(`Execution failed (${error.message}).`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Execute once";
  }
}

function bindEvents() {
  $$("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$("[data-go]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.go)));
  $("#identity-button").addEventListener("click", () => $("#connection-dialog").showModal());
  $("#connection-close").addEventListener("click", () => $("#connection-dialog").close());
  $("#refresh-button").addEventListener("click", async () => {
    await Promise.all([checkHealth(), refreshRuntimeVersion(), refreshTelemetryDeploymentHealth(), refreshTelemetryExportSlo(), refreshTelemetryExportBurnRate(), refreshEventDeliveryHealth(), refreshEventDeliverySlo(), refreshInvestigationCompletionSlo(), refreshEvidenceRetention(), refreshResources(), refreshActions()]);
    showNotice("Live platform state refreshed.");
  });
  $("#connection-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#connect-button");
    button.disabled = true;
    button.textContent = "Verifying…";
    try {
      const token = $("#token-input").value.trim();
      if (!token) {
        $("#connection-error").textContent = "Enter an access token before connecting.";
        $("#connection-error").hidden = false;
        return;
      }
      await connect(token, $("#remember-token").checked);
      $("#token-input").value = "";
    } catch (_error) {
      // The connection panel already contains the stable error.
    } finally {
      button.disabled = false;
      button.textContent = "Connect securely";
    }
  });
  $("#oidc-button").addEventListener("click", async () => {
    const button = $("#oidc-button");
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Preparing secure sign-in…";
    $("#connection-error").hidden = true;
    try {
      await beginOidcSignIn();
    } catch (_error) {
      $("#connection-error").textContent = "Single sign-on could not start. Verify the deployment's redirect and identity-provider configuration.";
      $("#connection-error").hidden = false;
      button.disabled = false;
      button.textContent = original;
    }
  });
  $("#demo-resource-button").addEventListener("click", addDemoResource);
  $("#resource-search").addEventListener("input", renderResources);
  $("#health-filter").addEventListener("change", renderResources);
  $("#investigation-form").addEventListener("submit", runInvestigation);
  $("#cancel-investigation").addEventListener("click", cancelInvestigation);
  $("#evidence-form").addEventListener("submit", (event) => {
    event.preventDefault();
    lookupEvidence($("#evidence-id").value.trim());
  });
  $("#plugin-invocation-form").addEventListener("submit", (event) => {
    event.preventDefault();
    lookupPluginInvocation($("#plugin-invocation-id").value.trim());
  });
  $("#plugin-cancel").addEventListener("click", cancelPluginInvocation);
  $("#plugin-reconcile").addEventListener("click", reconcilePluginInvocation);
  $("#action-proposal-form").addEventListener("submit", proposeAction);
  $("#action-type").addEventListener("change", renderActionProposalMode);
  $("#action-refresh").addEventListener("click", () => refreshActions());
  $("#action-load-more").addEventListener("click", () => refreshActions(true));
  $("#action-approve").addEventListener("click", () => decideSelectedAction("approved"));
  $("#action-reject").addEventListener("click", () => decideSelectedAction("rejected"));
  $("#action-execute").addEventListener("click", executeSelectedAction);
  $("#detail-close").addEventListener("click", () => $("#detail-dialog").close());
  $("#runtime-details").addEventListener("click", () => {
    if (!state.runtimeVersion) return;
    $("#detail-kicker").textContent = "Runtime identity";
    $("#detail-title").textContent = "Verified version report";
    $("#detail-content").textContent = JSON.stringify(state.runtimeVersion, null, 2);
    $("#detail-actions").hidden = true;
    $("#detail-dialog").showModal();
  });
  $("#telemetry-health-details").addEventListener("click", () => {
    if (!state.telemetryDeploymentHealth) return;
    $("#detail-kicker").textContent = "Portable observability path";
    $("#detail-title").textContent = "Deployment telemetry delivery";
    $("#detail-content").textContent = JSON.stringify(state.telemetryDeploymentHealth, null, 2);
    $("#detail-actions").hidden = true;
    $("#detail-dialog").showModal();
  });
  $("#telemetry-slo-details").addEventListener("click", () => {
    if (!state.telemetryExportSlo) return;
    $("#detail-kicker").textContent = "Portable observability path";
    $("#detail-title").textContent = "Telemetry export SLO";
    $("#detail-content").textContent = JSON.stringify(state.telemetryExportSlo, null, 2);
    $("#detail-actions").hidden = true;
    $("#detail-dialog").showModal();
  });
  $("#telemetry-burn-rate-details").addEventListener("click", () => {
    if (!state.telemetryExportBurnRate) return;
    $("#detail-kicker").textContent = "Portable observability path";
    $("#detail-title").textContent = "Telemetry export burn rate";
    $("#detail-content").textContent = JSON.stringify(state.telemetryExportBurnRate, null, 2);
    $("#detail-actions").hidden = true;
    $("#detail-dialog").showModal();
  });
  $("#delivery-details").addEventListener("click", () => {
    if (!state.eventDeliveryHealth) return;
    $("#detail-kicker").textContent = "Event delivery";
    $("#detail-title").textContent = "Tenant outbox and quarantine";
    $("#detail-content").textContent = JSON.stringify(state.eventDeliveryHealth, null, 2);
    state.inspectedReplay = state.eventDeliveryHealth.spec?.quarantine?.items?.[0] || null;
    $("#detail-actions").hidden = !state.inspectedReplay || !hasRole("platform-admin");
    $("#detail-dialog").showModal();
  });
  $("#delivery-slo-details").addEventListener("click", () => {
    if (!state.eventDeliverySlo) return;
    $("#detail-kicker").textContent = "Event delivery objective";
    $("#detail-title").textContent = "Rolling publication SLO";
    $("#detail-content").textContent = JSON.stringify(state.eventDeliverySlo, null, 2);
    $("#detail-actions").hidden = true;
    $("#detail-dialog").showModal();
  });
  $("#investigation-slo-details").addEventListener("click", () => {
    if (!state.investigationCompletionSlo) return;
    $("#detail-kicker").textContent = "Investigation reliability objective";
    $("#detail-title").textContent = "Rolling useful-completion SLO";
    $("#detail-content").textContent = JSON.stringify(state.investigationCompletionSlo, null, 2);
    $("#detail-actions").hidden = true;
    $("#detail-dialog").showModal();
  });
  $("#evidence-retention-details").addEventListener("click", () => {
    if (!state.evidenceRetention) return;
    $("#detail-kicker").textContent = "Evidence lifecycle policy";
    $("#detail-title").textContent = "Tenant artifact retention";
    $("#detail-content").textContent = JSON.stringify(state.evidenceRetention, null, 2);
    $("#detail-actions").hidden = true;
    $("#detail-dialog").showModal();
  });
  $("#delivery-recovery").addEventListener("click", () => {
    if (!state.inspectedReplay || !hasRole("platform-admin")) return;
    state.pendingReplay = { ...state.inspectedReplay };
    $("#detail-dialog").close();
    prepareInvestigation(state.pendingReplay.subject);
    $("#investigation-question").value = `Why did event ${state.pendingReplay.eventId} exhaust delivery retries, and is the receiver ready for an idempotent replay?`;
    $("#allow-proposal").checked = true;
    showNotice("Recovery scope prepared. Run the investigation before creating a replay proposal.");
  });
}

async function start() {
  bindEvents();
  renderResources();
  renderActionProposalMode();
  renderRuntimeVersion();
  renderTelemetryDeploymentHealth();
  renderTelemetryExportSlo();
  renderTelemetryExportBurnRate();
  renderEventDeliverySlo();
  renderInvestigationCompletionSlo();
  renderEvidenceRetention();
  renderPluginInvocationStatus();
  await Promise.all([checkHealth(), loadConsoleAuthentication()]);
  configureConnectionDialog();
  if (oidcCallbackPresent()) {
    $("#connection-dialog").showModal();
    $("#oidc-button").disabled = true;
    $("#oidc-button").textContent = "Completing secure sign-in…";
    try {
      await completeOidcCallback();
      return;
    } catch (_error) {
      $("#connection-error").textContent = "Single sign-on did not complete safely. Start a new sign-in attempt or use an issued access token.";
      $("#connection-error").hidden = false;
      $("#oidc-button").disabled = false;
      configureConnectionDialog();
    }
  }
  const remembered = sessionStorage.getItem(REMEMBERED_TOKEN_KEY);
  if (remembered) {
    $("#remember-token").checked = true;
    try {
      await connect(remembered, true);
      return;
    } catch (_error) {
      // Fall through to an explicit connection prompt.
    }
  }
  if (!$("#connection-dialog").open) $("#connection-dialog").showModal();
}

start();
