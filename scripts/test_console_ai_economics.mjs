#!/usr/bin/env node

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const scriptPath = new URL("../src/iip/surfaces/static/app.js", import.meta.url);
const examplePath = new URL(
  "../contracts/examples/ai-allocation-report.json",
  import.meta.url,
);
const source = readFileSync(scriptPath, "utf8");
const sourceWithoutStart = source.replace(/\nstart\(\);\s*$/, "");
assert.notEqual(sourceWithoutStart, source, "console entrypoint was not isolated");

const selections = {
  "#ai-report-window": { value: "24" },
  "#ai-report-group": { value: "application" },
};
const context = {
  console,
  document: {
    querySelector(selector) {
      return selections[selector];
    },
  },
};
vm.createContext(context);
vm.runInContext(
  `${sourceWithoutStart}
globalThis.consoleContractTest = {
  state,
  validateAiAllocationReport,
  formatCalculatedCost,
  selectedAiScope,
};`,
  context,
);

const subject = context.consoleContractTest;
subject.state.session = { metadata: { tenantId: "local" } };
const example = JSON.parse(readFileSync(examplePath, "utf8"));
const expectedScope = {
  start: example.spec.scope.start,
  end: example.spec.scope.end,
  groupBy: example.spec.scope.groupBy,
};

assert.equal(subject.validateAiAllocationReport(example, expectedScope), example);
assert.equal(
  subject.formatCalculatedCost(example.spec.totals.pricedCost),
  "USD 0.03147 est.",
);
assert.equal(subject.formatCalculatedCost(undefined), "Unresolved");

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

console.log("AI Economics console contract checks passed");
