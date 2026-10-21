import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const source = readFileSync(new URL("./index.mjs", import.meta.url), "utf8");

test("missing configuration exits nonzero", () => {
  assert.match(source, /reason:"missing_configuration"\}\)\);process\.exit\(1\)/);
});

test("delivery failures mark cron run failed", () => {
  assert.match(source, /if\(failed>0\)process\.exitCode=1/);
});

test("top-level execution errors mark cron run failed", () => {
  assert.match(source, /event:"alert_run_failed",ok:false,error:String\(error\)/);
  assert.match(source, /process\.exitCode=1/);
});
