import postgres from "postgres";
import { createDeliveryWorker } from "./delivery-v2.mjs";

const token = process.env.TELEGRAM_BOT_TOKEN;
const chatId = process.env.TELEGRAM_CHAT_ID;
const database = process.env.DATABASE_URL;
const apiBaseUrl = process.env.TELEGRAM_API_BASE_URL || "https://api.telegram.org";

if (!token || !chatId || !database) {
  console.error(JSON.stringify({ event: "alert_run_failed", ok: false, reason: "missing_configuration" }));
  process.exit(1);
}

if (process.env.TELEGRAM_API_BASE_URL) {
  let host;
  try { host = new URL(apiBaseUrl).hostname; } catch {
    console.error(JSON.stringify({ event: "alert_run_failed", ok: false, reason: "invalid_test_endpoint" }));
    process.exit(1);
  }
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) {
    console.error(JSON.stringify({ event: "alert_run_failed", ok: false, reason: "test_endpoint_must_be_loopback" }));
    process.exit(1);
  }
}

const sql = postgres(database, { max: 1, connect_timeout: 10, onnotice: () => {} });
const started = Date.now();
try {
  const worker = createDeliveryWorker({ sql, token, chatId, apiBaseUrl });
  const result = await worker.run();
  if (result.rejected || result.uncertain) process.exitCode = 1;
} catch (error) {
  console.error(JSON.stringify({
    event: "alert_run_failed",
    ok: false,
    error_class: String(error?.message || "delivery_worker_failed").replace(/[^a-zA-Z0-9_-]/g, "_"),
    duration_ms: Date.now() - started,
  }));
  process.exitCode = 1;
} finally {
  await sql.end();
}
