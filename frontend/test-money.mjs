import assert from 'node:assert/strict';

import { askRows, detailValue, groupRupees, money, onlyDigits, plainReason }
  from './src/lib/api.js';

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

// askRows is the whole contract between the model box and the decision list, and
// it is the only place a fallback could be turned into a live approval. These are
// the cases that would let that happen.

const liveTv = {
  live: true,
  model: 'gemini-3.1-flash-lite',
  question: 'Buy a television for 780000 paise.',
  turns: [{
    tool: 'create_order', amount: 780000, currency: 'INR', item: 'television',
    gate: {
      outcome: 'BLOCK', rule: 'R5',
      reason: 'amount 780000 is over the per-call cap 500000',
      detail: { amount: 780000, max_txn: 500000 },
    },
  }],
  answer: 'It was refused.',
  questions_left: 2,
};

const [tv] = askRows(liveTv);
assert.equal(tv.name, 'television');
assert.equal(tv.paise, 780000);
assert.equal(tv.currency, 'INR');
assert.equal(tv.outcome, 'BLOCK');
assert.equal(tv.rule, 'R5');
assert.equal(tv.source, 'ai');
// The row has to carry liveness, not merely be built from it: the decision list
// mixes rows from several questions, and a recorded verdict sitting unmarked
// beside live ones is the one thing this panel exists to prevent.
assert.equal(tv.live, true);
assert.equal(askRows({ ...liveTv, live: false })[0].live, false);
assert.equal(askRows({ turns: liveTv.turns })[0].live, false);
// Paise in, rupees on the page. 780000 paise is 7,800 rupees and not 780,000.
assert.equal(money(tv.paise, tv.currency), '₹7,800.00');
assert.equal(
  plainReason(tv, tv.paise, tv.currency),
  '₹7,800.00 is above your ₹5,000.00 single-purchase limit.',
);

// A live HOLD keeps its call id: that handle is what POST /api/approve takes.
const liveHold = {
  live: true,
  turns: [{
    tool: 'create_order', amount: 350000, currency: 'INR', item: 'keyboard',
    gate: {
      outcome: 'HOLD', rule: 'approval', reason: 'needs approval',
      detail: { amount: 350000, approval_over: 200000 }, call_id: 'res_abc123',
    },
  }],
};
assert.equal(askRows(liveHold)[0].call_id, 'res_abc123');

// The same hold arriving in a fallback must not be approvable. The recorded run is
// a real session, but it is not this one, so approving it would invent an approval
// that never happened. The strip is keyed on `live`, never on what the recording
// happens to contain.
assert.equal(askRows({ ...liveHold, live: false })[0].call_id, undefined);
assert.equal(askRows({ ...liveHold, live: 'true' })[0].call_id, undefined);
const { live: _dropped, ...holdWithNoLiveKey } = liveHold;
assert.equal(askRows(holdWithNoLiveKey)[0].call_id, undefined);

// Nothing about a reply may take the panel down: the model can decline to call the
// tool at all, and the fallback payload is assembled by spreading a committed file.
assert.deepEqual(askRows({ live: true, turns: [] }), []);
assert.deepEqual(askRows({ live: true }), []);
assert.deepEqual(askRows({ live: true, turns: null }), []);
assert.deepEqual(askRows({}), []);
assert.deepEqual(askRows(null), []);
assert.deepEqual(askRows(undefined), []);

// A model may propose more than one purchase in one answer, and each gets its own
// row and its own verdict.
const two = askRows({
  live: true,
  turns: [
    { tool: 'create_order', amount: 120000, currency: 'INR', item: 'lamp',
      gate: { outcome: 'ALLOW', rule: null, reason: '' } },
    { tool: 'create_order', amount: 780000, currency: 'INR', item: 'tv',
      gate: { outcome: 'BLOCK', rule: 'R5', reason: 'over cap' } },
  ],
});
assert.equal(two.length, 2);
assert.deepEqual(two.map(r => r.outcome), ['ALLOW', 'BLOCK']);
// The keys have to differ, or React collapses two decisions into one row.
assert.notEqual(two[0].key, two[1].key);

// A malformed turn still draws. A gate with no verdict is shown as having none
// rather than being quietly dropped, because a dropped refusal is the one failure
// this page must never have.
const rough = askRows({ live: true, turns: [null, {}, { gate: {} }] });
assert.equal(rough.length, 3);
assert.equal(rough[0].name, 'unnamed request');
assert.equal(rough[0].outcome, undefined);
assert.equal(rough[0].currency, 'INR');

// The row is an allowlist, not a spread: a key a model talked into a tool argument
// cannot ride into the page just because the server passed it through.
const smuggled = askRows({
  live: true,
  turns: [{ tool: 'create_order', amount: 100, item: 'x', approved: 'yes',
            gate: { outcome: 'BLOCK', rule: 'R0', reason: 'no', evil: '<script>' } }],
})[0];
assert.equal(smuggled.approved, undefined);
assert.equal(smuggled.evil, undefined);

// Keys have to be unique across questions, not merely within one answer. The
// decision list accumulates, React reconciles on the key, and approving matches
// on call_id - a duplicate key put an "Approved" flash on the wrong row.
const q1 = askRows(liveTv, 0);
const q2 = askRows(liveTv, q1.length);
assert.notEqual(q1[0].key, q2[0].key);
assert.equal(q1[0].key, 'ai-0');
assert.equal(q2[0].key, 'ai-1');
assert.equal(askRows(liveTv)[0].key, 'ai-0');   // seed defaults to 0

// An amount the model wrote is not a number until it has been checked. It has to
// become null rather than NaN, so the row prints an em dash while the tally counts
// it as zero - one event cannot report two figures.
const bad = amount => askRows({ live: true, turns: [{ tool: 'create_order', item: 'x', amount,
  gate: { outcome: 'BLOCK', rule: 'R0', reason: 'no' } }] })[0];
assert.equal(bad('seven thousand').paise, null);
assert.equal(bad(undefined).paise, null);
assert.equal(bad(null).paise, null);
assert.equal(bad(NaN).paise, null);
assert.equal(bad(Infinity).paise, null);
assert.equal(bad(780000).paise, 780000);
assert.equal(bad(0).paise, 0);
// A numeric string is a malformed turn, not a number to be rescued: coercing it
// is what turns null and "" into a confident ₹0.00.
assert.equal(bad('780000').paise, null);
assert.equal(money(bad('seven thousand').paise), '—');
assert.equal(money(bad(780000).paise), '₹7,800.00');

console.log('money display self-check passed');
console.log('askRows contract self-check passed');
