import { useEffect, useRef, useState } from 'react';
import { api, money } from '../lib/api.js';
import { ProofPage, RazorpayBrand } from '../components/Shell.jsx';
import {
  Async, Button, Disclosure, ErrorLine, Note, Panel, Skeleton, useAsync,
} from '../components/ui.jsx';

// Sheet 04 - one payment, start to finish. The life of a purchase is drawn as
// a dimension chain: one hairline down the sheet, a tick at every station, and
// the time read off in the margin. The old version used a 2px coloured rule
// with filled circles on it, which is a product timeline; a chain of ticks is
// what a drawing uses to say these points are on one measured run.

// The log writes its own vocabulary. This is the only place it is translated,
// so an event added to the log shows up untranslated rather than silently as
// something it is not.
const EXPLAIN = {
  allow: ['Policy verdict — ALLOW', 'Pre-authorization validated against remaining budget and policy constraints.'],
  block: ['Policy verdict — BLOCK', 'Request violated spending policy. Upstream payment creation halted.'],
  hold: ['Policy verdict — HOLD', 'Amount exceeds automated threshold, routed for manual operator review.'],
  reservation_bound: ['Order ID bound',
    'The reservation is now cryptographically bound to the upstream Razorpay order.'],
  debit_committed: ['Debit committed',
    'Reservation settled and committed to the ledger upon verified payment capture.'],
  reservation_released: ['Reservation released', 'Transaction cancelled or declined. Reserved funds returned to budget.'],
  reservation_expired: ['Authorization expired', 'Payment window timed out. Uncaptured reservation refunded to budget.'],
  COLD_START_LEDGER_RESET: ['Fresh ledger initialized',
    'No previous ledger state found for this session, initialized from baseline settings.'],
};

const receiptName = receipt => receipt?.replace(/-[A-Za-z0-9_-]{6}$/, '');

export default function Trace() {
  const list = useAsync(() => api('/api/trace'));
  const [order, setOrder] = useState(null);
  const [chain, setChain] = useState({ idle: true });
  const chainRef = useRef(null);

  // The settled purchase is the one worth opening first: it is the only row in
  // the sample where money actually moved.
  useEffect(() => {
    if (!order && list.data) {
      const settled = list.data.purchases.find(p => p.settled);
      if (settled) setOrder(settled.order_id);
    }
  }, [list.data, order]);

  useEffect(() => {
    if (!order) return;
    let live = true;
    setChain({ loading: true });
    api('/api/trace?order=' + encodeURIComponent(order))
      .then(t => live && setChain({ data: t }))
      .catch(e => live && setChain({ error: e.message }));
    return () => { live = false; };
  }, [order]);

  function pick(id) {
    setOrder(id);
    const reduce = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    chainRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest' });
  }

  const settled = list.data?.purchases.filter(p => p.settled).length;

  return (
    <ProofPage
      current="/trace"
      brand={<>
        <RazorpayBrand>Recorded in <strong>Razorpay test mode</strong></RazorpayBrand>
        <p className="chip-note">Razorpay's own mark, shown because these records
        came from their test mode. It is not an endorsement.</p>
      </>}
      title="Follow one real payment, start to finish"
      lede={'One ₹100 purchase that genuinely happened — an order created through the gate, a '
        + 'card paid by hand in a browser, and the charge taken back through the gate. Every '
        + 'step below was written down as it happened, and none of it can be edited afterwards '
        + 'without showing.'}
      stats={list.data ? [
        ['Records', String(list.data.purchases.length).padStart(2, '0')],
        ['Settled', String(settled).padStart(2, '0')],
      ] : []}
      footer={'Each line carries a fingerprint of the line before it. Change one, delete one, '
        + 'or swap two around, and every link after that point stops matching, and the check '
        + 'names the first line that broke.'}
    >
      <Panel
        title="Pick a purchase"
        intro="These are real records from the file committed to the repository. Most stopped at
               the order, because nobody paid the card. One went all the way."
      >
        <Async state={list} height="8rem">
          {t => t.purchases.length === 0
            ? <Note>This sample holds no purchases.</Note>
            : (
              <div className="pick-grid">
                {t.purchases.map(p => (
                  <Button
                    key={p.order_id}
                    variant={p.order_id === order ? 'primary' : 'outline'}
                    className="pick is-stacked"
                    roll={false}
                    onClick={() => pick(p.order_id)}
                  >
                    <span className="pick__name">{receiptName(p.receipt) || p.order_id}</span>
                    <span className="pick__meta">
                      {money(p.amount)} · {p.settled ? 'paid and charged' : 'never paid'}
                    </span>
                  </Button>
                ))}
              </div>
            )}
        </Async>
      </Panel>

      <div ref={chainRef}>
        <Panel
          title="What was written down"
          intro="Read it top to bottom. This is the whole life of one purchase."
        >
          {chain.idle && (list.data?.purchases?.length === 0
            ? <Note>No transactions recorded yet in this ledger.</Note>
            : <Note>Pick a purchase above.</Note>)}
          {chain.loading && <Skeleton height="10rem" />}
          {chain.error && <ErrorLine>{chain.error}</ErrorLine>}
          {chain.data && (
            <>
              <p className="chain-head">
                {receiptName(chain.data.purchase?.receipt)} · {money(chain.data.purchase?.amount)} ·{' '}
                {chain.data.purchase?.settled
                  ? <b className="text-allow">paid and charged</b>
                  : <span className="text-muted">held, never paid</span>}
              </p>

              {/* A dimension chain: one hairline run, a tick at every station,
                  the time read off in the margin. */}
              <ol className="chain stagger">
                {chain.data.steps.map((s, i) => {
                  const [what, why] = EXPLAIN[s.event] || [s.event, s.reason || ''];
                  return (
                    <li key={i}>
                      <span className="chain__at">{(s.ts || '').slice(11, 19)}</span>
                      <span className="chain__tick" aria-hidden="true" />
                      <div className="chain__body">
                        <p className="chain__what">{what}</p>
                        <p className="chain__why">{why}</p>
                        {s.amount != null && (
                          <span className="chain__amount">{money(s.amount, s.currency)}</span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>

              <Disclosure className="mt-7" summary="Show the tamper-proof links"
                          hint="what makes this un-editable">
                <Note className="mb-4">
                  The last column is a fingerprint of the previous line. It is what turns a
                  list of claims into a chain nobody can quietly rewrite.
                </Note>
                <div className="table-scroll">
                  <table className="sheet-table">
                    <thead>
                      <tr>
                        {['when', 'step', 'links back to'].map(h => <th key={h}>{h}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {chain.data.steps.map((s, i) => (
                        <tr key={i}>
                          <td className="is-num">{(s.ts || '').slice(11, 19)}</td>
                          <td>
                            <code>{s.event}</code>
                            {s.tool && ' ' + s.tool}
                            {s.rule && <code className="ml-1">{s.rule}</code>}
                          </td>
                          <td className="is-num is-faint">{(s.prev_hash || '').slice(0, 12)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Note className="mt-4">
                  Order <code>{chain.data.order_id}</code>
                </Note>
              </Disclosure>
            </>
          )}
        </Panel>
      </div>
    </ProofPage>
  );
}
