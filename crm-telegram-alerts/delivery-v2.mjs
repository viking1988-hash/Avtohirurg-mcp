import { randomUUID } from "node:crypto";

const MAX_ATTEMPTS = 5;
const LEASE_TIMEOUT_MINUTES = 15;

function safeLog(write, event, fields = {}) {
  write(JSON.stringify({ event, ...fields }));
}

function telegramUrl(baseUrl, token) {
  return `${baseUrl.replace(/\/$/, "")}/bot${token}/sendMessage`;
}

function retrySeconds(body) {
  const value = Number(body?.parameters?.retry_after);
  return Number.isInteger(value) && value >= 1 && value <= 86400 ? value : null;
}

export function createDeliveryWorker({
  sql,
  token,
  chatId,
  fetchImpl = fetch,
  apiBaseUrl = "https://api.telegram.org",
  now = () => new Date(),
  log = console.log,
  errorLog = console.error,
  maxAttempts = MAX_ATTEMPTS,
}) {
  if (!sql || !token || !chatId) throw new Error("delivery_worker_configuration_missing");

  async function enqueue() {
    const leads = await sql`SELECT id, created_at
      FROM inbound_leads
      WHERE status = 'new' AND created_at > now() - interval '30 days'
      ORDER BY created_at ASC LIMIT 200`;
    let queued = 0;
    for (const lead of leads) {
      const types = new Date(lead.created_at).getTime() < now().getTime() - 30 * 60_000
        ? ["new", "overdue"] : ["new"];
      for (const type of types) {
        const rows = await sql`INSERT INTO crm_telegram_delivery_v2
          (lead_id, alert_type, state)
          SELECT ${lead.id}, ${type}, 'pending'
          WHERE EXISTS (SELECT 1 FROM inbound_leads WHERE id = ${lead.id} AND status = 'new')
            AND NOT EXISTS (
              SELECT 1 FROM crm_telegram_alert_log
              WHERE lead_id = ${lead.id} AND alert_type = ${type}
            )
          ON CONFLICT (lead_id, alert_type) DO NOTHING
          RETURNING lead_id`;
        queued += rows.length;
      }
    }
    return { checked: leads.length, queued };
  }

  async function claimOne() {
    return sql.begin(async tx => {
      const rows = await tx`
        WITH candidate AS (
          SELECT lead_id, alert_type
          FROM crm_telegram_delivery_v2
          WHERE attempt_count < ${maxAttempts}
            AND (
              state = 'pending'
              OR (state = 'rejected' AND next_attempt_at IS NOT NULL AND next_attempt_at <= clock_timestamp())
            )
          ORDER BY COALESCE(next_attempt_at, updated_at), lead_id
          FOR UPDATE SKIP LOCKED
          LIMIT 1
        )
        UPDATE crm_telegram_delivery_v2 d
        SET state = 'sending',
            attempt_count = d.attempt_count + 1,
            claimed_at = clock_timestamp(),
            last_attempt_at = clock_timestamp(),
            next_attempt_at = NULL,
            last_error_class = NULL,
            updated_at = clock_timestamp()
        FROM candidate c
        WHERE d.lead_id = c.lead_id AND d.alert_type = c.alert_type
        RETURNING d.lead_id, d.alert_type, d.attempt_count
      `;
      return rows[0] ?? null;
    });
  }

  async function markUncertainStale() {
    return sql`
      UPDATE crm_telegram_delivery_v2
      SET state = 'uncertain',
          last_error_class = 'stale_sending_requires_review',
          updated_at = clock_timestamp()
      WHERE state = 'sending'
        AND claimed_at < clock_timestamp() - (${LEASE_TIMEOUT_MINUTES} * interval '1 minute')
      RETURNING lead_id, alert_type, attempt_count
    `;
  }

  async function persistSent(delivery, messageId) {
    await sql.begin(async tx => {
      await tx`UPDATE crm_telegram_delivery_v2
        SET state = 'sent', sent_at = clock_timestamp(),
            telegram_message_id = ${messageId}, claimed_at = NULL,
            last_error_class = NULL, updated_at = clock_timestamp()
        WHERE lead_id = ${delivery.lead_id} AND alert_type = ${delivery.alert_type}
          AND state = 'sending'`;
      await tx`INSERT INTO crm_telegram_alert_log (lead_id, alert_type, sent_at)
        VALUES (${delivery.lead_id}, ${delivery.alert_type}, clock_timestamp())
        ON CONFLICT (lead_id, alert_type) DO NOTHING`;
    });
  }

  async function persistRejected(delivery, errorClass, delaySeconds = null) {
    const retryAt = delaySeconds === null ? null : new Date(now().getTime() + delaySeconds * 1000);
    await sql`UPDATE crm_telegram_delivery_v2
      SET state = 'rejected',
          next_attempt_at = ${retryAt},
          claimed_at = NULL,
          last_error_class = ${errorClass},
          updated_at = clock_timestamp()
      WHERE lead_id = ${delivery.lead_id} AND alert_type = ${delivery.alert_type}
        AND state = 'sending'`;
  }

  async function persistUncertain(delivery, errorClass) {
    await sql`UPDATE crm_telegram_delivery_v2
      SET state = 'uncertain', claimed_at = NULL,
          next_attempt_at = NULL, last_error_class = ${errorClass},
          updated_at = clock_timestamp()
      WHERE lead_id = ${delivery.lead_id} AND alert_type = ${delivery.alert_type}
        AND state = 'sending'`;
  }

  async function send(delivery) {
    const text = "Автохирург CRM: " +
      (delivery.alert_type === "new" ? "🔔 Новая заявка" : "⏰ Заявка без обработки более 30 минут") +
      " №" + delivery.lead_id;
    let response;
    try {
      response = await fetchImpl(telegramUrl(apiBaseUrl, token), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text }),
      });
    } catch {
      await persistUncertain(delivery, "transport_outcome_unknown");
      safeLog(errorLog, "telegram_delivery_uncertain", {
        lead_id: delivery.lead_id, alert_type: delivery.alert_type,
        attempt: delivery.attempt_count, error_class: "transport_outcome_unknown",
      });
      return "uncertain";
    }

    let body;
    try { body = await response.json(); } catch {
      await persistUncertain(delivery, "malformed_telegram_response");
      safeLog(errorLog, "telegram_delivery_uncertain", {
        lead_id: delivery.lead_id, alert_type: delivery.alert_type,
        attempt: delivery.attempt_count, error_class: "malformed_telegram_response",
      });
      return "uncertain";
    }

    const code = Number(body?.error_code ?? response.status);
    if (response.ok && body?.ok === true) {
      const messageId = Number.isSafeInteger(Number(body?.result?.message_id))
        ? Number(body.result.message_id) : null;
      await persistSent(delivery, messageId);
      safeLog(log, "telegram_delivery_sent", {
        lead_id: delivery.lead_id, alert_type: delivery.alert_type,
        attempt: delivery.attempt_count, telegram_message_id: messageId,
      });
      return "sent";
    }

    if (code === 429) {
      const delay = retrySeconds(body);
      if (delay !== null && delivery.attempt_count < maxAttempts) {
        await persistRejected(delivery, "telegram_http_429", delay);
        safeLog(errorLog, "telegram_delivery_retry_scheduled", {
          lead_id: delivery.lead_id, alert_type: delivery.alert_type,
          attempt: delivery.attempt_count, retry_after_seconds: delay,
          error_class: "telegram_http_429",
        });
        return "rejected";
      }
      await persistRejected(delivery, "telegram_http_429_retry_unavailable");
      safeLog(errorLog, "telegram_delivery_rejected", {
        lead_id: delivery.lead_id, alert_type: delivery.alert_type,
        attempt: delivery.attempt_count, error_class: "telegram_http_429_retry_unavailable",
      });
      return "rejected";
    }

    if (code >= 400 && code < 500) {
      await persistRejected(delivery, code === 403 ? "telegram_http_403_permanent" : "telegram_http_4xx_permanent");
      safeLog(errorLog, "telegram_delivery_rejected", {
        lead_id: delivery.lead_id, alert_type: delivery.alert_type,
        attempt: delivery.attempt_count, error_class: code === 403 ? "telegram_http_403_permanent" : "telegram_http_4xx_permanent",
      });
      return "rejected";
    }

    await persistUncertain(delivery, code >= 500 ? "telegram_http_5xx_outcome_unknown" : "telegram_api_outcome_unknown");
    safeLog(errorLog, "telegram_delivery_uncertain", {
      lead_id: delivery.lead_id, alert_type: delivery.alert_type,
      attempt: delivery.attempt_count,
      error_class: code >= 500 ? "telegram_http_5xx_outcome_unknown" : "telegram_api_outcome_unknown",
    });
    return "uncertain";
  }

  async function run() {
    const started = now().getTime();
    const stale = await markUncertainStale();
    for (const row of stale) safeLog(errorLog, "telegram_stale_claim_uncertain", row);
    const queued = await enqueue();
    const totals = { ...queued, claimed: 0, sent: 0, rejected: 0, uncertain: stale.length };
    while (true) {
      const delivery = await claimOne();
      if (!delivery) break;
      totals.claimed++;
      const result = await send(delivery);
      totals[result]++;
    }
    safeLog(log, "telegram_delivery_run_completed", {
      ...totals, duration_ms: now().getTime() - started,
    });
    return totals;
  }

  return { run, enqueue, claimOne, markUncertainStale };
}
