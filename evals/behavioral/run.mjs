import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import {
  hasCompleteAccounting,
  hasCompleteNormalizedRows,
  hasExactRowCardinality,
  isCompletedPromptfooExit,
  parseFinalists,
  selectProviderLabels,
  SPIKE_PROVIDER_LABELS,
} from "./selection.mjs";

const ACCOUNTING_ID = "muse-attempt-accounting-v1";

const mode = process.argv[2] ?? "smoke";
const extraArgs = process.argv.slice(3);
const behavioralConfig = "evals/behavioral/promptfooconfig.yaml";
const behavioralOutput = "evals/behavioral/results/latest.json";
const crossModelConfig = "evals/behavioral/cross-model-promptfooconfig.yaml";
const crossModelOutput = "evals/behavioral/results/cross-model-latest.json";
const spikeConfig = "evals/behavioral/spike-promptfooconfig.yaml";
const spikeScoring = "evals/behavioral/scoring.py";
const developmentConfig = "evals/behavioral/development-v2-promptfooconfig.yaml";
const developmentV3Config = "evals/behavioral/development-v3-promptfooconfig.yaml";
const screenV3Config = "evals/behavioral/evidence-claims-v3-screen-promptfooconfig.yaml";

const spikeStages = {
  "spike-probe": {
    split: "train",
    cases: ["pr1-broken-promptfoo"],
    repeat: 1,
    providers: ["spike-current"],
    expectedRows: 1,
    output: "evals/behavioral/results/spike-probe.json",
    caseId: "pr1-broken-promptfoo",
    providerFilter: "^(?:spike-current)$",
  },
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

const developmentStages = {
  "development-v2": {
    split: "development",
    cases: [
      "genv-pr87-first-repair-type-boundary",
      "genv-pr90-evidence-claim",
      "genv-pr90-synchronized-clean",
    ],
    repeat: 1,
    providers: ["development-v2-current", "development-v2-evidence-claims"],
    expectedRows: 6,
    output: "evals/behavioral/results/development-v2.json",
    config: developmentConfig,
    preflight: "validate-development",
  },
  "development-v3": {
    split: "development",
    cases: [
      "genv-pr87-first-repair-type-boundary",
      "genv-pr90-evidence-claim",
      "genv-pr90-synchronized-clean",
    ],
    repeat: 1,
    providers: ["development-v3-current", "development-v3-evidence-claims"],
    expectedRows: 6,
    output: "evals/behavioral/results/development-v3.json",
    config: developmentV3Config,
    preflight: "validate-development-v3",
  },
  "evidence-claims-v3-screen": {
    split: "development",
    cases: [
      "genv-pr87-first-repair-type-boundary",
      "genv-pr90-evidence-claim",
      "genv-pr90-synchronized-clean",
    ],
    repeat: 1,
    providers: ["screen-evidence-claims-v2", "screen-evidence-claims-v3"],
    expectedRows: 6,
    output: "evals/behavioral/results/evidence-claims-v3-screen.json",
    config: screenV3Config,
    preflight: "validate-evidence-claims-v3-screen",
    // Disposable checkouts go outside this repository, away from answer keys.
    outsideWorkspaces: true,
    grading: "deterministic",
  },
};

const developmentValidateModes = {
  "development-v2-validate": "validate-development",
  "development-v3-validate": "validate-development-v3",
  "evidence-claims-v3-screen-validate": "validate-evidence-claims-v3-screen",
};

function developmentOutputPaths(settings) {
  const output = resolve(settings.output);
  return [
    output,
    `${output}.metrics-v2.json`,
    `${output}.reservation.json`,
    `${output}.promptfoo`,
    `${output}.traces`,
    `${output}.schedule.json`,
    `${output}.accounting-v1.json`,
  ];
}

function existingDevelopmentArtifacts(settings) {
  return developmentOutputPaths(settings).filter((path) => existsSync(path));
}

function reserveDevelopmentOutput(stageName, settings) {
  const [output, metrics, reservation, cache, traces, schedule, accounting] = developmentOutputPaths(settings);
  const existing = [output, metrics, reservation, cache, traces, schedule, accounting].filter((path) => existsSync(path));
  if (existing.length > 0) {
    return { ok: false, reason: `existing development result artifacts: ${existing.join(", ")}` };
  }
  mkdirSync(dirname(output), { recursive: true });
  try {
    writeFileSync(
      reservation,
      `${JSON.stringify({ stage: stageName, output, metrics, cache })}\n`,
      { encoding: "utf8", flag: "wx", mode: 0o600 },
    );
  } catch (error) {
    return { ok: false, reason: `unable to reserve development result identity: ${error.message}` };
  }
  try {
    writeFileSync(
      schedule,
      `${JSON.stringify({
        stage: stageName,
        providers: settings.providers,
        cases: settings.cases,
        repeat: settings.repeat,
        expectedRows: settings.expectedRows,
        accounting: ACCOUNTING_ID,
      })}\n`,
      { encoding: "utf8", flag: "wx", mode: 0o600 },
    );
  } catch (error) {
    return { ok: false, reason: `unable to reserve development attempt schedule: ${error.message}` };
  }
  try {
    mkdirSync(cache, { mode: 0o700 });
    mkdirSync(traces, { mode: 0o700 });
  } catch (error) {
    return { ok: false, reason: `unable to create private development cache: ${error.message}` };
  }
  let workspaces;
  if (settings.outsideWorkspaces) {
    const base = process.env.MUSE_EVAL_WORKSPACE_BASE ?? join(homedir(), ".cache", "muse-skill-eval");
    // Neutral name: the stage name is itself an answer-key marker in the trace scan.
    workspaces = join(resolve(base), `run-${Date.now()}-${process.pid}`);
    if (workspaces.startsWith(`${resolve(".")}/`)) {
      return { ok: false, reason: `workspace parent must be outside the repository: ${workspaces}` };
    }
    try {
      mkdirSync(workspaces, { recursive: true, mode: 0o700 });
    } catch (error) {
      return { ok: false, reason: `unable to create outside workspace parent: ${error.message}` };
    }
  }
  return { ok: true, reservation, cache, traces, workspaces };
}

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
  "development-v2-validate": ["validate", "config", "-c", developmentConfig],
  "development-v3-validate": ["validate", "config", "-c", developmentV3Config],
  "evidence-claims-v3-screen-validate": ["validate", "config", "-c", screenV3Config],
};

