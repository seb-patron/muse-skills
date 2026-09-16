import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const developmentV4Config = "evals/behavioral/development-v4-promptfooconfig.yaml";
const preflightScript = "evals/behavioral/development_v4.py";

if (process.argv.slice(2).length > 0) {
  console.error(
    "development v4 validation rejects extra Promptfoo arguments so the pinned " +
    "profile, snapshot identities, and leakage preflight cannot be overridden",
  );
  process.exit(2);
}

const preflight = spawnSync(
  process.env.SPIKE_PYTHON ?? "uv",
  process.env.SPIKE_PYTHON
    ? [preflightScript, "validate"]
    : ["run", "--with", "PyYAML==6.0.3", "python", preflightScript, "validate"],
  { stdio: "inherit", env: process.env },
);
if (preflight.error) {
  console.error(preflight.error.message);
  process.exit(1);
}
if (preflight.status !== 0) process.exit(1);

const executable = process.platform === "win32" ? "promptfoo.cmd" : "promptfoo";
const result = spawnSync(executable, ["validate", "config", "-c", developmentV4Config], {
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
