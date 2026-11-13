import { FormEvent, useState } from 'react';
import { ArrowLeft, CloudUpload, Download, FileSpreadsheet, Loader2, MessageSquareText, Send, Upload } from 'lucide-react';
import './product-migration.css';
import oracleLogo from './assets/oracle-logo.svg';
import sapLogo from './assets/sap-logo.svg';
import GenieMascot from './GenieMascot';

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
  correction_applied?: boolean;
  correction_message?: string;
}

interface HumanItem {
  type: string;
  field: string;
  detail: string;
}

interface PreviewIssue {
  field: string;
  rule: string;
  severity: string;
  message: string;
}

interface PreviewRow {
  [key: string]: string | PreviewIssue[] | undefined;
  _row_issues?: PreviewIssue[];
}

interface ConvertResponse {
  job_id: string;
  summary: Summary;
  human_needed: HumanItem[];
  download_url: string;
  preview: PreviewRow[];
}

interface PushItem {
  material: string;
  source_material?: string;
  product_id?: string | null;
  status: string;
  error?: string;
  http_status?: number;
  method?: string;
  endpoint?: string;
  fields?: string[];
  root_http_status?: number;
  root_included_deep_entities?: string[];
  related_results?: { entity: string; status: string; error?: string; http_status?: number; method?: string; endpoint?: string; included_deep_entities?: string[] }[];
}

interface PushResponse {
  status: string;
  created: number;
  partial: number;
  already_exists: number;
  skipped_with_errors: number;
  failed: number;
  results: PushItem[];
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
  { key: 'mapping', label: 'Mapping', hint: 'Oracle to SAP field mapping (.xlsx)' },
  { key: 'rules', label: 'Rules', hint: 'Validation and derivation rules (.xlsx)' },
] as const;

type SlotKey = (typeof SLOTS)[number]['key'];

