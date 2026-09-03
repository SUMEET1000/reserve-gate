import assert from 'node:assert/strict';

import { detailValue, plainReason } from './src/lib/api.js';

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

console.log('money display self-check passed');
