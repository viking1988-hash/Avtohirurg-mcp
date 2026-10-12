import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyDeliveryOutcome as classify } from './outbox_transport_policy.mjs';
test('confirmed accepted response records one receipt', () => {
  assert.deepEqual(classify({kind:'accepted',messageId:123}), {state:'sent',messageId:123});
});
test('429 uses bounded retry-after', () => {
  assert.deepEqual(classify({kind:'http',status:429,retryAfterSeconds:99999}),
    {state:'retryable_error',error:'TELEGRAM_RATE_LIMIT',retryAfterSeconds:3600});
  assert.equal(classify({kind:'http',status:429,retryAfterSeconds:0}).retryAfterSeconds,1);
});
for (const input of [
  {kind:'http',status:500},{kind:'http',status:503},
  {kind:'timeout'},{kind:'connection_reset'},{kind:'crash_after_ack'},
  {kind:'accepted',messageId:null},{kind:'unexpected'}
]) {
  test('uncertain response is quarantined: '+JSON.stringify(input), () => {
    assert.equal(classify(input).state,'review');
  });
}
test('explicit 400 error requires review instead of blind retry', () => {
  assert.equal(classify({kind:'http',status:400}).state,'review');
});
