// Pure, staging-testable classification. No network calls or Telegram credentials.
export function classifyDeliveryOutcome(result) {
  if (!result || typeof result !== 'object') throw new TypeError('Delivery result required');
  if (result.kind === 'accepted' && Number.isSafeInteger(result.messageId) && result.messageId > 0)
    return { state: 'sent', messageId: result.messageId };
  if (result.kind === 'http' && result.status === 429)
    return { state: 'retryable_error', error: 'TELEGRAM_RATE_LIMIT', retryAfterSeconds: boundedDelay(result.retryAfterSeconds) };
  if (result.kind === 'http' && result.status >= 500 && result.status <= 599)
    return { state: 'review', error: 'TELEGRAM_SERVER_RESPONSE_UNCERTAIN' };
  if (result.kind === 'http' && result.status >= 400 && result.status <= 499)
    return { state: 'review', error: 'TELEGRAM_REJECTED_REQUIRES_REVIEW' };
  if (result.kind === 'timeout' || result.kind === 'connection_reset' || result.kind === 'crash_after_ack')
    return { state: 'review', error: 'TELEGRAM_DELIVERY_UNCERTAIN' };
  return { state: 'review', error: 'TELEGRAM_UNKNOWN_OUTCOME' };
}
function boundedDelay(seconds) {
  if (!Number.isFinite(seconds)) return 60;
  return Math.min(3600, Math.max(1, Math.ceil(seconds)));
}
