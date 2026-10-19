import { FormEvent, useState } from 'react';
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Blocks,
  Check,
  ChevronDown,
  CircleHelp,
  ClipboardCheck,
  Clock3,
  Factory,
  FileCheck2,
  MessageSquareText,
  PackageCheck,
  RefreshCw,
  Send,
  ShieldAlert,
  Truck,
} from 'lucide-react';
import type { ChatMessage, DataRecord } from './types';
import './sales-assurance.css';
import yashLogo from './assets/Yash logo.jpg';

type SalesMode = 'ibp' | 's4' | 'sales';

interface SalesAssuranceProps {
  data: DataRecord | null;
  messages: ChatMessage[];
  loading: boolean;
  error: string;
  onAsk: (value: string) => void;
  onNewConversation: () => void;
  onSelectMode: (mode: SalesMode) => void;
  onShowAgents: () => void;
}

const PREVIEW: DataRecord = {
  data_status: 'Illustrative scenario; not live SAP data',
  last_updated: 'Illustrative scenario snapshot',
  disruption: {
    event: 'Production line interruption',
    line: 'Filling line 2',
    plant: 'Hamburg plant',
    work_center: 'PKG-L2',
    started_at: 'Oct 14, 08:20',
    expected_recovery: 'Not available',
    affected_products: ['Citrus concentrate 20L', 'Citrus concentrate 5L'],
  },
  orders: [
    { order: '48001264', customer: 'Northstar Beverages', material: 'Citrus concentrate 20L', quantity: '1,200 cases', requested_date: 'Oct 16, 2026', confirmed_date: 'Oct 16, 2026', plant: 'Hamburg', value_exposed: '$980,000', risk: 'High', reason: 'All confirmed quantity is assigned to the interrupted line.' },
    { order: '48001281', customer: 'Harbor & Field Foods', material: 'Citrus concentrate 5L', quantity: '840 cases', requested_date: 'Oct 17, 2026', confirmed_date: 'Oct 18, 2026', plant: 'Hamburg', value_exposed: '$840,000', risk: 'High', reason: 'Production availability is dependent on the interrupted line.' },
    { order: '48001305', customer: 'Meridian Drinks Co.', material: 'Citrus concentrate 20L', quantity: '620 cases', requested_date: 'Oct 19, 2026', confirmed_date: 'Oct 20, 2026', plant: 'Hamburg', value_exposed: '$580,000', risk: 'Medium', reason: 'Schedule recovery is unconfirmed; current commitment may be affected.' },
  ],
  order_value_exposed: '$2.4M',
  alternatives: [
    { option: 'A', supply_source: 'Hamburg plant after recovery', delivery_outcome: 'Current dates at risk', constraint: 'Recovery estimate unavailable', freight_impact: 'No incremental freight identified', margin_impact: 'Not evaluated', recommendation: 'Baseline; reassess after recovery estimate' },
    { option: 'B', supply_source: 'Rotterdam plant candidate', delivery_outcome: 'Target dates may be achievable', constraint: 'Material, inventory, capacity, and transit checks required', freight_impact: 'Not evaluated', margin_impact: 'Not evaluated', recommendation: 'Preferred for feasibility review; not yet validated' },
  ],
  recommendation: {
    action: 'Validate Rotterdam as an alternate source for the highest-risk orders.',
    rationale: 'A second source may protect customer dates, but no feasibility checks are confirmed.',
    expected_impact: 'Delivery dates, incremental freight, and margin remain unverified.',
    approval_required: 'Sales manager and supply planning',
    approval_status: 'Review pending',
    sap_update_status: 'No SAP sales document update submitted',
  },
};

const SUGGESTIONS = [
  'Which sales orders are impacted by the production interruption?',
  'Compare the supply options and explain the margin unknowns.',
  'Prepare the recommendation for sales manager review.',
];

function record(value: unknown): DataRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as DataRecord
    : {};
}

