import { useState } from 'react';
import { ChevronLeft, ChevronRight, Filter, RotateCcw } from 'lucide-react';
import type { ChatMessage, DataRecord } from './types';

const DIMENSIONS = [
  { key: 'PRDFAMILY', label: 'Product family' },
  { key: 'CATEGORY', label: 'Category' },
  { key: 'CUSTCOUNTRY', label: 'Country' },
  { key: 'PATIENTTYPE', label: 'Patient type' },
] as const;
const PAGE_SIZE = 25;

interface ForecastRow extends DataRecord {
  period: string;
  customer_base_forecast: number;
}

function asRecord(value: unknown): DataRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as DataRecord
    : null;
}

function latestPreparedForecast(messages: ChatMessage[]): DataRecord | null {
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const insights = messages[messageIndex].insights || [];
    for (let insightIndex = insights.length - 1; insightIndex >= 0; insightIndex -= 1) {
      const insight = insights[insightIndex];
      if (insight.tool !== 'prepare_customer_base_forecast') continue;
      const data = asRecord(insight.data);
      if (data?.status === 'prepared' && Array.isArray(data.forecast_rows)) return data;
    }
  }
  return null;
}

function formatQuantity(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? numeric.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })
    : '—';
}

export default function ScenarioForecastPreview({ messages }: { messages: ChatMessage[] }) {
  const prepared = latestPreparedForecast(messages);
  const rawRows = Array.isArray(prepared?.forecast_rows) ? prepared.forecast_rows : [];
  const rows = rawRows
    .map(asRecord)
    .filter((row): row is DataRecord => row !== null)
    .filter((row): row is ForecastRow =>
      typeof row.period === 'string' && Number.isFinite(Number(row.customer_base_forecast)),
    );
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(0);

  if (!prepared || rows.length === 0) return null;

  const periods = [...new Set(rows.map((row) => row.period))].sort();
  const options = Object.fromEntries(DIMENSIONS.map(({ key }) => [
    key,
    [...new Set(rows.map((row) => String(row[key] ?? '')))].filter(Boolean).sort(),
  ])) as Record<(typeof DIMENSIONS)[number]['key'], string[]>;

  const filteredRows = rows.filter((row) =>
    DIMENSIONS.every(({ key }) => !filters[key] || String(row[key] ?? '') === filters[key]),
  );
  const grouped = new Map<string, { dimensions: string[]; values: Map<string, number> }>();
  for (const row of filteredRows) {
    const dimensionValues = DIMENSIONS.map(({ key }) => String(row[key] ?? ''));
    const groupKey = JSON.stringify(dimensionValues);
    let group = grouped.get(groupKey);
    if (!group) {
      group = { dimensions: dimensionValues, values: new Map() };
      grouped.set(groupKey, group);
    }
    group.values.set(row.period, Number(row.customer_base_forecast));
  }

  const matrixRows = [...grouped.values()].sort((left, right) =>
    left.dimensions.join('\u0000').localeCompare(right.dimensions.join('\u0000')),
  );
  const pageCount = Math.max(1, Math.ceil(matrixRows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const visibleRows = matrixRows.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  const warnings = Array.isArray(prepared.warnings)
    ? prepared.warnings.filter((warning): warning is string => typeof warning === 'string')
    : [];

  function updateFilter(key: string, value: string) {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(0);
  }

  function resetFilters() {
    setFilters({});
    setPage(0);
  }

  return (
    <section className="scenario-preview" aria-label="Prepared customer base forecast preview">
      <header className="scenario-preview-header">
        <div>
          <span className="scenario-kicker"><i /> FORECAST PREVIEW</span>
          <h2>Customer base forecast</h2>
          <p>
            {String(prepared.planning_version_id || 'Baseline')} · Scenario {String(prepared.scenario_id)}
            {' · '}{Number(prepared.row_count ?? rows.length).toLocaleString()} prepared period rows
          </p>
        </div>
        <span className="scenario-preview-state">Not imported</span>
      </header>

      <div className="scenario-preview-filters" aria-label="Filter forecast rows">
        <span className="scenario-filter-title"><Filter size={14} /> Filter by</span>
        {DIMENSIONS.map(({ key, label }) => (
          <label className="scenario-filter" key={key}>
            <span>{label}</span>
            <select value={filters[key] || ''} onChange={(event) => updateFilter(key, event.target.value)}>
              <option value="">All {label.toLowerCase()}s</option>
              {options[key].map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
        ))}
        <button className="scenario-reset-filters" type="button" onClick={resetFilters} disabled={!Object.values(filters).some(Boolean)} title="Clear all filters" aria-label="Clear all filters">
          <RotateCcw size={14} /><span>Reset</span>
        </button>
      </div>

      <div className="scenario-preview-table-wrap">
        <table className="scenario-preview-table">
          <thead>
            <tr>
              {DIMENSIONS.map(({ key, label }) => <th key={key}>{label}</th>)}
              {periods.map((period) => <th className="scenario-period-heading" key={period}>{period}</th>)}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={JSON.stringify(row.dimensions)}>
                {row.dimensions.map((value, index) => <td key={DIMENSIONS[index].key}>{value || '—'}</td>)}
                {periods.map((period) => <td className="scenario-quantity" key={period}>{formatQuantity(row.values.get(period))}</td>)}
              </tr>
            ))}
            {visibleRows.length === 0 && <tr><td className="scenario-no-results" colSpan={DIMENSIONS.length + periods.length}>No rows match these filters.</td></tr>}
          </tbody>
        </table>
      </div>

      <footer className="scenario-preview-footer">
        <span>Showing {matrixRows.length ? currentPage * PAGE_SIZE + 1 : 0}–{Math.min((currentPage + 1) * PAGE_SIZE, matrixRows.length)} of {matrixRows.length.toLocaleString()} dimension combinations</span>
        <div className="scenario-pagination">
          <button type="button" onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={currentPage === 0} aria-label="Previous page" title="Previous page"><ChevronLeft size={16} /></button>
          <span>Page {currentPage + 1} of {pageCount}</span>
          <button type="button" onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))} disabled={currentPage >= pageCount - 1} aria-label="Next page" title="Next page"><ChevronRight size={16} /></button>
        </div>
      </footer>
      {warnings.length > 0 && (
        <details className="scenario-preview-warnings">
          <summary>{warnings.length.toLocaleString()} validation warnings</summary>
          <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </details>
      )}
    </section>
  );
}