import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const mode = process.argv[2] ?? "smoke";
const extraArgs = process.argv.slice(3);
const config = "evals/behavioral/promptfooconfig.yaml";
const output = "evals/behavioral/results/latest.json";

const commands = {
  validate: ["validate", "config", "-c", config],
  smoke: ["eval", "-c", config, "--no-cache", "--repeat", "1", "-o", output],
  baseline: ["eval", "-c", config, "--no-cache", "--repeat", "3", "-o", output],
};

if (!(mode in commands)) {
  console.error(`unknown mode ${mode}; expected validate, smoke, or baseline`);
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