function records(value: unknown): DataRecord[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function text(value: unknown, fallback = 'Not available'): string {
  return value == null || value === '' ? fallback : String(value);
}

function SalesAssurance({ data, messages, loading, error, onAsk, onNewConversation, onSelectMode, onShowAgents }: SalesAssuranceProps) {
  const [draft, setDraft] = useState('');
  const [selectedOrder, setSelectedOrder] = useState(0);
  const scenario = data?.evidence_type === 'sales_assurance' ? data : PREVIEW;
  const disruption = record(scenario.disruption);
  const recommendation = record(scenario.recommendation);
  const orders = records(scenario.orders);
  const alternatives = records(scenario.alternatives);
  const selected = orders[selectedOrder] || orders[0] || {};
  const approvalStatus = text(recommendation.approval_status, 'Review pending');

  function submit(value: string) {
    const trimmed = value.trim();
    if (!trimmed || loading) return;
    setDraft('');
    onAsk(trimmed);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(draft);
  }

  return (
    <div className="assurance-app">
      <header className="assurance-topbar">
        <a className="assurance-brand" href="#top" aria-label="Commercial Assurance Agent home">
          <span className="assurance-brand-mark"><img src={yashLogo} alt="YASH Technologies" /></span>
          <span><strong>Commercial Assurance Agent</strong><small>SAP Sales · Supply Risk · Decision Support</small></span>
        </a>
        <nav className="assurance-nav" aria-label="Agent workspace">
          <button type="button" className="assurance-mode active" onClick={() => onSelectMode('sales')}>Sales Assurance</button>
          <button type="button" className="assurance-mode" onClick={() => onSelectMode('ibp')}>IBP</button>
          <button type="button" className="assurance-mode" onClick={() => onSelectMode('s4')}>S/4 Capacity</button>
        </nav>
        <div className="assurance-top-actions">
          <button type="button" className="all-agents-button" onClick={onShowAgents}><Blocks size={14} /> All agents</button>
          <span className="ready-state"><i /> Ready</span>
          <button type="button" className="assurance-icon-button" onClick={onNewConversation} aria-label="Start a new conversation" title="New conversation"><RefreshCw size={16} /></button>
        </div>
      </header>

      <main className="assurance-content" id="top">
        <section className="assurance-title-row">
          <div>
            <span className="assurance-eyebrow"><span /> SALES · SUPPLY RISK · DECISION SUPPORT</span>
            <h1>Commercial Assurance</h1>
            <p>Delivery commitments, order exposure, and supply options in one decision view.</p>
          </div>
          <div className="scenario-stamp"><span>DATA STATUS</span><strong>{text(scenario.last_updated, 'Illustrative scenario snapshot')}</strong></div>
        </section>

        <div className="preview-notice"><CircleHelp size={15} /></div>

        <section className="disruption-strip" aria-labelledby="disruption-heading">
          <div className="disruption-mark"><AlertTriangle size={19} /></div>
          <div className="disruption-main">
            <span className="assurance-section-label">DISRUPTION SUMMARY · ILLUSTRATIVE</span>
            <h2 id="disruption-heading">{text(disruption.event)}</h2>
            <p><Factory size={13} /> {text(disruption.line)} <span className="summary-separator">·</span> {text(disruption.work_center)} <span className="summary-separator">·</span> {text(disruption.plant)}</p>
          </div>
          <div className="disruption-fact"><span>START TIME</span><strong>{text(disruption.started_at)}</strong></div>
          <div className="disruption-fact"><span>EXPECTED RECOVERY</span><strong>{text(disruption.expected_recovery)}</strong></div>
          <div className="disruption-products"><span>AFFECTED PRODUCTS</span><strong>{Array.isArray(disruption.affected_products) ? disruption.affected_products.join(' · ') : 'Not available'}</strong></div>
        </section>

        <section className="assurance-metrics" aria-label="Commercial impact summary">
          <article className="assurance-metric risk-metric"><span>ORDERS AT RISK</span><strong>{orders.length.toString().padStart(2, '0')}</strong><small>Illustrative order count</small></article>
          <article className="assurance-metric value-metric"><span>DELIVERY EXPOSURE</span><strong>{text(scenario.order_value_exposed)}</strong><small>Order value exposed, not lost revenue</small></article>
          <article className="assurance-metric supply-metric"><span>ALTERNATIVE SUPPLY</span><strong>Candidate</strong><small>Feasibility checks required</small></article>
          <article className="assurance-metric approval-metric"><span>RECOMMENDED ACTION</span><strong>{approvalStatus}</strong><small>Manager decision required</small></article>
        </section>

        <div className="assurance-columns">
          <section className="orders-section" id="impacted-orders" aria-labelledby="orders-heading">
            <div className="assurance-section-heading">
              <div><span className="assurance-section-label">SAP SD · ILLUSTRATIVE ORDER ANALYSIS</span><h2 id="orders-heading">Impacted sales orders</h2></div>
              <span className="table-count">{orders.length} orders <ChevronDown size={13} /></span>
            </div>
            <div className="orders-table-wrap">
              <table className="orders-table">
                <thead><tr><th>SALES ORDER</th><th>CUSTOMER</th><th>MATERIAL</th><th>QUANTITY</th><th>REQUESTED / CONFIRMED</th><th>PLANT</th><th>VALUE EXPOSED</th><th>RISK</th></tr></thead>
                <tbody>
                  {orders.map((order, index) => (
                    <tr key={String(order.order ?? index)} className={selectedOrder === index ? 'selected-order' : ''}>
                      <td><button type="button" className="order-select" aria-pressed={selectedOrder === index} onClick={() => setSelectedOrder(index)}>{text(order.order)}</button></td>
                      <td>{text(order.customer)}</td><td>{text(order.material)}</td><td>{text(order.quantity)}</td>
                      <td><span>{text(order.requested_date)}</span><small>Confirmed {text(order.confirmed_date)}</small></td>
                      <td>{text(order.plant)}</td><td className="value-cell">{text(order.value_exposed)}</td>
                      <td><span className={`risk-pill ${String(order.risk || '').toLowerCase()}`}>{text(order.risk)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="order-reason"><span className="reason-icon"><ShieldAlert size={15} /></span><div><strong>Why order {text(selected.order)} is flagged</strong><p>{text(selected.reason)}</p></div><span className="illustrative-tag">ILLUSTRATIVE</span></div>
            <p className="exposure-note"><ArrowDownRight size={14} /> Total value is exposed to delivery risk; it is not represented as lost revenue.</p>

            <section className="comparison-section" id="alternatives" aria-labelledby="alternatives-heading">
              <div className="assurance-section-heading">
                <div><span className="assurance-section-label">DELIVERY · COST · MARGIN</span><h2 id="alternatives-heading">Compare supply options</h2></div>
                <span className="validation-tag"><Clock3 size={12} /> Feasibility not validated</span>
              </div>
              <div className="comparison-table-wrap">
                <table className="comparison-table">
                  <thead><tr><th>ASSESSMENT</th>{alternatives.map((item) => <th key={String(item.option)}><span className={item.option === 'B' ? 'preferred-option' : ''}>OPTION {text(item.option)}</span></th>)}</tr></thead>
                  <tbody>
                    <tr><th>Supply source</th>{alternatives.map((item) => <td key={`source-${String(item.option)}`}>{text(item.supply_source)}</td>)}</tr>
                    <tr><th>Delivery outcome</th>{alternatives.map((item) => <td key={`delivery-${String(item.option)}`}>{text(item.delivery_outcome)}</td>)}</tr>
                    <tr><th>Constraints</th>{alternatives.map((item) => <td key={`constraint-${String(item.option)}`}>{text(item.constraint)}</td>)}</tr>
                    <tr><th>Freight impact</th>{alternatives.map((item) => <td key={`freight-${String(item.option)}`}>{text(item.freight_impact)}</td>)}</tr>
                    <tr><th>Margin impact</th>{alternatives.map((item) => <td key={`margin-${String(item.option)}`}><span className="unknown-value">{text(item.margin_impact)}</span></td>)}</tr>
                    <tr><th>Assessment</th>{alternatives.map((item) => <td key={`recommendation-${String(item.option)}`} className={item.option === 'B' ? 'preferred-cell' : ''}>{text(item.recommendation)}</td>)}</tr>
                  </tbody>
                </table>
              </div>
            </section>
          </section>

          <aside className="decision-column" aria-label="Recommendation and agent">
            <section className="recommendation-panel" id="recommendation">
              <div className="recommendation-header"><span className="recommendation-icon"><PackageCheck size={17} /></span><div><span className="assurance-section-label">AGENT RECOMMENDATION</span><span className="pending-badge"><Clock3 size={11} /> {approvalStatus}</span></div></div>
              <h2>{text(recommendation.action)}</h2>
              <div className="justification-block"><span>BUSINESS JUSTIFICATION</span><p>{text(recommendation.rationale)}</p></div>
              <div className="impact-block"><span>EXPECTED IMPACT</span><p>{text(recommendation.expected_impact)}</p></div>
              <div className="approval-owner"><FileCheck2 size={15} /><span>Required review</span><strong>{text(recommendation.approval_required)}</strong></div>
              <div className="sap-status"><ClipboardCheck size={14} /><span>{text(recommendation.sap_update_status)}</span></div>
              <div className="decision-actions">
                <button type="button" className="approve-button" onClick={() => submit('Approve recommendation')} disabled={loading}><Check size={15} /> Approve for review</button>
                <div className="secondary-actions">
                  <button type="button" className="reject-button" onClick={() => submit('Reject recommendation')} disabled={loading}>Reject recommendation</button>
                  <button type="button" className="reject-button" onClick={() => submit('Request further analysis')} disabled={loading}>Request further analysis <ArrowRight size={14} /></button>
                </div>
              </div>
            </section>

            <section className="assistant-panel" aria-labelledby="assistant-heading">
              <div className="assistant-heading"><span className="assistant-icon"><MessageSquareText size={16} /></span><div><span className="assurance-section-label">FOLLOW-UP ANALYSIS</span><h2 id="assistant-heading">Ask about this scenario</h2></div></div>
              <div className="assistant-messages" aria-live="polite">
                {messages.length === 0 ? <p className="assistant-intro">Explore order exposure, compare options, or prepare a review request.</p> : messages.slice(-4).map((message) => <div className={`assistant-message ${message.role}`} key={message.id}><span>{message.role === 'user' ? 'YOU' : 'ASSURANCE AGENT'}</span><p>{message.content}</p></div>)}
                {loading && <div className="assistant-loading"><span /><span /><span /> Reviewing scenario</div>}
              </div>
              {error && <p className="assistant-error" role="alert">{error}</p>}
              {messages.length === 0 && <div className="suggestion-list">{SUGGESTIONS.map((suggestion) => <button type="button" key={suggestion} onClick={() => submit(suggestion)} disabled={loading}>{suggestion}<ArrowUpRight size={13} /></button>)}</div>}
              <form className="assurance-composer" onSubmit={handleSubmit}>
                <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Ask a follow-up question..." aria-label="Ask the Commercial Assurance Agent" disabled={loading} />
                <button type="submit" aria-label="Send question" disabled={!draft.trim() || loading}><Send size={15} /></button>
              </form>
              <p className="assistant-footnote"><Truck size={12} /> No delivery or sales order changes are submitted here.</p>
            </section>
          </aside>
        </div>

        <footer className="assurance-footer"><span><Check size={13} /> Scenario values are illustrative</span><span><Clock3 size={13} /> Recovery estimate: {text(disruption.expected_recovery)}</span><span><CircleHelp size={13} /> Feasibility and margin require validation</span></footer>
      </main>
    </div>
  );
}

export default SalesAssurance;