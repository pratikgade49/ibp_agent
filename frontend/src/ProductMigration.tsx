import { FormEvent, useState } from 'react';
import { ArrowLeft, Download, FileSpreadsheet, Loader2, Upload } from 'lucide-react';
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

interface ConvertResponse {
  job_id: string;
  summary: Summary;
  human_needed: HumanItem[];
  download_url: string;
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
  const ready = SLOTS.every((slot) => files[slot.key]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || loading) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const body = new FormData();
      SLOTS.forEach((slot) => body.append(slot.key, files[slot.key] as File));
      const response = await fetch('/migration/convert', { method: 'POST', body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `Conversion failed (${response.status}).`);
      setResult(payload as ConvertResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Conversion failed.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="pm-shell">
      <header className="pm-topbar">
        <button type="button" className="pm-back" onClick={onShowAgents}><ArrowLeft size={16} /> All agents</button>
        <div className="pm-brand"><FileSpreadsheet size={18} /><strong>Product Master Migration Agent</strong></div>
        <span />
      </header>

      <section className="pm-layout">
        <h1>Convert Oracle product master to S/4HANA.</h1>
        <p className="pm-lede">Upload the data, mapping and rules. The agent interprets the rules, translates codes, applies the changes and returns an Excel file. You only review what it is not confident about.</p>

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
            <p className="pm-note">The Excel file lists every AI decision with its confidence, plus a change log and per-record issues.</p>
          </section>
        )}
      </section>
    </main>
  );
}
