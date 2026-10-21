import postgres from "postgres";

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const args = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i];
    if (!key?.startsWith("--") || rest[i + 1] === undefined) throw new Error("invalid_cli_arguments");
    args[key.slice(2)] = rest[i + 1];
  }
  return { command, args };
}

export async function runOperatorAction(sql, command, args) {
  if (command === "list-uncertain" || command === "list-rejected") {
    const state = command === "list-uncertain" ? "uncertain" : "rejected";
    return sql`SELECT lead_id, alert_type, attempt_count, last_attempt_at, next_attempt_at,
        last_error_class, updated_at
      FROM crm_telegram_delivery_v2 WHERE state = ${state}
      ORDER BY updated_at ASC`;
  }

  const leadId = Number(args["lead-id"]);
  const alertType = args["alert-type"];
  const operatorId = args.operator;
  const reason = args.reason;
  if (!Number.isSafeInteger(leadId) || leadId < 1 ||
      !["new", "overdue"].includes(alertType) ||
      typeof operatorId !== "string" || !operatorId.trim() ||
      typeof reason !== "string" || !reason.trim()) {
    throw new Error("operator_action_requires_lead_type_operator_and_reason");
  }

  if (command === "confirm-delivered") {
    return sql.begin(async tx => {
      const rows = await tx`UPDATE crm_telegram_delivery_v2
        SET state = 'sent', sent_at = clock_timestamp(), claimed_at = NULL,
            last_error_class = NULL, updated_at = clock_timestamp()
        WHERE lead_id = ${leadId} AND alert_type = ${alertType} AND state = 'uncertain'
        RETURNING lead_id`;
      if (!rows.length) throw new Error("delivery_not_uncertain_or_not_found");
      await tx`INSERT INTO crm_telegram_alert_log (lead_id, alert_type, sent_at)
        VALUES (${leadId}, ${alertType}, clock_timestamp())
        ON CONFLICT (lead_id, alert_type) DO NOTHING`;
      await tx`INSERT INTO crm_telegram_delivery_operator_audit
        (lead_id, alert_type, action, operator_id, reason, duplicate_risk_accepted)
        VALUES (${leadId}, ${alertType}, 'confirmed_delivered', ${operatorId.trim()}, ${reason.trim()}, false)`;
      return [{ lead_id: leadId, alert_type: alertType, state: "sent" }];
    });
  }

  if (command === "authorize-resend") {
    if (args["accept-duplicate-risk"] !== "YES") {
      throw new Error("explicit_duplicate_risk_acceptance_required");
    }
    return sql.begin(async tx => {
      const rows = await tx`UPDATE crm_telegram_delivery_v2
        SET state = 'pending', next_attempt_at = NULL, claimed_at = NULL,
            manual_resend_authorized = true, last_error_class = NULL,
            updated_at = clock_timestamp()
        WHERE lead_id = ${leadId} AND alert_type = ${alertType} AND state = 'uncertain'
        RETURNING lead_id`;
      if (!rows.length) throw new Error("delivery_not_uncertain_or_not_found");
      await tx`INSERT INTO crm_telegram_delivery_operator_audit
        (lead_id, alert_type, action, operator_id, reason, duplicate_risk_accepted)
        VALUES (${leadId}, ${alertType}, 'authorized_resend', ${operatorId.trim()}, ${reason.trim()}, true)`;
      return [{ lead_id: leadId, alert_type: alertType, state: "pending", duplicate_risk_accepted: true }];
    });
  }

  throw new Error("unsupported_operator_command");
}

if (import.meta.url === new URL(process.argv[1], "file:").href) {
  const { command, args } = parseArgs(process.argv.slice(2));
  const database = process.env.DATABASE_URL;
  if (!database) {
    console.error(JSON.stringify({ event: "delivery_operator_failed", error_class: "missing_database_configuration" }));
    process.exit(1);
  }
  const sql = postgres(database, { max: 1, connect_timeout: 10, onnotice: () => {} });
  try {
    const result = await runOperatorAction(sql, command, args);
    console.log(JSON.stringify({ event: "delivery_operator_completed", command, result }));
  } catch (error) {
    console.error(JSON.stringify({
      event: "delivery_operator_failed",
      command,
      error_class: String(error?.message || "operator_action_failed").replace(/[^a-zA-Z0-9_-]/g, "_"),
    }));
    process.exitCode = 1;
  } finally {
    await sql.end();
  }
}
