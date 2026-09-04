import assert from 'node:assert/strict';

import { detailValue, groupRupees, onlyDigits, plainReason } from './src/lib/api.js';

assert.equal(
  plainReason({ rule: 'R5', reason: 'amount 600000 is over the per-call cap 500000' }, 600000),
  '₹6,000.00 is above your ₹5,000.00 single-purchase limit.',
);
assert.equal(
  plainReason({ rule: 'R0', reason: 'amount -50000 is outside 100..1000000000' }),
  '₹-500.00 is outside the allowed range of ₹1.00 to ₹1,00,00,000.00.',
);
assert.equal(detailValue('available_after', 950000), '₹9,500.00');
assert.equal(detailValue('count', 3), '3');
assert.equal(plainReason({ rule: 'R5', reason: 'unrecognised refusal' }), 'unrecognised refusal');

// Indian grouping in the limit boxes: last three digits, then pairs. The second half of
// the contract matters more than the first - what is stored is bare digits, so a typed
// separator can never reach the API.
assert.equal(groupRupees('10000'), '10,000');
assert.equal(groupRupees('100000'), '1,00,000');
assert.equal(groupRupees('10000000'), '1,00,00,000');
assert.equal(groupRupees('500'), '500');
assert.equal(groupRupees(''), '');
assert.equal(onlyDigits('1,00,000'), '100000');
assert.equal(onlyDigits('12a,3 4'), '1234');
assert.equal(onlyDigits(''), '');
// Round trip: whatever is displayed parses back to exactly what was stored.
for (const d of ['1', '999', '10000', '123456789']) {
  assert.equal(onlyDigits(groupRupees(d)), d);
}

console.log('money display self-check passed');