export default function ProductMigration({ onShowAgents }: ProductMigrationProps) {
  const [files, setFiles] = useState<Partial<Record<SlotKey, File>>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<ConvertResponse | null>(null);
  const [pushResult, setPushResult] = useState<PushResponse | null>(null);
  const [pushing, setPushing] = useState(false);
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
      setPushResult(null);
      setMessages((current) => [...current, {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: correctionText
          ? (parsed.summary.correction_message || (parsed.summary.correction_applied ? 'Correction applied. Updated workbook is ready.' : 'No correction was applied.'))
          : 'Conversion finished. Review the preview and download the Excel file.',
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

  async function pushToS4() {
    if (!result || pushing || result.summary.ready === 0) return;
    const confirmed = window.confirm(
      `Create ${result.summary.ready} ready product(s) in S/4HANA? SAP will generate each Product number; source MATNR will not be used as the S/4 Product key. Retrying can create duplicates.`,
    );
    if (!confirmed) return;
    setPushing(true);
    setPushResult(null);
    setError('');
    try {
      const response = await fetch(`/migration/push/${result.job_id}`, {
        method: 'POST',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `S/4 upload failed (${response.status}).`);
      setPushResult(payload as PushResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'S/4 upload failed.');
    } finally {
      setPushing(false);
    }
  }

  const columns = result?.preview?.[0] ? Object.keys(result.preview[0]).filter((column) => column !== '_row_issues') : [];

  return (
    <main className="pm-shell">
      <header className="pm-topbar">
        <button type="button" className="pm-back" onClick={onShowAgents}><ArrowLeft size={16} /> All agents</button>
        <div className="pm-brand"><FileSpreadsheet size={18} /><strong>Oracle to SAP-S4HANA Migration</strong></div>
        <span />
      </header>

      <section className="pm-layout">
        <div className="pm-hero">
          <div className="pm-hero-copy">
            <span className="pm-eyebrow">Oracle to SAP-S4HANA Migration</span>
            <h1>Oracle to SAP-S4HANA Masterdata Migration Workbench</h1>
            <p>transform your Master Data from Legacy Oracle DB to SAP S/4HANA with automated AI Infused Mapping and rule based Validation</p>
          </div>
          <div className="pm-flow" aria-label="Oracle product data flows through the migration agent to SAP S/4HANA">
            <div className="pm-flow-brand"><img src={oracleLogo} alt="Oracle" /><span></span></div>
            <div className="pm-flow-line"><i /><i /><i /></div>
            <div className="pm-flow-agent"><FileSpreadsheet size={25} /><span>AI AGENT</span></div>
            <div className="pm-flow-line"><i /><i /><i /></div>
            <div className="pm-flow-brand pm-flow-sap"><img src={sapLogo} alt="SAP" /><strong>S/4HANA</strong><span></span></div>
          </div>
        </div>

        <section className="pm-how" aria-label="How migration works">
          <div className="pm-section-title"><div><span className="pm-eyebrow">THE JOURNEY</span><h2>How your migration works</h2></div><p>Clear steps. You stay in control.</p></div>
          <ol className="pm-steps">
            <li><span>01</span><FileSpreadsheet size={20} /><strong>Provide inputs</strong><p>Upload Oracle data, field mapping and rules as Excel files.</p></li>
            <li><span>02</span><MessageSquareText size={20} /><strong>Interpret & translate</strong><p>The agent applies the mapping and interprets the supplied rules.</p></li>
            <li><span>03</span><CloudUpload size={20} /><strong>Validate & review</strong><p>Review converted records, warnings and decisions needing your input.</p></li>
            <li><span>04</span><Download size={20} /><strong>Download or create</strong><p>Download the workbook or choose to create ready products in S/4HANA.</p></li>
          </ol>
        </section>
        <div className="pm-input-heading"><div><span className="pm-eyebrow">START A MIGRATION</span><h2>Prepare your files</h2><p>Upload the three Excel files to begin. S/4 fields are matched against live service metadata.</p></div><span className="pm-input-count">{SLOTS.filter((slot) => files[slot.key]).length} / 3 files added</span></div>
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
            {loading ? <><Loader2 size={16} className="pm-spin" /> Agent is converting your files...</> : 'Run conversion'}
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
                <div className="pm-result-actions">
                  <a className="pm-download" href={result.download_url} download><Download size={16} /> Download Excel</a>
                  <button type="button" className="pm-run pm-push" onClick={() => void pushToS4()} disabled={pushing || !!pushResult || result.summary.ready === 0}>
                    {pushing ? <><Loader2 size={16} className="pm-spin" /> Sending...</> : <><CloudUpload size={16} /> Create ready products in S/4</>}
                  </button>
                </div>

                {pushResult && (
                  <div className="pm-push-result" role="status">
                    <strong>S/4 upload: {pushResult.created} created, {pushResult.partial} partially created, {pushResult.skipped_with_errors} skipped for conversion errors, {pushResult.failed} failed.</strong>
                    <ul>{pushResult.results.map((item, index) => (
                      <li key={`${item.source_material || item.material}-${index}`}>
                        <strong>Source MATNR: {item.source_material || item.material}</strong>
                        {item.product_id && <> → S/4 Product: <code>{item.product_id}</code></>}
                        <ul>
                          <li>
                            Main product: {item.status === 'created' || item.status === 'created_with_errors' ? 'created' : item.status.replace(/_/g, ' ')}
                            {item.root_http_status ? ` (HTTP ${item.root_http_status})` : item.http_status ? ` (HTTP ${item.http_status})` : ''}
                            {item.error ? `: ${item.error}` : ''}
                            {item.root_included_deep_entities?.length ? `; included in root request: ${item.root_included_deep_entities.join(', ')}` : ''}
                          </li>
                          {item.related_results?.map((related, childIndex) => (
                            <li key={`${related.entity}-${childIndex}`}>
                              {related.entity}: {related.status}{related.http_status ? ` (HTTP ${related.http_status})` : ''}
                              {related.error ? `: ${related.error}` : ''}
                              {related.included_deep_entities?.length ? `; included in this request: ${related.included_deep_entities.join(', ')}` : ''}
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}</ul>
                    <p>Included nested entities share the parent request result; their individual status is not separately confirmed.</p>
                  </div>
                )}

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
                          <tr>{columns.map((column) => <th key={column}>{column}</th>)}<th>Issues</th></tr>
                        </thead>
                        <tbody>
                          {result.preview.map((row, index) => (
                            <tr
                              key={`${index}-${String(row.MATNR ?? index)}`}
                              className={row._row_issues?.some((issue) => issue.severity === 'ERROR')
                                ? 'pm-row-error'
                                : row._row_issues?.some((issue) => issue.severity === 'WARN') ? 'pm-row-warning' : undefined}
                            >
                              {columns.map((column) => <td key={`${column}-${index}`}>{typeof row[column] === 'string' ? row[column] : ''}</td>)}
                              <td className="pm-issues-cell">
                                {row._row_issues?.length ? (
                                  <ul>{row._row_issues.map((issue, issueIndex) => (
                                    <li key={`${issue.field}-${issue.rule}-${issueIndex}`}>
                                      <strong className={`pm-issue-${issue.severity.toLowerCase()}`}>{issue.severity}</strong>{' '}
                                      <code>{issue.field}</code>: {issue.message}
                                    </li>
                                  ))}</ul>
                                ) : <span className="pm-no-issues">None</span>}
                              </td>
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
                <div className="genie-composer-row"><GenieMascot /><div className="pm-chat-composer">
                  <textarea
                    value={correction}
                    onChange={(e) => setCorrection(e.target.value)}
                    placeholder="Example: set BESKZ to E or set DISMM to PD"
                    rows={3}
                  />
                  <button type="button" className="pm-run" onClick={() => void applyCorrection()} disabled={loading || !correction.trim()}>
                    <Send size={15} /> Apply
                  </button>
                </div></div>
              </aside>
            </div>
          </section>
        )}
      </section>
    </main>
  );
}
