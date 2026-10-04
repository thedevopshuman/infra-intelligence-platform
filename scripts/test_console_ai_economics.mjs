#!/usr/bin/env node

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const scriptPath = new URL("../src/iip/surfaces/static/app.js", import.meta.url);
const examplePath = new URL(
  "../contracts/examples/ai-allocation-report.json",
  import.meta.url,
);
const savingsPagePath = new URL(
  "../contracts/examples/ai-savings-finding-page.json",
  import.meta.url,
);
const source = readFileSync(scriptPath, "utf8");
const sourceWithoutStart = source.replace(/\nstart\(\);\s*$/, "");
assert.notEqual(sourceWithoutStart, source, "console entrypoint was not isolated");

const selections = {
  "#ai-report-window": { value: "24" },
  "#ai-report-group": { value: "application" },
};
const elements = new Map();

function element() {
  const descendants = new Map();
  return {
    className: "",
    firstChild: null,
    hidden: false,
    textContent: "",
    querySelector(selector) {
      if (!descendants.has(selector)) descendants.set(selector, element());
      return descendants.get(selector);
    },
    removeChild() {},
  };
}

const context = {
  console,
  document: {
    querySelector(selector) {
      if (selections[selector]) return selections[selector];
      if (!elements.has(selector)) elements.set(selector, element());
      return elements.get(selector);
    },
  },
};
vm.createContext(context);
vm.runInContext(
  `${sourceWithoutStart}
globalThis.consoleContractTest = {
  state,
  validateAiAllocationReport,
  validateAiSavingsFindingPage,
  formatCalculatedCost,
  formatPotentialSaving,
  renderAiAllocationReport,
  selectedAiScope,
};`,
  context,
);

const subject = context.consoleContractTest;
subject.state.session = { metadata: { tenantId: "local" } };
const example = JSON.parse(readFileSync(examplePath, "utf8"));
const savingsPage = JSON.parse(readFileSync(savingsPagePath, "utf8"));
const expectedScope = {
  start: example.spec.scope.start,
  end: example.spec.scope.end,
  groupBy: example.spec.scope.groupBy,
};

assert.equal(subject.validateAiAllocationReport(example, expectedScope), example);
const savingsScope = savingsPage.spec.scope;
assert.equal(subject.validateAiSavingsFindingPage(savingsPage, savingsScope), savingsPage);
assert.equal(
  subject.formatCalculatedCost(example.spec.totals.pricedCost),
  "USD 0.03147 est.",
);
assert.equal(subject.formatCalculatedCost(undefined), "Unresolved");
assert.equal(
  subject.formatPotentialSaving(savingsPage.spec.items[0].spec.potentialSavings),
  "USD 0.36 est.",
);
assert.equal(subject.formatPotentialSaving({ status: "unpriced" }), "Unpriced");
assert.equal(
  subject.formatPotentialSaving({ status: "unresolved" }),
  "Billing unresolved",
);

if (!elements.has("#ai-metric-cost")) {
  elements.set("#ai-metric-cost", element());
}
elements.get("#ai-metric-cost").textContent = "USD 99.00 est.";
subject.state.aiAllocationReport = null;
subject.state.aiAllocationError = "ai.history.retired";
subject.renderAiAllocationReport();
assert.equal(elements.get("#ai-report-state").textContent, "Source data retired");
assert.equal(
  elements.get("#ai-allocation-empty").querySelector("h3").textContent,
  "Historical report unavailable",
);
assert.equal(
  elements.get("#ai-allocation-empty").querySelector("p").textContent,
  "One or more source records needed for this interval were retired under the tenant's retention policy. No totals are shown because a complete report can no longer be reconstructed. Choose a newer interval or contact an administrator.",
);
assert.equal(elements.get("#ai-metric-cost").textContent, "—");
assert.notEqual(
  elements.get("#ai-allocation-empty").querySelector("h3").textContent,
  "No AI usage in this window",
);

const selectedScope = subject.selectedAiScope();
assert.match(selectedScope.start, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
assert.match(selectedScope.end, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
assert.equal(selectedScope.groupBy, "application");

function expectInvalid(mutate) {
  const candidate = structuredClone(example);
  mutate(candidate);
  assert.throws(
    () => subject.validateAiAllocationReport(candidate, expectedScope),
    /ai[.]allocation[.]response[.]invalid/,
  );
}

expectInvalid((candidate) => { candidate.metadata.tenantId = "another-tenant"; });
expectInvalid((candidate) => { candidate.metadata.generatedAt = "0"; });
expectInvalid((candidate) => { candidate.spec.scope.start = "2026-09-05T09:59:59Z"; });
expectInvalid((candidate) => { candidate.spec.coverage.pricedRecords += 1; });
expectInvalid((candidate) => { candidate.spec.totals.pricedCost.costBasis = "invoice"; });
expectInvalid((candidate) => { candidate.spec.groups[0].pricedCost.totalSubunits += 1; });
expectInvalid((candidate) => {
  candidate.spec.groups.forEach((group) => { delete group.pricedCost; });
});
expectInvalid((candidate) => { candidate.spec.groups[0].unexpected = "untrusted"; });

function expectInvalidSavings(mutate) {
  const candidate = structuredClone(savingsPage);
  mutate(candidate);
  assert.throws(
    () => subject.validateAiSavingsFindingPage(candidate, savingsScope),
    /ai[.]savings[.]response[.]invalid/,
  );
}

expectInvalidSavings((candidate) => { candidate.metadata.tenantId = "another-tenant"; });
expectInvalidSavings((candidate) => { candidate.spec.scope.end = "2026-09-05T23:00:00Z"; });
expectInvalidSavings((candidate) => { candidate.spec.items[0].metadata.evaluatedAt = "2026-09-06T00:00:00Z"; });
expectInvalidSavings((candidate) => { candidate.spec.items[0].spec.recommendation.requiresValidation = false; });
expectInvalidSavings((candidate) => { candidate.spec.items[0].spec.potentialSavings.amountSubunits = Number.MAX_SAFE_INTEGER + 1; });
expectInvalidSavings((candidate) => { candidate.spec.items[0].spec.evidenceRefs[0].type = "prompt"; });
expectInvalidSavings((candidate) => { candidate.spec.items[0].spec.unexpected = "untrusted"; });
expectInvalidSavings((candidate) => {
  candidate.spec.page.hasMore = true;
});

console.log("AI Economics console contract checks passed");