for (const [stage, settings] of Object.entries(spikeStages)) {
  commands[stage] = [
    "eval", "-c", spikeConfig, "--no-cache", "--repeat", String(settings.repeat),
    "--filter-metadata", `split=${settings.split}`, "-o", settings.output,
  ];
  if (settings.caseId) {
    commands[stage].push("--filter-metadata", `case_id=${settings.caseId}`);
  }
  if (settings.providerFilter) {
    commands[stage].push("--filter-providers", settings.providerFilter);
  }
}

for (const [stage, settings] of Object.entries(developmentStages)) {
  commands[stage] = [
    "eval", "-c", settings.config, "--no-cache", "--repeat", String(settings.repeat),
    "--filter-metadata", `split=${settings.split}`, "-o", settings.output,
  ];
}

if (!(mode in commands)) {
  console.error(`unknown mode ${mode}; expected ${Object.keys(commands).join(", ")}`);
  process.exit(2);
}

function runExperimentPreflight(args) {
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

function verifyExperimentOutput(stageName, settings, finalistLabels) {
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
  const metricsOutput = `${output}.metrics-v2.json`;
  const gradingArgs = settings.grading ? ["--grading", settings.grading] : [];
  const summary = spawnSync(
    process.env.SPIKE_PYTHON ?? "python3",
    process.env.SPIKE_PYTHON
      ? [spikeScoring, "--input", output, "--output", metricsOutput, ...gradingArgs]
      : [spikeScoring, "--input", output, "--output", metricsOutput, ...gradingArgs],
    { stdio: "inherit", env: process.env },
  );
  if (summary.error || summary.status !== 0) {
    if (summary.error) console.error(summary.error.message);
    return false;
  }
  let metrics;
  try {
    metrics = JSON.parse(readFileSync(metricsOutput, "utf8"));
  } catch (error) {
    console.error(`unable to read normalized metrics: ${error.message}`);
    return false;
  }
  if (!hasCompleteNormalizedRows(metrics, settings.expectedRows)) {
    console.error(
      `${stageName} completion contract failed: expected ${settings.expectedRows} ` +
      `fully graded rows, got ${metrics.aggregate?.completed ?? "unknown"} complete and ` +
      `${metrics.aggregate?.errors ?? "unknown"} errors`,
    );
    return false;
  }
  if (metrics.aggregate?.quarantined) {
    console.warn(
      `${stageName}: ${metrics.aggregate.quarantined} row(s) QUARANTINED; inspect traces before using them`,
    );
  }
  console.log(`${stageName}: verified ${rows.length} rows; normalized metrics at ${metricsOutput}`);
  return true;
}

const isSpike = mode === "spike-validate" || mode in spikeStages;
const isDevelopment = mode in developmentValidateModes || mode in developmentStages;
const isManagedExperiment = isSpike || isDevelopment;
let finalistLabels;
if (mode in developmentStages) {
  process.umask(0o077);
  const existing = existingDevelopmentArtifacts(developmentStages[mode]);
  if (existing.length > 0) {
    console.error(
      `${mode} refuses to overwrite prior or partial attempt artifacts: ${existing.join(", ")}`,
    );
    process.exit(2);
  }
}
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
  if (!runExperimentPreflight(preflightArgs)) process.exit(1);
}
if (isDevelopment) {
  if (extraArgs.length > 0) {
    console.error("development modes reject extra Promptfoo arguments so the pinned profile and row contract cannot be overridden");
    process.exit(2);
  }
  const preflight = mode in developmentStages
    ? developmentStages[mode].preflight
    : developmentValidateModes[mode];
  if (!runExperimentPreflight([preflight])) process.exit(1);
  if (mode in developmentStages) {
    const reservation = reserveDevelopmentOutput(mode, developmentStages[mode]);
    if (!reservation.ok) {
      console.error(`${mode} did not start: ${reservation.reason}`);
      process.exit(2);
    }
    console.log(`${mode} reserved result custody at ${reservation.reservation}`);
    process.env.MUSE_EVAL_TRACE_DIR = reservation.traces;
    if (reservation.workspaces) process.env.MUSE_EVAL_WORKSPACE_PARENT = reservation.workspaces;
  }
}

