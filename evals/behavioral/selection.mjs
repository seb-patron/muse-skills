const SPIKE_PROVIDER_LABELS = [
  "spike-current",
  "spike-minimal",
  "spike-risk-first",
  "spike-upstream-adapted",
];

function quoteRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function parseFinalists(raw, allowed = SPIKE_PROVIDER_LABELS) {
  const labels = String(raw ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const unique = [...new Set(labels)];
  const unknown = unique.filter((label) => !allowed.includes(label));
  if (unique.length !== 2 || unknown.length > 0) {
    throw new Error(
      `SPIKE_FINALISTS must contain exactly two distinct allowed provider labels; ` +
      `got ${labels.join(",") || "nothing"}`,
    );
  }
  return {
    labels: unique,
    filter: `^(?:${unique.map(quoteRegex).join("|")})$`,
  };
}

export function selectProviderLabels(labels, filter) {
  const expression = new RegExp(filter);
  return labels.filter((label) => expression.test(label));
}

export function hasExactRowCardinality(rows, providerLabels, caseIds, repeat) {
  if (!Array.isArray(rows) || rows.length !== providerLabels.length * caseIds.length * repeat) {
    return false;
  }
  const counts = new Map();
  for (const row of rows) {
    const key = `${row.provider?.label}\0${row.metadata?.case_id}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return providerLabels.every((label) =>
    caseIds.every((caseId) => counts.get(`${label}\0${caseId}`) === repeat),
  );
}

export function isCompletedPromptfooExit(status) {
  // Promptfoo 0.123 returns 100 when evaluation rows complete but one or more
  // assertions fail. That is quality evidence, not an infrastructure failure.
  return status === 0 || status === 100;
}

export function hasCompleteNormalizedRows(payload, expectedRows) {
  const aggregate = payload?.aggregate;
  return aggregate?.rows === expectedRows &&
    aggregate?.completed === expectedRows && aggregate?.errors === 0;
}

export { SPIKE_PROVIDER_LABELS };
