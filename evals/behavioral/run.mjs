import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const mode = process.argv[2] ?? "smoke";
const extraArgs = process.argv.slice(3);
const behavioralConfig = "evals/behavioral/promptfooconfig.yaml";
const behavioralOutput = "evals/behavioral/results/latest.json";
const crossModelConfig = "evals/behavioral/cross-model-promptfooconfig.yaml";
const crossModelOutput = "evals/behavioral/results/cross-model-latest.json";

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
};

if (!(mode in commands)) {
  console.error(`unknown mode ${mode}; expected ${Object.keys(commands).join(", ")}`);
  process.exit(2);
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
process.exit(result.status ?? 1);
