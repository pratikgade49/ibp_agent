import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity,
  ArrowDown,
  ArrowUpRight,
  Check,
  ChevronDown,
  Clock3,
  Database,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Send,
  Sparkles,
  X,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { AskResponse, ChatMessage, Conversation, DataRecord, Insight } from './types';

const STORAGE_KEY = 'ibp-demand-desk-conversations-v1';
const EMPTY_MESSAGES: ChatMessage[] = [];
const PROMPTS = [
  'Which products have the highest forecast?',
  'Show forecast anomalies',
  'Is sales history ready?',
  'Which resources are bottlenecks in the next three periods?',
];

function asRecord(value: unknown): DataRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as DataRecord)
    : null;
}

function asRecords(value: unknown): DataRecord[] {
  return Array.isArray(value)
    ? value.map(asRecord).filter((item): item is DataRecord => item !== null)
    : [];
}

function readConversations(): Conversation[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const stored: unknown = JSON.parse(saved);
      if (!Array.isArray(stored)) return [];
      return stored.filter(
        (item): item is Conversation =>
          typeof item === 'object' &&
          item !== null &&
          typeof item.id === 'string' &&
          Array.isArray(item.messages),
      );
    }

    const legacy: unknown = JSON.parse(localStorage.getItem('ibp-recent-chats') || '[]');
    const migrated = Array.isArray(legacy)
      ? legacy.map((entry, conversationIndex): Conversation => {
          const item = asRecord(entry) || {};
          const id = typeof item.id === 'string' ? item.id : `legacy-${conversationIndex}`;
          const messages = asRecords(item.messages).map((message, messageIndex): ChatMessage => ({
            id: `legacy-${id}-${messageIndex}`,
            role: message.role === 'user' ? 'user' : 'assistant',
            content: String(message.text ?? message.content ?? ''),
          }));
          const parsedDate = new Date(String(item.updatedAt || ''));
          return {
            id,
            conversationId: id,
            title: String(item.title || 'Planning conversation'),
            updatedAt: Number.isNaN(parsedDate.getTime()) ? new Date().toISOString() : parsedDate.toISOString(),
            messages,
          };
        })
      : [];
    if (migrated.length) return migrated;

    const oldTranscript: unknown = JSON.parse(localStorage.getItem('ibp-transcript') || '[]');
    const oldMessages = asRecords(oldTranscript).map((message, messageIndex): ChatMessage => ({
      id: `legacy-current-${messageIndex}`,
      role: message.role === 'user' ? 'user' : 'assistant',
      content: String(message.text ?? message.content ?? ''),
    }));
    if (!oldMessages.length) return [];
    const id = localStorage.getItem('ibp-conversation-id') || 'legacy-current';
    return [{
      id,
      conversationId: id,
      title: getTitle(oldMessages.find((message) => message.role === 'user')?.content || 'Planning conversation'),
      updatedAt: new Date().toISOString(),
      messages: oldMessages,
    }];
  } catch {
    return [];
  }
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'N/A';
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  }
  return String(value);
}

function isUsefulData(data: DataRecord | undefined): data is DataRecord {
  return Boolean(
    data &&
      (Array.isArray(data.results) ||
        Array.isArray(data.alert_results) ||
        Array.isArray(data.anomalies) ||
        typeof data.ready === 'boolean' ||
        data.key_figures),
  );
}

function latestInsight(messages: ChatMessage[]): DataRecord | null {
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const insights = messages[messageIndex].insights || [];
    for (let insightIndex = insights.length - 1; insightIndex >= 0; insightIndex -= 1) {
      const data = insights[insightIndex].data;
      if (isUsefulData(data)) return data;
    }
  }
  return null;
}

function upsertConversation(current: Conversation[], conversation: Conversation): Conversation[] {
  return [conversation, ...current.filter((item) => item.id !== conversation.id)]
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .slice(0, 12);
}

function getTitle(question: string): string {
  return question.length > 54 ? `${question.slice(0, 51)}...` : question;
}

