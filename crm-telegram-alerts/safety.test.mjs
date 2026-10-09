import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const source = readFileSync(new URL("./index.mjs", import.meta.url), "utf8");
test("missing configuration fails the scheduled job", () => {
  assert.match(source, /reason:"missing_configuration"/);
  assert.match(source, /process\.exit\(1\)/);
});
test("Telegram requests have a bounded timeout", () => {
  assert.match(source, /signal:AbortSignal\.timeout\(10000\)/);
});
test("message payload contains no customer phone", () => {
  assert.match(source, /chat_id:chat,text/);
  assert.doesNotMatch(source, /lead\.phone|lead\.telephone/);
});
