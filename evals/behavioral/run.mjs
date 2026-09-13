import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  hasExactRowCardinality,
  parseFinalists,
  selectProviderLabels,
  SPIKE_PROVIDER_LABELS,
} from "./selection.mjs";

const mode = process.argv[2] ?? "smoke";
const extraArgs = process.argv.slice(3);
const behavioralConfig = "evals/behavioral/promptfooconfig.yaml";
const behavioralOutput = "evals/behavioral/results/latest.json";
const crossModelConfig = "evals/behavioral/cross-model-promptfooconfig.yaml";
const crossModelOutput = "evals/behavioral/results/cross-model-latest.json";
const spikeConfig = "evals/behavioral/spike-promptfooconfig.yaml";
const spikeScoring = "evals/behavioral/scoring.py";

const spikeStages = {
  "spike-train": {
    split: "train",
    cases: ["pr1-broken-promptfoo", "pr1-structural-lint-header-gap", "clean-control-main"],
    repeat: 1,
    providers: SPIKE_PROVIDER_LABELS,
    expectedRows: 12,
    output: "evals/behavioral/results/spike-train.json",
  },
  "spike-validation": {
    split: "validation",
    cases: ["clean-control-pr1-merge", "clean-control-pr3-merge"],
    repeat: 3,
    expectedRows: 12,
    output: "evals/behavioral/results/spike-validation.json",
  },
  "spike-heldout": {
    split: "heldout",
    cases: ["pr2-review-loop", "pr4-cross-model-reference", "pr3-behavioral-baseline"],
    repeat: 1,
    expectedRows: 3,
    output: "evals/behavioral/results/spike-heldout.json",
  },
};

const commands = {
  validate: ["validate", "config", "-c", behavioralConfig],
  smoke: [
    "eval", "-c", behavioralConfig, "--no-cache", "--repeat", "1", "-o", behavioralOutput,
  ],
  baseline: [
    "eval", "-c", behavioralConfig, "--no-cache", "--repeat", "3", "-o", behavioralOutput,
  ],
  "cross-validate": ["validate", "config", "-c", crossModelConfig],
  "cross-smoke": [
    "eval", "-c", crossModelConfig, "--no-cache", "--repeat", "1", "-o", crossModelOutput,
  ],
  "cross-baseline": [
    "eval", "-c", crossModelConfig, "--no-cache", "--repeat", "3", "-o", crossModelOutput,
  ],
  "spike-validate": ["validate", "config", "-c", spikeConfig],
};

for (const [stage, settings] of Object.entries(spikeStages)) {
  commands[stage] = [
    "eval", "-c", spikeConfig, "--no-cache", "--repeat", String(settings.repeat),
    "--filter-metadata", `split=${settings.split}`, "-o", settings.output,
  ];
}

if (!(mode in commands)) {
  console.error(`unknown mode ${mode}; expected ${Object.keys(commands).join(", ")}`);
  process.exit(2);
}

function runSpikePreflight(args) {
  const result = spawnSync(
    process.env.SPIKE_PYTHON ?? "uv",
    process.env.SPIKE_PYTHON
      ? ["evals/behavioral/experiment.py", ...args]
      : ["run", "--with", "PyYAML==6.0.3", "python", "evals/behavioral/experiment.py", ...args],
    { stdio: "inherit", env: process.env },
  );
  if (result.error) console.error(result.error.message);
  return !result.error && result.status === 0;
}

function resultRows(output) {
  const parsed = JSON.parse(readFileSync(output, "utf8"));
  const inner = parsed.results ?? parsed;
  const rows = inner.results;
  if (!Array.isArray(rows)) throw new Error(`Promptfoo output has no results array: ${output}`);
  return rows;
}

