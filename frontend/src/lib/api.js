// Python serves JSON and never markup. Everything a visitor reads is assembled
// on this side, which is what lets the whole design change without touching a
// money path on the server.

// Every request goes through here so no panel can fail silently. A rejected
// promise is the caller's cue to render its error state, not to give up.
export async function api(path, body) {
  const r = await fetch(path, body === undefined ? { credentials: 'same-origin' } : {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch { data = {}; }
  if (!r.ok && data.error) throw new Error(data.error);
  if (!r.ok) throw new Error('the server answered ' + r.status);
  return data;
}

export function money(paise, currency = 'INR') {
  if (typeof paise !== 'number') return String(paise ?? '—');
  const sign = currency === 'INR' ? '₹' : currency + ' ';
  return sign + (paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 });
}

const MONEY_DETAIL_KEYS = new Set([
  'amount', 'available', 'reserved', 'spent', 'held', 'max_txn',
  'approval_over', 'available_after', 'reserved_amount',
]);

export function detailValue(key, value, currency = 'INR') {
  if (MONEY_DETAIL_KEYS.has(key) && typeof value === 'number') return money(value, currency);
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

// The rule ids are the audit trail's language, not a visitor's. This turns the
// common money refusals into one plain sentence each and falls back to the
// server's own reason for everything else.
export function plainReason(d, amount = d && d.detail && d.detail.amount, currency = 'INR') {
  const detail = (d && d.detail) || {};
  const reason = (d && d.reason) || '';
  const rawCap = /^amount (-?\d+) is over the per-call cap (-?\d+)$/.exec(reason);
  const shownAmount = typeof amount === 'number' ? amount : Number(rawCap?.[1] ?? NaN);
  const maxTxn = typeof detail.max_txn === 'number'
    ? detail.max_txn : Number(rawCap?.[2] ?? NaN);
  if (d && d.rule === 'R1' && typeof detail.available === 'number')
    return `${money(amount, currency)} is more than the ${money(detail.available, currency)} left in your budget.`;
  if (d && d.rule === 'R5' && Number.isFinite(shownAmount) && Number.isFinite(maxTxn))
    return `${money(shownAmount, currency)} is above your ${money(maxTxn, currency)} single-purchase limit.`;
  const bounds = /^amount (-?\d+) is outside (-?\d+)\.\.(-?\d+)$/.exec(reason);
  if (d && d.rule === 'R0' && bounds)
    return `${money(Number(bounds[1]), currency)} is outside the allowed range of `
      + `${money(Number(bounds[2]), currency)} to ${money(Number(bounds[3]), currency)}.`;
  // An R7 replay is an ALLOW that bought nothing: the first answer is handed
  // back and no second order is created. Left as a bare "replay of a completed
  // call" it reads on the attack page as the duplicate having got through.
  if (d && detail.replay)
    return 'Already answered. You got the first answer back — no second purchase was made.';
  if (d && d.outcome === 'HOLD' && typeof detail.approval_over === 'number')
    return `${money(amount, currency)} is above ${money(detail.approval_over, currency)}, so the AI must ask you first.`;
  return reason;
}

// Nav labels are what the page lets you do, not what the file is called. The
// route stays the short technical name because it is in the README and in links
// a judge may already hold.
export const PAGES = [
  ['/', 'Home'],
  ['/demo', 'Guided demo'],
  ['/attack', 'Try to break it'],
  ['/mutate', 'Remove a rule'],
  ['/trace', 'Follow the money'],
  ['/rules', 'The rules'],
  ['/evidence', 'The proof'],
];

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Just enough Markdown for the four committed documents this site renders:
// headings, tables, lists, fenced code, bold and inline code. A real parser
// would be a dependency, and these files are written by us. The output is fed
// to dangerouslySetInnerHTML, so escaping here is load-bearing.
export function md(text) {
  // esc() runs before any markup is added, so every character in the source is
  // already inert. Link targets get a second check: escaping does not touch a
  // colon, so `javascript:` would survive into an href.
  const href = u => (/^(https?:\/\/|\/|#)/.test(u) ? u : '#');
  const inline = s => esc(s)
    .replace(/`([^`]+)`/g, '<code class="font-mono text-[.92em] text-blue-ink">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
      (_, label, url) => `<a class="text-blue underline underline-offset-2" href="${href(url)}" rel="noopener">${label}</a>`);

  // Join soft-wrapped continuations before parsing. The committed documents are
  // wrapped at about 95 columns, so a bullet written over four source lines
  // became four separate paragraphs and the list structure disappeared.
  const raw = String(text || '').split('\n');
  const lines = [];
  let fenced = false;
  for (const line of raw) {
    if (line.startsWith('```')) fenced = !fenced;
    const opens = fenced || !line.trim()
      || /^(#{1,4}\s|\||```|\s*[-*]\s|\s*\d+\.\s)/.test(line);
    const prev = lines.length ? lines[lines.length - 1] : '';
    if (!opens && prev.trim() && !prev.startsWith('```') && !/^\|/.test(prev)) {
      lines[lines.length - 1] = prev.replace(/\s+$/, '') + ' ' + line.trim();
    } else {
      lines.push(line);
    }
  }

  const out = [];
  let list = null, table = null, fence = null;

  const closeList = () => { if (list) { out.push('</' + list + '>'); list = null; } };
  const closeTable = () => { if (table) { out.push('</tbody></table></div>'); table = null; } };
  const cells = line => line.replace(/^\||\|$/g, '').split('|').map(c => c.trim());

  for (const line of lines) {
    if (line.startsWith('```')) {
      if (fence === null) { closeList(); closeTable(); fence = []; }
      else {
        out.push('<pre class="overflow-auto border border-rule bg-paper p-4 font-mono text-sm">'
          + esc(fence.join('\n')) + '</pre>');
        fence = null;
      }
      continue;
    }
    if (fence !== null) { fence.push(line); continue; }

    if (/^\|/.test(line)) {
      const row = cells(line);
      if (!table) {
        closeList();
        table = true;
        out.push('<div class="max-w-full overflow-x-auto"><table class="w-full min-w-[900px] border-collapse"><thead><tr>'
          + row.map(c => `<th class="border-b border-rule p-2 text-left font-mono text-xs tracking-wider whitespace-nowrap text-blue uppercase">${inline(c)}</th>`).join('')
          + '</tr></thead><tbody>');
        continue;
      }
      if (row.every(c => /^:?-{2,}:?$/.test(c))) continue;
      out.push('<tr>' + row.map(c => `<td class="border-b border-rule p-2 align-top [overflow-wrap:normal] [word-break:normal]">${inline(c)}</td>`).join('') + '</tr>');
      continue;
    }
    closeTable();

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      const level = Math.min(6, heading[1].length + 1);
      const size = ['text-2xl', 'text-xl', 'text-lg', 'text-base', 'text-base'][heading[1].length - 1];
      out.push(`<h${level} class="mt-6 mb-2 font-bold tracking-tight ${size}">${inline(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      if (list !== 'ul') { closeList(); list = 'ul'; out.push('<ul class="my-3 list-disc pl-6 space-y-1">'); }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    const numbered = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (numbered) {
      if (list !== 'ol') { closeList(); list = 'ol'; out.push('<ol class="my-3 list-decimal pl-6 space-y-1">'); }
      out.push(`<li>${inline(numbered[1])}</li>`);
      continue;
    }
    closeList();
    if (line.trim()) out.push(`<p class="my-3 max-w-[78ch]">${inline(line)}</p>`);
  }
  if (fence !== null) out.push('<pre class="overflow-auto border border-rule bg-paper p-4 font-mono text-sm">' + esc(fence.join('\n')) + '</pre>');
  closeList(); closeTable();
  return out.join('\n');
}
