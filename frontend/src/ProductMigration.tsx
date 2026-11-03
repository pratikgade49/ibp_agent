import { FormEvent, useState } from 'react';
import { ArrowLeft, Download, FileSpreadsheet, Loader2, MessageSquareText, Send, Upload } from 'lucide-react';
import './product-migration.css';

interface Summary {
  records: number;
  ready: number;
  with_errors: number;
  changes_applied: number;
  issues: number;
  rules_compiled_by_ai: number;
  rules_from_cache: number;
  human_decisions_needed: number;
  warnings: string[];
}

interface HumanItem {
  type: string;
  field: string;
  detail: string;
}

interface PreviewRow {
  [key: string]: string;
}

interface ConvertResponse {
  job_id: string;
  summary: Summary;
  human_needed: HumanItem[];
  download_url: string;
  preview: PreviewRow[];
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

interface ProductMigrationProps {
  onShowAgents: () => void;
}

const SLOTS = [
  { key: 'data', label: 'Data', hint: 'Oracle product master (.xlsx)' },
  { key: 'mapping', label: 'Mapping', hint: 'Oracle to S/4 field mapping (.xlsx)' },
  { key: 'rules', label: 'Rules', hint: 'Validation and derivation rules (.xlsx)' },
] as const;

type SlotKey = (typeof SLOTS)[number]['key'];

export default function ProductMigration({ onShowAgents }: ProductMigrationProps) {
  const [files, setFiles] = useState<Partial<Record<SlotKey, File>>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<ConvertResponse | null>(null);
  const [correction, setCorrection] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 'welcome', role: 'assistant', text: 'Upload the input files and I will convert them. If you want to override warnings, type a correction like “set BESKZ to E”.' },
  ]);
  const ready = SLOTS.every((slot) => files[slot.key]);

  function buildBody(extra?: { key: string; value: string | File }) {
    const body = new FormData();
    SLOTS.forEach((slot) => body.append(slot.key, files[slot.key] as File));
    if (extra) body.append(extra.key, extra.value);
    return body;
  }

  async function runConversion(correctionText?: string) {
    if (!ready || loading) return;
    setLoading(true);
    setError('');
    const requestBody = buildBody(correctionText ? { key: 'correction', value: correctionText } : undefined);
    try {
      const response = await fetch('/migration/convert', { method: 'POST', body: requestBody });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `Conversion failed (${response.status}).`);
      const parsed = payload as ConvertResponse;
      setResult(parsed);
      setMessages((current) => [...current, {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: correctionText ? `Applied: “${correctionText}”. Updated workbook is ready.` : 'Conversion finished. Review the preview and download the Excel file.',
      }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Conversion failed.');
    } finally {
      setLoading(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || loading) return;
    setResult(null);
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', text: 'Run migration with the selected files.' }]);
    await runConversion();
  }

  async function applyCorrection() {
    const value = correction.trim();
    if (!value || !ready || loading) return;
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', text: value }]);
    setCorrection('');
    await runConversion(value);
  }

  const columns = result?.preview?.[0] ? Object.keys(result.preview[0]) : [];

  return (
    <main className="pm-shell">
      <header className="pm-topbar">
        <button type="button" className="pm-back" onClick={onShowAgents}><ArrowLeft size={16} /> All agents</button>
        <div className="pm-brand"><FileSpreadsheet size={18} /><strong>Product Master Migration Agent</strong></div>
        <span />
      </header>

      <section className="pm-layout">
        <h1>Convert Oracle product master to S/4HANA.</h1>
        <p className="pm-lede">Upload the data, mapping and rules. After the workbook is generated, you can correct warnings in the chat and regenerate the updated Excel immediately.</p>

        <form className="pm-form" onSubmit={submit}>
          {SLOTS.map((slot) => (
            <label className={`pm-drop ${files[slot.key] ? 'filled' : ''}`} key={slot.key}>
              <Upload size={18} />
              <span><strong>{slot.label}</strong><small>{files[slot.key]?.name || slot.hint}</small></span>
              <input
                type="file"
                accept=".xlsx"
                onChange={(e) => setFiles((current) => ({ ...current, [slot.key]: e.target.files?.[0] }))}
              />
            </label>
          ))}
          <button type="submit" className="pm-run" disabled={!ready || loading}>
            {loading ? <><Loader2 size={16} className="pm-spin" /> Converting...</> : 'Run conversion'}
          </button>
        </form>

        {error && <div className="pm-error" role="alert">{error}</div>}

        {result && (
          <section className="pm-result" aria-label="Conversion result">
            <div className="pm-stats">
              <div><b>{result.summary.records}</b><span>Records</span></div>
              <div><b>{result.summary.ready}</b><span>Ready to load</span></div>
              <div className={result.summary.with_errors ? 'warn' : ''}><b>{result.summary.with_errors}</b><span>With errors</span></div>
              <div><b>{result.summary.changes_applied}</b><span>Changes applied</span></div>
            </div>

            <div className="pm-content-grid">
              <div className="pm-panel">
                <a className="pm-download" href={result.download_url} download><Download size={16} /> Download Excel</a>

                {result.human_needed.length > 0 ? (
                  <div className="pm-human">
                    <h2>Needs your decision ({result.human_needed.length})</h2>
                    <ul>{result.human_needed.map((item, index) => (
                      <li key={`${item.field}-${index}`}><code>{item.field}</code> <em>{item.type.replace('_', ' ')}</em> {item.detail}</li>
                    ))}</ul>
                  </div>
                ) : <p className="pm-ok">The agent resolved everything on its own.</p>}

                {result.summary.warnings.length > 0 && (
                  <div className="pm-human"><h2>Input warnings</h2><ul>{result.summary.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
                )}

                <div className="pm-preview">
                  <div className="pm-preview-header"><MessageSquareText size={16} /> Excel preview</div>
                  {result.preview?.length ? (
                    <div className="pm-table-wrap">
                      <table>
                        <thead>
                          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
                        </thead>
                        <tbody>
                          {result.preview.map((row, index) => (
                            <tr key={`${index}-${Object.values(row).join('-')}`}>
                              {columns.map((column) => <td key={`${column}-${index}`}>{row[column] ?? ''}</td>)}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : <p className="pm-note">Preview is not available for this result.</p>}
                </div>
              </div>

              <aside className="pm-chat-panel">
                <div className="pm-chat-header">Correction chat</div>
                <div className="pm-chat-body">
                  {messages.map((message) => (
                    <div key={message.id} className={`pm-chat-message ${message.role}`}>
                      <span>{message.role === 'user' ? 'You' : 'Agent'}</span>
                      <p>{message.text}</p>
                    </div>
                  ))}
                </div>
                <div className="pm-chat-composer">
                  <textarea
                    value={correction}
                    onChange={(e) => setCorrection(e.target.value)}
                    placeholder="Example: set BESKZ to E or set DISMM to PD"
                    rows={3}
                  />
                  <button type="button" className="pm-run" onClick={() => void applyCorrection()} disabled={loading || !correction.trim()}>
                    <Send size={15} /> Apply
                  </button>
                </div>
              </aside>
            </div>
          </section>
        )}
      </section>
    </main>
  );
}