function DataTable({ rows, columns }: { rows: DataRecord[]; columns?: string[] }) {
  const visibleColumns = columns || Object.keys(rows[0] || {}).slice(0, 7);
  if (rows.length === 0) return <p className="table-empty">No rows returned for this view.</p>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>{visibleColumns.map((column) => <th key={column}>{column.replace(/_/g, ' ')}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 40).map((row, index) => (
            <tr key={`${String(row.id ?? row.product ?? row.resource ?? 'row')}-${index}`}>
              {visibleColumns.map((column) => (
                <td key={column}>{formatValue(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 40 && <p className="table-footnote">Showing 40 of {rows.length} rows</p>}
    </div>
  );
}

function MetricStrip({ items }: { items: Array<{ label: string; value: unknown; tone?: string }> }) {
  return (
    <div className="metric-strip">
      {items.map((item) => (
        <div className={`metric-tile ${item.tone || ''}`} key={item.label}>
          <span>{item.label}</span>
          <strong>{formatValue(item.value)}</strong>
        </div>
      ))}
    </div>
  );
}

function CapacityEvidence({ data }: { data: DataRecord }) {
  const tableMap = asRecord(data.tables);
  const tables = tableMap && Object.keys(tableMap).length
    ? Object.entries(tableMap)
    : [[String(data.resource_type || 'capacity'), {
        title: 'Capacity results',
        rows: data.results,
        key_figures: data.key_figures,
      } as DataRecord] as [string, DataRecord]];
  const rowCount = tables.reduce((total, [, value]) => total + asRecords(asRecord(value)?.rows).length, 0);
  const bottleneckCount = tables.reduce((total, [, value]) => {
    const table = asRecord(value);
    const summary = asRecord(table?.summary);
    return total + Number(summary?.bottleneck_count ?? table?.bottleneck_count ?? 0);
  }, 0);

  return (
    <>
      <MetricStrip items={[
        { label: 'Rows evaluated', value: data.total_rows_evaluated ?? rowCount },
        { label: 'Bottlenecks', value: bottleneckCount, tone: bottleneckCount ? 'alert' : '' },
        { label: 'Activity rows', value: data.activity_row_count ?? 'N/A' },
        { label: 'Analysis status', value: data.analysis_status ?? 'Complete' },
      ]} />
      {data.data_quality_warning && <div className="warning-note">{String(data.data_quality_warning)}</div>}
      {tables.map(([resourceType, rawTable]) => {
        const table = asRecord(rawTable) || {};
        const rows = asRecords(table.rows);
        const keyFigures = asRecord(table.key_figures) || {};
        const supply = String(keyFigures.supply || 'CAPASUPPLY');
        const usage = String(keyFigures.usage || 'Capacity Usage');
        const transport = resourceType === 'transportation';
        const production = resourceType === 'production';
        const columns = transport
          ? ['Product', 'Location', 'Ship From', 'Mode', 'Period', supply, usage, 'Utilization', 'Status']
          : production
            ? ['Resource', 'Location', 'Source', 'Period', supply, usage, 'Utilization', 'Status']
            : ['Resource', 'Location', 'Product', 'Period', supply, usage, 'Utilization', 'Status'];
        const normalized = rows.map((row) => ({
          [transport ? 'Product' : 'Resource']: transport ? row.product ?? '-' : row.resource ?? '-',
          Location: row.location ?? '-',
          ...(transport ? { 'Ship From': row.ship_from_location ?? '-', Mode: row.mode_of_transport ?? '-' } : {}),
          ...(production ? { Source: row.source ?? '-' } : {}),
          ...(transport || production ? {} : { Product: row.product ?? '-' }),
          Period: row.period ?? '-',
          [supply]: row.supply ?? '-',
          [usage]: row.usage ?? '-',
          Utilization: row.utilization_pct == null ? 'N/A' : `${formatValue(row.utilization_pct)}%`,
          Status: row.status ?? '-',
        }));
        return (
          <section className="evidence-section" key={resourceType}>
            <div className="section-heading">
              <div><span className="section-kicker">{String(table.title || resourceType)}</span><h3>Capacity detail</h3></div>
              <span className="row-count">{rows.length} rows</span>
            </div>
            <DataTable rows={normalized} columns={columns} />
          </section>
        );
      })}
    </>
  );
}

function Evidence({ data }: { data: DataRecord }) {
  const results = asRecords(data.results);
  const alerts = asRecords(data.alert_results);
  const anomalies = asRecords(data.anomalies);

  if (data.key_figures || data.resource_type && (data.total_rows_evaluated !== undefined || data.tables)) {
    return <CapacityEvidence data={data} />;
  }

  if (results.length) {
    const metric = String(data.metric || 'value');
    const chartRows = results.map((row, index) => ({
      label: String(row.product ?? row.period ?? row.resource ?? row.location ?? `Row ${index + 1}`),
      forecast: Number(row.forecast ?? row[metric] ?? row.actual ?? 0) || 0,
      actual: Number(row.actual ?? 0) || 0,
    }));
    const values = chartRows.map((row) => row.forecast);
    const columns = Object.keys(results[0]).slice(0, 7);
    return (
      <>
        <MetricStrip items={[
          { label: 'Rows evaluated', value: data.total_rows_evaluated ?? results.length },
          { label: `Top ${metric}`, value: Math.max(...values, 0) },
          { label: 'Results shown', value: results.length },
        ]} />
        <section className="evidence-section">
          <div className="section-heading"><div><span className="section-kicker">Comparison</span><h3>{metric} by {Array.isArray(data.group_by) ? data.group_by.join(' / ') : 'result'}</h3></div></div>
          <div className="chart-frame">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartRows} margin={{ top: 12, right: 8, left: 0, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke="#e7ebe7" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: '#6d7974', fontSize: 11 }} />
                <YAxis tickLine={false} axisLine={false} tick={{ fill: '#6d7974', fontSize: 11 }} />
                <Tooltip cursor={{ fill: '#f1f5f1' }} />
                <Bar dataKey="forecast" name={metric} fill="#146a57" radius={[3, 3, 0, 0]} />
                {chartRows.some((row) => row.actual !== 0) && <Bar dataKey="actual" name="Actual" fill="#e2765b" radius={[3, 3, 0, 0]} />}
              </BarChart>
            </ResponsiveContainer>
          </div>
          <DataTable rows={results} columns={columns} />
        </section>
      </>
    );
  }

  if (alerts.length || anomalies.length) {
    const rows = alerts.length ? alerts : anomalies;
    return (
      <>
        <MetricStrip items={[
          { label: 'Rows checked', value: data.count ?? rows.length },
          { label: 'Alerts', value: data.alert_count ?? data.anomaly_count ?? rows.length, tone: 'alert' },
          { label: 'Scope', value: data.threshold_pct != null ? `${formatValue(data.threshold_pct)}% threshold` : data.result_scope ?? 'Analysis results' },
        ]} />
        <section className="evidence-section">
          <div className="section-heading"><div><span className="section-kicker">Attention required</span><h3>Flagged results</h3></div></div>
          <DataTable rows={rows} />
        </section>
      </>
    );
  }

  if (typeof data.ready === 'boolean') {
    const missingPeriods = Array.isArray(data.missing_periods) ? data.missing_periods : [];
    return (
      <>
        <MetricStrip items={[
          { label: 'Data ready', value: data.ready ? 'Yes' : 'No', tone: data.ready ? '' : 'alert' },
          { label: 'Last loaded', value: data.last_loaded_period },
          { label: 'Missing periods', value: missingPeriods.length, tone: missingPeriods.length ? 'alert' : '' },
        ]} />
        {missingPeriods.length > 0 && <section className="evidence-section"><div className="section-heading"><div><span className="section-kicker">Data quality</span><h3>Missing periods</h3></div></div><p className="period-list">{missingPeriods.map(String).join(', ')}</p></section>}
      </>
    );
  }

  return <div className="no-evidence"><Database size={18} /><span>This response did not include structured evidence.</span></div>;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Recently';
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>(readConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [connection, setConnection] = useState<'connected' | 'running' | 'offline'>('connected');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [error, setError] = useState('');
  const messageListRef = useRef<HTMLDivElement>(null);
  const activeConversation = conversations.find((conversation) => conversation.id === activeId);
  const messages = activeConversation?.messages || EMPTY_MESSAGES;
  const dashboardData = latestInsight(messages);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    } catch {
      setError('Conversation history could not be saved in this browser.');
    }
  }, [conversations]);

  useEffect(() => {
    if (messageListRef.current) messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
  }, [messages.length, loading]);

  function saveConversation(conversation: Conversation) {
    setConversations((current) => upsertConversation(current, conversation));
  }

  function startNewConversation() {
    setActiveId(null);
    setQuestion('');
    setError('');
    setHistoryOpen(false);
  }

  async function submitQuestion(value: string) {
    const message = value.trim();
    if (!message || loading) return;
    setQuestion('');
    setError('');
    setLoading(true);
    setConnection('running');

    const existing = activeConversation;
    const conversationId = existing?.id || crypto.randomUUID();
    const now = new Date().toISOString();
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: message };
    const optimistic: Conversation = existing
      ? { ...existing, updatedAt: now, messages: [...existing.messages, userMessage] }
      : { id: conversationId, title: getTitle(message), updatedAt: now, messages: [userMessage] };

    setActiveId(conversationId);
    saveConversation(optimistic);

    try {
      const response = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, conversation_id: existing?.conversationId || null }),
      });
      if (!response.ok) throw new Error((await response.text()) || `Request failed (${response.status})`);
      const result = (await response.json()) as AskResponse;
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: result.reply || 'The agent returned no text.',
        insights: Array.isArray(result.insights) ? result.insights : [],
      };
      saveConversation({
        ...optimistic,
        conversationId: result.conversation_id,
        updatedAt: new Date().toISOString(),
        messages: [...optimistic.messages, assistantMessage],
      });
      setConnection('connected');
    } catch (requestError) {
      const detail = requestError instanceof Error ? requestError.message : 'Unexpected request error';
      setError(detail);
      saveConversation({
        ...optimistic,
        updatedAt: new Date().toISOString(),
        messages: [...optimistic.messages, {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: `I couldn't complete that analysis. ${detail}`,
        }],
      });
      setConnection('offline');
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitQuestion(question);
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion(question);
    }
  }

  return (
    <div className={`app-frame ${historyOpen ? 'history-open' : ''}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <button className="icon-button mobile-history-toggle" aria-label="Toggle conversation history" onClick={() => setHistoryOpen(!historyOpen)}>
            {historyOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
          <div className="brand-mark"><Activity size={19} strokeWidth={2.2} /></div>
          <div className="brand-copy"><strong>Demand Desk</strong><span>IBP / Planning workspace</span></div>
        </div>
        <div className="topbar-right">
          <span className={`connection-state ${connection}`}><i />{connection === 'running' ? 'Analyzing' : connection === 'offline' ? 'Agent unavailable' : 'Agent connected'}</span>
          <button className="icon-button new-chat-top" aria-label="Start a new conversation" title="New conversation" onClick={startNewConversation}><MessageSquarePlus size={17} /></button>
        </div>
      </header>

      <div className="workspace-grid">
        <aside className="history-rail" aria-label="Conversation history">
          <div className="rail-heading"><span>Workspace</span><button className="rail-collapse" aria-label="Close history" onClick={() => setHistoryOpen(false)}><PanelLeftClose size={16} /></button></div>
          <button className="new-conversation" onClick={startNewConversation}><MessageSquarePlus size={16} /><span>New conversation</span><span className="shortcut">N</span></button>
          <div className="history-label"><span>Recent conversations</span><ChevronDown size={14} /></div>
          <div className="history-list">
            {conversations.length === 0 ? (
              <div className="history-empty"><Clock3 size={16} /><span>Your conversations will appear here.</span></div>
            ) : conversations.map((conversation) => (
              <button
                className={`history-entry ${conversation.id === activeId ? 'selected' : ''}`}
                key={conversation.id}
                onClick={() => { setActiveId(conversation.id); setError(''); setHistoryOpen(false); }}
                disabled={loading}
              >
                <span className="history-entry-title">{conversation.title}</span>
                <span className="history-entry-meta">{conversation.messages.length} messages <i /> {formatDate(conversation.updatedAt)}</span>
              </button>
            ))}
          </div>
          <div className="rail-footer"><span className="workspace-dot" /><span>Planning workspace</span><ArrowUpRight size={14} /></div>
        </aside>

        <main className="analysis-workspace">
          <div className="workspace-heading">
            <div>
              <div className="eyebrow"><span className="eyebrow-mark" /> Planning workspace</div>
              <h1>{activeConversation ? activeConversation.title : 'Decision signals'}</h1>
              <p>{activeConversation ? 'Evidence and analysis from this conversation.' : 'Forecast signals, exceptions, and readiness at a glance.'}</p>
            </div>
            <div className="updated-stamp"><span>DATA VIEW</span><strong>{activeConversation ? formatDate(activeConversation.updatedAt) : 'Awaiting analysis'}</strong></div>
          </div>

          <section className="dashboard" aria-live="polite">
            <div className="dashboard-heading">
              <div className="dashboard-title"><span className="dashboard-icon"><Activity size={17} /></span><div><span className="section-kicker">Analysis output</span><h2>Evidence board</h2></div></div>
              {dashboardData && <span className="evidence-badge"><Check size={13} /> Structured evidence</span>}
            </div>
            {dashboardData ? (
              <Evidence data={dashboardData} />
            ) : (
              <div className="dashboard-empty">
                <div className="empty-graphic"><Activity size={23} /><span /><span /><span /></div>
                <div><h3>{activeConversation ? 'No evidence in this conversation' : 'Your planning signals will appear here'}</h3><p>Ask the agent a question to populate this board with metrics, comparisons, and detailed results.</p></div>
                {!activeConversation && <button onClick={() => document.getElementById('message-input')?.focus()}>Ask a question <ArrowDown size={14} /></button>}
              </div>
            )}
          </section>

          {activeConversation && messages.length > 0 && (
            <section className="transcript-preview">
              <div className="section-heading"><div><span className="section-kicker">Conversation</span><h3>Latest exchange</h3></div><span className="row-count">{messages.length} messages</span></div>
              <div className="latest-exchange">
                {[...messages].slice(-2).map((item) => (
                  <div className={`exchange-line ${item.role}`} key={item.id}>
                    <span>{item.role === 'user' ? 'YOU' : 'AGENT'}</span>
                    <div>{item.role === 'assistant' ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown> : item.content}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </main>

        <aside className="agent-panel" aria-label="Planning agent chat">
          <div className="agent-header">
            <div className="agent-avatar"><Sparkles size={17} /></div>
            <div className="agent-heading"><strong>Planning agent</strong><span><i /> Ready to analyze</span></div>
            <button className="icon-button panel-toggle" aria-label="Toggle conversation history" title="Conversation list" onClick={() => setHistoryOpen(!historyOpen)}><PanelLeftOpen size={17} /></button>
          </div>
          <div className="chat-context"><span className="context-line" />{activeConversation ? activeConversation.title : 'New conversation'}</div>
          <div className="message-list" ref={messageListRef} aria-live="polite">
            {messages.length === 0 ? (
              <div className="chat-welcome">
                <span className="welcome-glyph"><Sparkles size={19} /></span>
                <h2>Good planning starts with a clear signal.</h2>
                <p>Ask about forecasts, anomalies, data readiness, or capacity constraints.</p>
                <div className="prompt-list">
                  {PROMPTS.map((prompt) => <button key={prompt} onClick={() => void submitQuestion(prompt)} disabled={loading}>{prompt}<ArrowUpRight size={14} /></button>)}
                </div>
              </div>
            ) : messages.map((message) => (
              <article className={`message ${message.role}`} key={message.id}>
                <span className="message-label">{message.role === 'user' ? 'You' : 'Planning agent'}</span>
                {message.role === 'assistant'
                  ? <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>
                  : <p>{message.content}</p>}
                {message.role === 'assistant' && message.insights && message.insights.length > 0 && <span className="message-evidence"><Database size={12} /> Evidence attached</span>}
              </article>
            ))}
            {loading && <div className="thinking"><span /><span /><span /> Analyzing planning data</div>}
          </div>
          {error && <div className="request-error" role="alert">{error}</div>}
          <div className="composer-area">
            <form className="composer" onSubmit={handleSubmit}>
              <textarea
                id="message-input"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder="Ask about your plan..."
                rows={2}
                aria-label="Message planning agent"
                disabled={loading}
              />
              <div className="composer-controls"><span>Enter to send <i /> Shift + Enter for a new line</span><button type="submit" aria-label="Send message" disabled={!question.trim() || loading}><Send size={16} /></button></div>
            </form>
            <p className="privacy-note">Analysis is based on available planning data.</p>
          </div>
        </aside>
      </div>
      <button className="mobile-backdrop" aria-label="Close conversation history" onClick={() => setHistoryOpen(false)} />
    </div>
  );
}