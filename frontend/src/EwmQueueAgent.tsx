import { FormEvent, useState } from 'react';
import { ArrowLeft, RefreshCw, Search, Warehouse, AlertCircle } from 'lucide-react';
import './ewm-queue-agent.css';
type Queue = { Lgnum?: string; Qname?: string; Qstate?: string; Direction?: string; Mtypetext?: string; Busobj?: string; BusobjKey?: string; Errmess?: string; Msgid?: string; Msgno?: string; Wqname?: string; Dest?: string; Creadate?: string; EntriesInQueue?: number; MqDescription?: string; guidance?: string[]; transaction?: string };
type Result = { queues: Queue[]; count: number; truncated?: boolean };
function display(value: unknown) { return value === undefined || value === null || value === '' ? '—' : String(value); }
function displayDate(value?: string) { if (!value) return '—'; const match = /^\/Date\((-?\d+)(?:[+-]\d+)?\)\/$/.exec(value); if (match) { const date = new Date(Number(match[1])); return Number.isNaN(date.getTime()) ? value : date.toISOString().slice(0, 10); } return value.slice(0, 10); }
function direction(q: Queue) { return q.Mtypetext || q.Direction?.match(/\\Q([^@]+)/)?.[1] || q.Direction || 'Unknown'; }
export default function EwmQueueAgent() {
  const [message, setMessage] = useState(''); const [scope, setScope] = useState('');
  const [chat, setChat] = useState<{role: 'agent' | 'user'; text: string}[]>([{role: 'agent', text: 'Which warehouse would you like to monitor? You can also include a creation date or range, such as today or last 3 days.'}]);
  const [result, setResult] = useState<Result | null>(null); const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(false); const [error, setError] = useState(''); const [filter, setFilter] = useState('');
  async function load(value: string, dateFrom: string | null, dateTo: string | null) {
    setLoading(true); setError(''); setResult(null); setSelected(null); setScope(value.trim());
    try { const params = new URLSearchParams(); if (value.trim()) params.set('warehouse', value.trim()); if (dateFrom) params.set('date_from', dateFrom); if (dateTo) params.set('date_to', dateTo);
      const response = await fetch(`/api/ewm/queues${params.size ? `?${params}` : ''}`);
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`); }
      const data: Result = await response.json(); setResult(data); setSelected(data.queues.length ? 0 : null);
      setChat(previous => [...previous, {role: 'agent', text: `SAP returned ${data.count} queue${data.count === 1 ? '' : 's'} for warehouse ${value}${dateFrom || dateTo ? ` (creation date: ${dateFrom || 'any start'} to ${dateTo || 'any end'})` : ' (all creation dates)'}.${data.truncated ? ' SAP results exceeded the 1,000-record scan limit, so some matching queues may be missing.' : ''} Select a queue below to see its reported error and suggested checks.`}]);
    } catch (e) { const detail = e instanceof Error ? e.message : 'Unable to load queues.'; setError(detail); setChat(previous => [...previous, {role: 'agent', text: detail}]); } finally { setLoading(false); }
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); const input = message.trim(); if (!input || loading) return;
    setMessage(''); setChat(previous => [...previous, {role: 'user', text: input}]); setLoading(true); setError('');
    const now = new Date(); const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    try {
      const response = await fetch('/api/ewm/interpret', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: input, warehouse: scope, today})});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not understand the request.');
      setFilter(''); await load(data.warehouse, data.date_from, data.date_to);
    } catch (e) { setChat(previous => [...previous, {role: 'agent', text: e instanceof Error ? e.message : 'Could not understand the request.'}]); setLoading(false); }
  }
  const queues = result?.queues || [];
  const matches = queues.map((queue, index) => ({ queue, index })).filter(({ queue }) => [queue.Qname, queue.Busobj, queue.BusobjKey, queue.Errmess, queue.Qstate, queue.Lgnum].some(v => String(v || '').toLowerCase().includes(filter.toLowerCase())));
  const active = selected === null || !matches.some(item => item.index === selected) ? null : queues[selected];
  return <main className="ewm-shell"><header className="ewm-top"><button type="button" onClick={() => window.location.assign('/#agents')}><ArrowLeft size={16}/> All agents</button><div><Warehouse size={19}/><strong>EWM Queue Monitoring</strong><span>Operations workspace</span></div><span className="ewm-readonly">READ ONLY</span></header>
    <div className="ewm-content"><div className="ewm-intro"><div><span className="ewm-eyebrow">WAREHOUSE OPERATIONS / QUEUE MONITOR</span><h1>Find the reason.<br/><em>Move deliveries forward.</em></h1><p>Review inbound and outbound delivery queues, understand the reported error, and identify the next step for your SAP team.</p></div><div className="ewm-intro-icon"><Warehouse size={36}/><span>SMQ1 / SMQ2</span></div></div>
      <section className="ewm-chat" aria-label="Queue monitoring conversation"><div className="ewm-chat-messages" aria-live="polite">{chat.map((item, i) => <p className={`ewm-chat-${item.role}`} key={i}><strong>{item.role === 'agent' ? 'Queue agent' : 'You'}</strong>{item.text}</p>)}</div><form className="ewm-controls" onSubmit={submit}><label>Message the queue agent <input value={message} onChange={e => setMessage(e.target.value)} placeholder="Show queues for warehouse 5070 from last week" /></label><button type="submit" disabled={loading || !message.trim()}><RefreshCw size={15}/> {loading ? 'Loading...' : 'Send'}</button><span>Enter a warehouse to load live SAP data</span></form></section>
      {error && <div className="ewm-notice ewm-error" role="alert"><AlertCircle size={18}/><div><strong>Could not load queues</strong><p>{error}</p></div></div>}{loading && <div className="ewm-notice" role="status">Loading queue data from SAP...</div>}
      {result && <><div className="ewm-summary"><div><span>QUEUES RETRIEVED</span><strong>{result.count ?? queues.length}</strong></div><div><span>DISPLAYED</span><strong>{matches.length}</strong></div><div><span>SCOPE</span><strong>{scope || 'All warehouses'}</strong></div><p>{result.truncated ? 'Scan limit reached; some matching queues may be missing. Narrow the date range or warehouse selection.' : 'Select a queue to see its error and suggested checks.'}</p></div>
      <div className="ewm-workspace"><section className="ewm-list"><div className="ewm-section-head"><div><span className="ewm-eyebrow">QUEUE OVERVIEW</span><h2>Delivery queues</h2></div><span>{matches.length} shown</span></div><label className="ewm-search"><Search size={16}/><input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Search queue, delivery, error..." aria-label="Search queues" /></label>
      {matches.length === 0 ? <p className="ewm-empty">{queues.length ? 'No queues match your search.' : 'No queues returned for this selection.'}</p> : <div className="ewm-rows">{matches.map(({queue:q,index}) => <button type="button" key={`${q.Lgnum}-${q.Qname}-${index}`} className={`ewm-row ${selected === index ? 'active' : ''}`} onClick={() => setSelected(index)}><div><span className="ewm-status">{display(q.Qstate)}</span><span>{direction(q)} · WH {display(q.Lgnum)}</span></div><strong>{display(q.Busobj)} {display(q.BusobjKey)}</strong><p>{q.Errmess || 'No error message provided'}</p><small>{display(q.Qname)}</small></button>)}</div>}</section>
      <section className="ewm-detail" aria-live="polite">{active ? <><div className="ewm-section-head"><div><span className="ewm-eyebrow">QUEUE DETAILS</span><h2>{display(active.Busobj)} <span>{display(active.BusobjKey)}</span></h2></div><span className="ewm-status">{display(active.Qstate)}</span></div><p className="ewm-queue-name">{display(active.Qname)}</p><div className="ewm-reason"><span>REPORTED MESSAGE</span><strong>{active.Errmess || 'No error message provided by SAP'}</strong><small>Message {display(active.Msgid)} / {display(active.Msgno)}</small></div><h3>Suggested checks</h3><p className="ewm-advice">Use these suggestions to investigate the underlying issue in SAP.</p><ol className="ewm-guidance">{(active.guidance || []).map((item, i) => <li key={i}>{item}</li>)}</ol><dl className="ewm-facts">{[['Direction', direction(active)], ['Transaction', active.transaction], ['Warehouse', active.Lgnum], ['Queue entries', active.EntriesInQueue], ['Predecessor queue', active.Wqname], ['Destination', active.Dest], ['Created date', displayDate(active.Creadate)], ['Process', active.MqDescription]].map(([label,value]) => <div key={String(label)}><dt>{label}</dt><dd>{display(value)}</dd></div>)}</dl></> : <p className="ewm-empty">Select a queue to inspect its details.</p>}</section></div></>}
    </div></main>;
}