function verifySpikeOutput(stageName, finalistLabels) {
  const settings = spikeStages[stageName];
  const output = resolve(settings.output);
  let rows;
  try {
    rows = resultRows(output);
  } catch (error) {
    console.error(error.message);
    return false;
  }
  const providers = [...new Set(rows.map((row) => row.provider?.label).filter(Boolean))];
  const expectedProviders = finalistLabels ?? settings.providers;
  const splitOk = rows.every((row) => row.metadata?.split === settings.split);
  const providersOk = providers.length === expectedProviders.length &&
    expectedProviders.every((label) => providers.includes(label));
  const cardinalityOk = hasExactRowCardinality(
    rows,
    expectedProviders,
    settings.cases,
    settings.repeat,
  );
  if (rows.length !== settings.expectedRows || !splitOk || !providersOk || !cardinalityOk) {
    console.error(
      `${stageName} row contract failed: expected ${settings.expectedRows} rows for ` +
      `${expectedProviders.join(",")}, got ${rows.length} rows for ${providers.join(",") || "none"}`,
    );
    return false;
  }
  const metricsOutput = `${output}.metrics.json`;
  const summary = spawnSync(
    process.env.SPIKE_PYTHON ?? "python3",
    process.env.SPIKE_PYTHON
      ? [spikeScoring, "--input", output, "--output", metricsOutput]
      : [spikeScoring, "--input", output, "--output", metricsOutput],
    { stdio: "inherit", env: process.env },
  );
  if (summary.error || summary.status !== 0) {
    if (summary.error) console.error(summary.error.message);
    return false;
  }
  console.log(`${stageName}: verified ${rows.length} rows; normalized metrics at ${metricsOutput}`);
  return true;
}

const isSpike = mode === "spike-validate" || mode in spikeStages;
let finalistLabels;
if (isSpike) {
  if (extraArgs.length > 0) {
    console.error("spike modes reject extra Promptfoo arguments so the pinned grader and row contract cannot be overridden");
    process.exit(2);
  }
  const preflightArgs = ["validate"];
  if (mode === "spike-heldout") {
    if (process.env.SPIKE_HELDOUT_READY !== "1") {
      console.error("held-out scoring is locked; set SPIKE_HELDOUT_READY=1 only after human gold and finalist identity are frozen");
      process.exit(2);
    }
    const label = process.env.SPIKE_HELDOUT_FINALIST ?? "";
    const hash = process.env.SPIKE_HELDOUT_FINALIST_SHA256 ?? "";
    if (!label || !hash) {
      console.error("held-out scoring requires SPIKE_HELDOUT_FINALIST and SPIKE_HELDOUT_FINALIST_SHA256");
      process.exit(2);
    }
    preflightArgs.splice(0, 1, "validate-heldout", "--finalist-label", label, "--finalist-sha256", hash);
    finalistLabels = [label];
    commands[mode].push("--filter-providers", `^(?:${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})$`);
  } else if (mode === "spike-validation") {
    try {
      const parsed = parseFinalists(process.env.SPIKE_FINALISTS);
      finalistLabels = parsed.labels;
      commands[mode].push("--filter-providers", parsed.filter);
      if (selectProviderLabels(SPIKE_PROVIDER_LABELS, parsed.filter).length !== 2) {
        throw new Error("finalist filter did not select exactly two configured providers");
      }
    } catch (error) {
      console.error(error.message);
      process.exit(2);
    }
  }
  if (!runSpikePreflight(preflightArgs)) process.exit(1);
}

const executable = process.platform === "win32" ? "promptfoo.cmd" : "promptfoo";
const result = spawnSync(executable, [...commands[mode], ...extraArgs], {
  stdio: "inherit",
  env: {
    ...process.env,
    PROMPTFOO_DISABLE_TELEMETRY: "1",
    PROMPTFOO_DISABLE_UPDATE: "1",
    PROMPTFOO_DISABLE_SHARING: "1",
    PROMPTFOO_CONFIG_DIR: resolve(".promptfoo"),
  },
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
if ((result.status ?? 1) !== 0) process.exit(result.status ?? 1);
if (mode in spikeStages && !verifySpikeOutput(mode, finalistLabels)) process.exit(1);
process.exit(0);