const executable = process.platform === "win32" ? "promptfoo.cmd" : "promptfoo";
const result = spawnSync(executable, [...commands[mode], ...extraArgs], {
  stdio: "inherit",
  env: {
    ...process.env,
    PROMPTFOO_DISABLE_TELEMETRY: "1",
    PROMPTFOO_DISABLE_UPDATE: "1",
    PROMPTFOO_DISABLE_SHARING: "1",
    PROMPTFOO_CONFIG_DIR: mode in developmentStages
      ? developmentOutputPaths(developmentStages[mode])[3]
      : resolve(".promptfoo"),
    ...(isManagedExperiment ? { PROMPTFOO_FAILED_TEST_EXIT_CODE: "100" } : {}),
  },
});

function writeStageAccounting(stageName, settings) {
  // Account for every scheduled slot after Promptfoo exits (completed or
  // not). When the Promptfoo output is missing, the accounting step lists
  // every scheduled slot from the attempt ledger alone.
  const [output, , , , traces, schedule, accounting] = developmentOutputPaths(settings);
  const ledger = join(traces, "attempts.jsonl");
  const scoringArgs = [spikeScoring];
  if (existsSync(output)) scoringArgs.push("--input", output);
  scoringArgs.push("--schedule", schedule, "--accounting-output", accounting);
  if (existsSync(ledger)) scoringArgs.push("--ledger", ledger);
  const accountingRun = spawnSync(
    process.env.SPIKE_PYTHON ?? "python3",
    scoringArgs,
    { stdio: "inherit", env: process.env },
  );
  if (accountingRun.error) console.error(accountingRun.error.message);
  if (accountingRun.error || accountingRun.status !== 0 || !existsSync(accounting)) {
    console.error(`${stageName} failed to write attempt accounting at ${accounting}`);
    return false;
  }
  console.log(`${stageName}: wrote attempt accounting at ${accounting}`);
  return true;
}

function hasCompleteStageAccounting(settings) {
  const [, , , , , , accounting] = developmentOutputPaths(settings);
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(accounting, "utf8"));
  } catch (error) {
    console.error(`unable to read attempt accounting: ${error.message}`);
    return false;
  }
  return hasCompleteAccounting(parsed, settings.expectedRows);
}

if (result.error) {
  console.error(result.error.message);
  if (mode in developmentStages) writeStageAccounting(mode, developmentStages[mode]);
  process.exit(1);
}
const promptfooStatus = result.status ?? 1;
if (mode in developmentStages) {
  const settings = developmentStages[mode];
  const accountingWritten = writeStageAccounting(mode, settings);
  const promptfooDone = isCompletedPromptfooExit(promptfooStatus);
  const verified = promptfooDone ? verifyExperimentOutput(mode, settings) : false;
  const accountingComplete = hasCompleteStageAccounting(settings);
  if (!accountingWritten || !accountingComplete) {
    if (accountingWritten) {
      console.error(
        `${mode} attempt accounting incomplete: expected ${settings.expectedRows} ` +
        `completed/quarantined slots`,
      );
    }
    process.exit(promptfooDone ? 1 : promptfooStatus);
  }
  if (!promptfooDone) process.exit(promptfooStatus);
  if (!verified) process.exit(1);
  process.exit(0);
}
if (mode in spikeStages
  ? !isCompletedPromptfooExit(promptfooStatus)
  : promptfooStatus !== 0) {
  process.exit(promptfooStatus);
}
if (mode in spikeStages && !verifyExperimentOutput(mode, spikeStages[mode], finalistLabels)) {
  process.exit(1);
}
process.exit(0);
