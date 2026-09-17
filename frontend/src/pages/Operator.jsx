import { useEffect, useRef, useState } from 'react';
import { SiteHeader, Button } from '../components/Shell.jsx';
import { money } from '../lib/api.js';

const when = value => value ? new Date(value).toLocaleString() : 'Not recorded';
const age = value => `${Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 60000))} min`;

export default function Operator() {
  const token = useRef('');
  const active = useRef(null);
  const detail = useRef(null);
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [evidence, setEvidence] = useState(null);

  useEffect(() => () => { token.current = ''; active.current?.abort(); }, []);
  useEffect(() => {
    if (selected && window.matchMedia('(max-width: 700px)').matches) {
      detail.current?.focus({ preventScroll: true });
      detail.current?.scrollIntoView({ block: 'start' });
    }
  }, [selected]);

  function lock() {
    token.current = '';
    active.current?.abort();
    active.current = null;
    setData(null); setSelected(null); setEvidence(null);
    setError(''); setMessage(''); setBusy(false);
  }

  async function run(path = '/operator/inbox', method = 'GET') {
    setBusy(true); setError(''); setMessage(''); setEvidence(null);
    const controller = new AbortController();
    active.current = controller;
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const headers = { Authorization: `Bearer ${token.current}`, Accept: 'application/json' };
      const response = await fetch(path, { method, headers, credentials: 'omit', cache: 'no-store', signal: controller.signal });
      const body = await response.json();
      if (!token.current || active.current !== controller) return;
      if (response.status === 401) {
        lock(); setError('Access denied. Enter the operator token, not the agent token.'); return;
      }
      if (body.evidence) setEvidence(body.evidence);
      if (!response.ok) throw new Error(path.startsWith('/operator/') ? body.error || body.message
        : 'Action did not complete successfully. Refresh and inspect the current state before retrying.');
      if (method === 'GET') setData(body);
      else {
        setMessage(body.message || (path.startsWith('/approve/') ? 'Approval processed. The order is not a captured payment.'
          : 'Block unfrozen. Payment history has not been reconciled or changed.'));
        const refreshed = await fetch('/operator/inbox', { headers, credentials: 'omit', cache: 'no-store', signal: controller.signal });
        if (!refreshed.ok) throw new Error('Action returned successfully, but refresh failed. Refresh before taking another action.');
        const fresh = await refreshed.json();
        if (token.current && active.current === controller) setData(fresh);
      }
    } catch (e) {
      if (token.current && active.current === controller) setError(e.name === 'AbortError' || e instanceof TypeError
        ? 'No response received. The action may have completed. Refresh the inbox before retrying.' : e.message);
    } finally {
      clearTimeout(timeout);
      if (active.current === controller) { active.current = null; setBusy(false); }
    }
  }

  const items = data ? [
    ...data.reservations.map(r => ({ ...r, id: r.reservation_id,
      kind: r.outcome_unknown ? 'Unknown payment outcome' : 'Awaiting approval', type: 'reservation' })),
    ...data.blocks.map(b => ({ ...b, id: b.block_id, kind: 'Frozen spending block', type: 'block' })),
  ] : [];
  const item = items.find(i => i.id === selected);
  const canApprove = item?.awaiting_approval && !item.outcome_unknown && !item.frozen_at && !item.revoked_at
    && Date.parse(item.expires_at) > Date.now() && Date.parse(item.block_expires_at) > Date.now();

  return <>
    <SiteHeader><nav aria-label="Operator navigation"><a href="/">Public site</a></nav></SiteHeader>
    <main id="main-content" className="operator-workspace" tabIndex="-1">
      <header className="operator-heading"><div><h1>Recovery inbox</h1>
        <p>Review approvals, uncertain payments and frozen spending blocks.</p></div></header>
      {!data ? <form className="operator-login" onSubmit={e => {
        e.preventDefault(); token.current = new FormData(e.currentTarget).get('token').trim();
        e.currentTarget.reset(); run();
      }}>
        <h2>Operator access</h2><p>Enter your operator token. It stays in this tab’s memory and is cleared when you leave or lock the inbox.</p>
        <label htmlFor="operator-token">Operator token</label>
        <input id="operator-token" name="token" type="password" required autoComplete="off" spellCheck="false" disabled={busy} />
        <Button type="submit" variant="primary" disabled={busy}>{busy ? 'Checking access…' : 'Open inbox'}</Button>
      </form> : <>
        <div className="operator-toolbar"><p>{items.length} items · Checked {when(data.checked_at)}</p>
          <div className="operator-actions"><Button disabled={busy} onClick={() => run()}>{busy ? 'Working…' : 'Refresh inbox'}</Button>
            <Button onClick={lock}>Lock inbox</Button></div></div>
        <p className="operator-note">Includes agent and browser-demo blocks. Local demo order IDs start with “demo_”.</p>
      </>}
      <p className="sr-only" role="status">{busy ? 'Request in progress. Please wait.' : ''}</p>
      {error && <p className="operator-error" role="alert">{error}</p>}
      {message && <p className="operator-message" role="status">{message}</p>}
      {evidence && <section className="operator-evidence" aria-label="Provider evidence"><h2>Provider evidence</h2>
        <p>{evidence.status} · {money(evidence.amount, evidence.currency)}</p><code>{evidence.id} / {evidence.order_id}</code></section>}
      {data && (items.length === 0 ? <section className="operator-empty"><h2>No items need attention</h2>
        <p>There are no pending upstream approvals, unknown held payments or frozen blocks in the current ledger.</p></section>
        : <div className="operator-columns"><section aria-label="Recovery items" className="operator-list">
          {items.map(i => <button key={i.id} className="operator-row" aria-pressed={selected === i.id} disabled={busy}
            onClick={() => { setSelected(i.id); setEvidence(null); setMessage(''); setError(''); }}>
            <span>{i.kind}</span><strong>{money(i.type === 'block' ? i.held : i.amount, i.currency)}{i.type === 'block' ? ' held' : ''}</strong>
            <code>{i.id}</code><small>{age(i.frozen_at || i.created_at)} since {i.type === 'block' ? 'freeze' : 'reservation'}</small>
          </button>)}</section>
          <section ref={detail} tabIndex="-1" className="operator-detail" aria-label="Selected item">
            {!item ? <p>Select an item to inspect its state and available actions.</p> : <>
              <h2>{item.kind}</h2><dl>
                <div><dt>Block</dt><dd><code>{item.block_id}</code></dd></div>
                <div><dt>Caller</dt><dd><code>{item.caller_id}</code></dd></div>
                <div><dt>Created</dt><dd>{when(item.created_at)}</dd></div>
                <div><dt>Expires</dt><dd>{when(item.expires_at)}</dd></div>
                {item.type === 'reservation' && <><div><dt>Order</dt><dd>{item.order_id || 'Not recorded'}</dd></div>
                  <div><dt>Payment</dt><dd>{item.payment_id || 'Not recorded'}</dd></div></>}
              </dl>
              {item.type === 'reservation' && (item.outcome_unknown ? <>
                <p>The upstream request may still be running, or its response was lost. Funds remain reserved until the outcome is established.</p>
                <p>A matching captured payment updates the ledger. This check does not create a new charge.</p>
                <Button disabled={busy || !item.payment_id || !item.order_id} onClick={() => run(`/operator/recover/${encodeURIComponent(item.id)}`, 'POST')}>Check payment status</Button>
                {(!item.payment_id || !item.order_id) && <p>No complete payment reference is stored. Review the provider records manually; this inbox cannot safely release these funds.</p>}
              </> : <><p>Approval forwards the pending request through the gate. Current expiry, revocation and freeze checks still apply.</p>
                <Button disabled={busy || !canApprove} onClick={() => {
                  if (window.confirm(`Approve the pending request for ${money(item.amount, item.currency)}? This can create an upstream order.`)) run(`/approve/${encodeURIComponent(item.id)}`, 'POST');
                }}>Approve request</Button>{!canApprove && <p>This request is no longer approvable while its block is frozen, revoked or expired.</p>}</>)}
              {item.type === 'block' && <><p><strong>Freeze reason:</strong> {item.freeze_reason}</p>
                <p>Review the conflict and payment history before unfreezing. This restores permission to spend; it does not reconcile payments.</p>
                <Button disabled={busy} onClick={() => {
                  if (window.confirm('Confirm you reviewed the payment conflict. Unfreeze this block without changing its payment history?')) run(`/unfreeze/${encodeURIComponent(item.id)}`, 'POST');
                }}>Unfreeze after review</Button>
                <h3>Latest 20 reservations</h3><ol className="operator-history">{item.history.map(r => <li key={r.reservation_id}>
                  <strong>{r.state} · {money(r.amount, r.currency)}</strong><code>{r.reservation_id}</code>
                  <span>{r.order_id || 'No order'} · {r.payment_id || 'No payment'}</span>
                  <span>Created {when(r.created_at)}{r.settled_at ? ` · Settled ${when(r.settled_at)}` : ''}</span>
                </li>)}</ol></>}
            </>}
          </section></div>)}
    </main>
  </>;
}
