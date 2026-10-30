import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import yashLogo from './assets/Yash logo.jpg';
import jouleLogo from './assets/sap-joule-logo.svg';
import {
  Activity,
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  Clock3,
  Database,
  Factory,
  Layers3,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Send,
  ShieldCheck,
  Sparkles,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { AskResponse, ChatMessage, Conversation, DataRecord, Insight } from './types';
import SalesAssurance from './SalesAssurance';
import ScenarioTestAgent from './ScenarioTestAgent';

const STORAGE_KEY = 'ibp-demand-desk-conversations-v1';
const EMPTY_MESSAGES: ChatMessage[] = [];
const PROMPTS = [
  'Which products have the highest forecast?',
  'Show forecast anomalies',
  'Is sales history ready?',
  'Which resources are bottlenecks in the next three periods?',
];

const AGENTS: Array<{
  id: 'ibp' | 's4' | 'sales' | 'maintenance' | 'scenario';
  index: string;
  name: string;
  descriptor: string;
  description: string;
  capabilities: string[];
  icon: LucideIcon;
  tone: string;
}> = [
  {
    id: 'ibp',
    index: '01',
    name: 'IBP Demand Planning',
    descriptor: 'Forecast intelligence',
    description: 'Explore demand signals, forecast quality, data readiness, and planning exceptions across your business.',
    capabilities: ['Forecast analysis', 'Anomaly detection', 'Master data health'],
    icon: Activity,
    tone: 'blue',
  },
  {
    id: 'scenario',
    index: '02',
    name: 'IBP Scenario Test Agent',
    descriptor: 'Customer base forecast',
    description: 'Prepare and review customer-base forecasts against a selected IBP scenario before import.',
    capabilities: ['Scenario-specific inputs', 'Forecast preview', 'Approval-gated import'],
    icon: Layers3,
    tone: 'teal',
  },
  {
    id: 's4',
    index: '03',
    name: 'S/4 Capacity Reader',
    descriptor: 'Capacity & constraints',
    description: 'Read work-center setup, evaluate daily capacity, and identify overloaded resources using SAP data.',
    capabilities: ['Daily load evaluation', 'Work-center shifts', 'Bottleneck evidence'],
    icon: Factory,
    tone: 'teal',
  },
  {
    id: 'maintenance',
    index: '03',
    name: 'S/4 Maintenance Planner',
    descriptor: 'Maintenance windows',
    description: 'Inspect scheduled maintenance calls and the production work centers they affect.',
    capabilities: ['Scheduled calls', 'Work-center links', 'Order intervals'],
    icon: Wrench,
    tone: 'maintenance',
  },
  {
    id: 'sales',
    index: '04',
    name: 'Commercial Assurance',
    descriptor: 'Order risk decisions',
    description: 'Understand delivery exposure, compare supply options, and prepare commercial decisions for review.',
    capabilities: ['Order exposure', 'Supply alternatives', 'Decision support'],
    icon: ShieldCheck,
    tone: 'coral',
  },
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
            agentMode: 'ibp',
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
        agentMode: 'ibp',
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
      (data.master_data_type && Array.isArray(data.results) ||
        Array.isArray(data.results) ||
        Array.isArray(data.comparison_results) ||
        Array.isArray(data.alert_results) ||
        Array.isArray(data.anomalies) ||
        data.evidence_type === 's4_capacity_bottleneck' ||
        data.evidence_type === 's4_maintenance_schedule' ||
        Array.isArray(data.capacity_evaluations) ||
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

function HealthCheckEvidence({ data }: { data: DataRecord }) {
  const results = asRecords(data.results);
  const errorCount = Number(data.error_count ?? 0);
  const warningCount = Number(data.warning_count ?? 0);
  const rows = results.map((result) => ({
    Severity: String(result.severity || 'warning'),
    Code: String(result.code || 'UNKNOWN'),
    Message: String(result.message || ''),
    Record: JSON.stringify(result.record || {}),
  }));

  return (
    <>
      <MetricStrip items={[
        { label: 'Rows evaluated', value: data.total_rows_evaluated ?? results.length },
        { label: 'Errors', value: errorCount, tone: errorCount ? 'alert' : '' },
        { label: 'Warnings', value: warningCount, tone: warningCount ? 'warning' : '' },
        { label: 'Source', value: data.source || 'unknown' },
      ]} />
      <section className="evidence-section">
        <div className="section-heading">
          <div><span className="section-kicker">Master data quality</span><h3>Health check findings</h3></div>
          <span className="row-count">{results.length} findings</span>
        </div>
        <DataTable rows={rows} columns={['Severity', 'Code', 'Message', 'Record']} />
      </section>
    </>
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

function S4CapacityBottleneckEvidence({ data }: { data: DataRecord }) {
  const recoverySystems = Array.isArray(data.recovery_systems)
    ? data.recovery_systems.join(' + ')
    : 'N/A';
  const rows = [
    { Signal: 'Work center', Value: data.work_center },
    { Signal: 'Shipment', Value: data.shipment },
    { Signal: 'Capacity gap', Value: `${formatValue(data.overload_hours)} h` },
    { Signal: 'Vessel cut-off impact', Value: `${formatValue(data.delivery_delay_hours)} h delay` },
    { Signal: 'Recovery path', Value: recoverySystems },
    ...(data.alternate_work_center ? [{ Signal: 'Alternate work center', Value: data.alternate_work_center }] : []),
    ...(data.recommended_action ? [{ Signal: 'Recommended action', Value: data.recommended_action }] : []),
    ...(Array.isArray(data.preconditions)
      ? [{ Signal: 'Preconditions', Value: data.preconditions.join('; ') }]
      : []),
    { Signal: 'Resolution status', Value: data.resolution_status || data.approval_status },
  ];

  return (
    <>
      <MetricStrip items={[
        { label: 'Overload', value: `${formatValue(data.overload_hours)} h`, tone: 'alert' },
        { label: 'Cut-off impact', value: `${formatValue(data.delivery_delay_hours)} h delay` },
        { label: 'Recovery path', value: recoverySystems },
        { label: 'Decision', value: data.resolution_status || data.approval_status || 'Pending approval' },
      ]} />
      <section className="evidence-section">
        <div className="section-heading">
          <div>
            <span className="section-kicker">
              {data.assessment_stage === 'resolution'
                ? 'SAP PP resolution'
                : data.assessment_stage === 'recovery_plan'
                  ? 'Proposed SAP PP recovery plan'
                  : 'Capacity recovery assessment'}
            </span>
            <h3>CNC-04 bottleneck resolution</h3>
          </div>
          <span className="row-count">{String(data.resolution_status || data.approval_status || 'Pending approval')}</span>
        </div>
        <DataTable rows={rows} columns={['Signal', 'Value']} />
      </section>
    </>
  );
}

function S4CapacityEvidence({ data }: { data: DataRecord }) {
  const evaluations = asRecords(data.capacity_evaluations);
  const overloaded = evaluations.filter((row) => row.status === 'overloaded');
  const contributors = asRecords(data.contributors);
  const alternateAssessments = asRecords(data.alternate_assessments);
  const withinCapacityCount = evaluations.filter((row) => row.status === 'within_capacity').length;

  return (
    <>
      <MetricStrip items={[
        { label: 'Capacity checks', value: data.evaluation_count ?? evaluations.length },
        { label: 'Bottlenecks', value: overloaded.length, tone: overloaded.length ? 'alert' : '' },
        { label: 'Within capacity', value: withinCapacityCount },
      ]} />
      {overloaded.length === 0 ? (
        <p className="capacity-empty">No overloaded work centers were found in the returned capacity checks.</p>
      ) : overloaded.map((bucket, index) => {
        const relatedOrders = contributors.filter((order) =>
          String(order.date) === String(bucket.date) &&
          String(order.plant) === String(bucket.plant) &&
          String(order.work_center) === String(bucket.work_center),
        );
        const orderDetails = relatedOrders.map((order) => {
          const assessment = alternateAssessments.find((item) =>
            String(item.date) === String(order.date) &&
            String(item.plant) === String(order.plant) &&
            String(item.work_center) === String(order.work_center) &&
            String(item.order_id) === String(order.order_id) &&
            String(item.operation) === String(order.operation),
          );
          const alternates = asRecords(assessment?.alternates);
          const suitable = alternates.filter((alternate) => alternate.sufficient_for_operation === true);
          const alternateSummary = suitable.length
            ? suitable.map((alternate) => `${formatValue(alternate.work_center)} (${formatValue(alternate.remaining_after_move)} ${formatValue(order.capacity_unit)} remaining)`).join(', ')
            : !assessment || assessment.status !== 'completed'
              ? 'Could not verify'
              : alternates.length
                ? 'No alternate has enough verified capacity'
                : 'No suitable alternate found';
          return { order, assessment, suitable, alternateSummary };
        });
        const orderRows = orderDetails.map(({ order, alternateSummary }) => ({
          Order: order.order_id ?? '-',
          Operation: order.operation ?? '-',
          Material: order.material ?? '-',
          Status: order.operation_status ?? order.order_status ?? '-',
          'Required capacity': order.required_capacity == null
            ? 'N/A'
            : `${formatValue(order.required_capacity)} ${formatValue(order.capacity_unit)}`,
          'Alternate work center': alternateSummary,
        }));
        const actions = Array.isArray(bucket.recommended_actions)
          ? bucket.recommended_actions.map(String)
          : [];

        return (
          <section className="evidence-section" key={`${String(bucket.date)}-${String(bucket.work_center)}-${index}`}>
            <div className="section-heading">
              <div>
                <span className="section-kicker">{formatValue(bucket.date)}</span>
                <h3>{formatValue(bucket.work_center)} bottleneck</h3>
              </div>
              <span className="row-count">{formatValue(bucket.plant)}</span>
            </div>
            <MetricStrip items={[
              { label: 'Required', value: `${formatValue(bucket.required_capacity)} ${formatValue(bucket.capacity_unit)}` },
              { label: 'Available', value: `${formatValue(bucket.available_capacity)} ${formatValue(bucket.capacity_unit)}` },
              { label: 'Remaining', value: `${formatValue(bucket.remaining_capacity)} ${formatValue(bucket.capacity_unit)}`, tone: 'alert' },
              { label: 'Utilization', value: bucket.utilization_percent == null ? 'N/A' : `${formatValue(bucket.utilization_percent)}%` },
            ]} />
            <h4 className="evidence-subheading">Orders contributing to this bottleneck</h4>
            <DataTable rows={orderRows} columns={['Order', 'Operation', 'Material', 'Status', 'Required capacity', 'Alternate work center']} />
            <div className="recommended-action">
              <h4>Recommended Action</h4>
              {orderDetails.length > 0 && alternateAssessments.length > 0
                ? <ul>{orderDetails.map(({ order, assessment, suitable }) => (
                    <li key={`${String(order.order_id)}-${String(order.operation)}`}>
                      {suitable.length > 0
                        ? `I recommend moving order ${formatValue(order.order_id)}, operation ${formatValue(order.operation)}, to ${suitable.map((alternate) => formatValue(alternate.work_center)).join(' or ')}; capacity fit is verified. Confirm the schedule with the planner before making any change.`
                          : !assessment || assessment.status !== 'completed'
                          ? `I could not verify an alternate for order ${formatValue(order.order_id)}, operation ${formatValue(order.operation)}. Please review the schedule with the planner; no change has been made.`
                          : `I found no suitable alternate with verified capacity for order ${formatValue(order.order_id)}, operation ${formatValue(order.operation)}. Please review the schedule with the planner; no change has been made.`}
                    </li>
                  ))}</ul>
                : <ul>{actions.map((action, actionIndex) => <li key={`${actionIndex}-${action}`}>{action}</li>)}</ul>}
            </div>
          </section>
        );
      })}
    </>
  );
}

function S4MaintenanceEvidence({ data }: { data: DataRecord }) {
  const maintenanceCalls = asRecords(data.maintenance_calls);
  const rows = maintenanceCalls.map((call) => ({
    'Scheduled start': call.scheduled_start_date ?? call.planned_date ?? 'Not provided',
    'Scheduled end': call.scheduled_end_date ?? 'Not provided',
    'Maintenance plant': call.maintenance_plant ?? 'Not provided',
    'Production work center': call.production_work_center ?? 'Unlinked',
    'Maintenance order': call.maintenance_order ?? '-',
    'Maintenance item': call.maintenance_item ?? '-',
    Status: call.scheduling_status_text ?? call.scheduling_status ?? 'Not provided',
    Completed: call.completed === true ? 'Yes' : 'No',
  }));

  return (
    <>
      <MetricStrip items={[
        { label: 'Calls returned', value: data.maintenance_count ?? maintenanceCalls.length },
        { label: 'Unlinked work centers', value: data.unlinked_item_count ?? 0 },
        { label: 'Capacity impact', value: 'Not evaluated' },
      ]} />
      <section className="evidence-section">
        <div className="section-heading">
          <div><span className="section-kicker">Scheduled maintenance</span><h3>Maintenance calls by production work center</h3></div>
          <span className="row-count">{maintenanceCalls.length} calls</span>
        </div>
        <DataTable rows={rows} columns={[
          'Scheduled start', 'Scheduled end', 'Maintenance plant', 'Production work center',
          'Maintenance order', 'Maintenance item', 'Status', 'Completed',
        ]} />
        <p className="capacity-empty">Scheduled maintenance is source evidence; this view does not calculate capacity loss.</p>
      </section>
    </>
  );
}

function Evidence({ data }: { data: DataRecord }) {
  const [selectedProduct, setSelectedProduct] = useState('');
  const results = asRecords(data.results);
  const comparisonResults = asRecords(data.comparison_results);
  const alerts = asRecords(data.alert_results);
  const anomalies = asRecords(data.anomalies);
  const forecastSeries = asRecords(data.forecast_series);

  if (data.evidence_type === 's4_capacity_bottleneck') {
    return <S4CapacityBottleneckEvidence data={data} />;
  }

  if (data.evidence_type === 's4_maintenance_schedule') {
    return <S4MaintenanceEvidence data={data} />;
  }

  if (Array.isArray(data.capacity_evaluations)) {
    return <S4CapacityEvidence data={data} />;
  }

  if (data.master_data_type && Array.isArray(data.results)) {
    return <HealthCheckEvidence data={data} />;
  }

  if (data.key_figures || data.resource_type && (data.total_rows_evaluated !== undefined || data.tables)) {
    return <CapacityEvidence data={data} />;
  }

  if (forecastSeries.length > 0) {
    const activeSeries = forecastSeries.find((series) => String(series.product_id) === selectedProduct)
      || forecastSeries[0];
    const productId = String(activeSeries.product_id ?? 'Unknown product');
    const productAnomalies = anomalies.filter((anomaly) => String(anomaly.product_id) === productId);
    const anomalyPeriods = new Set(productAnomalies.map((anomaly) => String(anomaly.period)));
    const periods = asRecords(activeSeries.periods);
    const chartRows = periods.map((period) => {
      const label = String(period.period ?? '');
      const forecast = typeof period.forecast === 'number' ? period.forecast : null;
      return {
        period: label,
        forecast,
        anomaly: anomalyPeriods.has(label) ? forecast : null,
      };
    });
    const anomalyRows = productAnomalies.map((anomaly) => ({
      period: anomaly.period,
      type: anomaly.type,
      previous_quantity: anomaly.previous_quantity,
      forecast_quantity: anomaly.forecast_quantity,
      change_quantity: anomaly.change_quantity,
      pct_change: anomaly.pct_change == null ? 'Undefined (zero baseline)' : `${formatValue(anomaly.pct_change)}%`,
      description: anomaly.description,
    }));

    return (
      <>
        <MetricStrip items={[
          { label: 'Products scanned', value: forecastSeries.length },
          { label: 'Anomalies detected', value: data.anomaly_count ?? anomalies.length, tone: anomalies.length ? 'alert' : '' },
          { label: 'Forecast periods', value: periods.length },
          { label: 'Window', value: `${formatValue(data.period_start_rel)} to ${formatValue(data.period_end_rel)}` },
        ]} />
        <section className="evidence-section forecast-evidence">
          <div className="section-heading forecast-heading">
            <div><span className="section-kicker">Forecast history and outlook</span><h3>{productId} · statistical forecast</h3></div>
            <label className="product-picker">
              <span>Product</span>
              <select value={productId} onChange={(event) => setSelectedProduct(event.target.value)} aria-label="Select product forecast">
                {forecastSeries.map((series) => {
                  const optionProduct = String(series.product_id ?? 'Unknown product');
                  return <option key={optionProduct} value={optionProduct}>{optionProduct}</option>;
                })}
              </select>
            </label>
          </div>
          <div className="chart-frame forecast-chart">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartRows} margin={{ top: 12, right: 12, left: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke="#e7ebe7" />
                <XAxis dataKey="period" tickLine={false} axisLine={false} tick={{ fill: '#6d7974', fontSize: 11 }} />
                <YAxis tickLine={false} axisLine={false} tick={{ fill: '#6d7974', fontSize: 11 }} />
                <Tooltip formatter={(value, name) => [formatValue(value), name === 'anomaly' ? 'Detected anomaly' : 'Forecast quantity']} />
                <Line type="monotone" dataKey="forecast" name="Forecast quantity" stroke="#146a57" strokeWidth={2} dot={{ r: 3, fill: '#146a57' }} activeDot={{ r: 5 }} connectNulls={false} />
                <Line type="monotone" dataKey="anomaly" name="Detected anomaly" stroke="transparent" dot={{ r: 5, fill: '#d66b50', stroke: '#fff', strokeWidth: 2 }} activeDot={{ r: 6 }} connectNulls={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <h4 className="evidence-subheading">Forecast values by period</h4>
          <DataTable rows={chartRows.map(({ period, forecast }) => ({ period, forecast }))} columns={['period', 'forecast']} />
          <h4 className="evidence-subheading">Anomaly details</h4>
          {anomalyRows.length > 0
            ? <DataTable rows={anomalyRows} columns={['period', 'type', 'previous_quantity', 'forecast_quantity', 'change_quantity', 'pct_change', 'description']} />
            : <p className="table-empty">No anomalies were detected for this product in the returned periods.</p>}
        </section>
      </>
    );
  }

  if (comparisonResults.length > 0) {
    const getSeriesKey = (row: DataRecord) => [row.product, row.location, row.customer]
      .map((value) => value == null ? '' : String(value))
      .join('|');
    const seriesByKey = new Map<string, DataRecord[]>();
    for (const row of comparisonResults) {
      const key = getSeriesKey(row);
      const rows = seriesByKey.get(key) || [];
      rows.push(row);
      seriesByKey.set(key, rows);
    }
    const seriesKeys = [...seriesByKey.keys()];
    const activeKey = seriesKeys.includes(selectedProduct) ? selectedProduct : seriesKeys[0];
    const selectedRows = (seriesByKey.get(activeKey) || [])
      .sort((left, right) => String(left.period ?? '').localeCompare(String(right.period ?? '')));
    const firstRow = selectedRows[0] || {};
    const selectedLabel = [firstRow.product, firstRow.location, firstRow.customer]
      .filter((value) => value != null && value !== '')
      .map(String)
      .join(' · ') || 'Forecast and consumption';
    const chartRows = selectedRows.map((row) => ({
      period: String(row.period ?? ''),
      forecast: typeof row.forecast === 'number' ? row.forecast : null,
      actual: typeof row.actual === 'number' ? row.actual : null,
    }));
    const productCount = new Set(comparisonResults.map((row) => String(row.product ?? ''))).size;

    return (
      <>
        <MetricStrip items={[
          { label: 'Products compared', value: productCount },
          { label: 'Periods shown', value: selectedRows.length },
          { label: 'Alerts', value: data.alert_count ?? alerts.length, tone: Number(data.alert_count ?? alerts.length) ? 'alert' : '' },
          { label: 'Variance threshold', value: `${formatValue(data.threshold_pct)}%` },
        ]} />
        <section className="evidence-section forecast-evidence">
          <div className="section-heading forecast-heading">
            <div><span className="section-kicker">Demand signal comparison</span><h3>{selectedLabel}</h3></div>
            {seriesKeys.length > 1 && <label className="product-picker">
              <span>Product / series</span>
              <select value={activeKey} onChange={(event) => setSelectedProduct(event.target.value)} aria-label="Select forecast consumption series">
                {seriesKeys.map((key) => {
                  const sample = seriesByKey.get(key)?.[0] || {};
                  const label = [sample.product, sample.location, sample.customer]
                    .filter((value) => value != null && value !== '')
                    .map(String)
                    .join(' · ');
                  return <option key={key} value={key}>{label}</option>;
                })}
              </select>
            </label>}
          </div>
          <div className="chart-frame forecast-chart">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartRows} margin={{ top: 12, right: 12, left: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke="#e7ebe7" />
                <XAxis dataKey="period" tickLine={false} axisLine={false} tick={{ fill: '#6d7974', fontSize: 11 }} />
                <YAxis tickLine={false} axisLine={false} tick={{ fill: '#6d7974', fontSize: 11 }} />
                <Tooltip formatter={(value, name) => [formatValue(value), name === 'actual' ? 'Consumption' : 'Forecast']} />
                <Line type="monotone" dataKey="forecast" name="Forecast" stroke="#146a57" strokeWidth={2} dot={{ r: 3, fill: '#146a57' }} activeDot={{ r: 5 }} connectNulls={false} />
                <Line type="monotone" dataKey="actual" name="Consumption" stroke="#d66b50" strokeWidth={2} dot={{ r: 3, fill: '#d66b50' }} activeDot={{ r: 5 }} connectNulls={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <h4 className="evidence-subheading">Forecast and consumption by period</h4>
          <DataTable rows={chartRows} columns={['period', 'forecast', 'actual']} />
          {alerts.length > 0 && <>
            <h4 className="evidence-subheading">Periods above the variance threshold</h4>
            <DataTable rows={alerts} />
          </>}
        </section>
      </>
    );
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
  const [agentMode, setAgentMode] = useState<'ibp' | 's4' | 'sales' |'maintenance' | 'scenario'>('ibp');
  const [showAgentDirectory, setShowAgentDirectory] = useState(true);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [connection, setConnection] = useState<'connected' | 'running' | 'offline'>('connected');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [error, setError] = useState('');
  const messageListRef = useRef<HTMLDivElement>(null);
  const activeConversation = conversations.find((conversation) => conversation.id === activeId);
  const messages = activeConversation?.messages || EMPTY_MESSAGES;
  const dashboardData = latestInsight(messages);
  const chatAgentName = agentMode === 's4'
    ? 'S/4 Capacity Reader'
    : agentMode === 'maintenance'
      ? 'S/4 Maintenance Planner'
      : 'IBP Demand Planning Agent';

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    } catch {
      setError('Conversation history could not be saved in this browser.');
    }
  }, [conversations]);

  useEffect(() => {
    if (!messageListRef.current) return;
    messageListRef.current.scrollTop = messages.length === 0
      ? 0
      : messageListRef.current.scrollHeight;
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

  function toggleHistory() {
    if (window.matchMedia('(max-width: 980px)').matches) {
      setHistoryOpen((open) => !open);
      return;
    }
    setHistoryCollapsed((collapsed) => !collapsed);
  }

  function selectAgentMode(mode: 'ibp' | 's4' | 'sales' | 'maintenance' | 'scenario') {
    if (mode === agentMode) return;
    setAgentMode(mode);
    if (activeConversation && (activeConversation.agentMode || 'ibp') !== mode) {
      setActiveId(null);
    }
    setError('');
  }

  function openAgent(mode: 'ibp' | 's4' | 'sales' | 'maintenance' | 'scenario') {
    selectAgentMode(mode);
    setShowAgentDirectory(false);
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
    const selectedMode = existing?.agentMode || agentMode;
    const now = new Date().toISOString();
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: message };
    const optimistic: Conversation = existing
      ? { ...existing, updatedAt: now, messages: [...existing.messages, userMessage] }
      : { id: conversationId, agentMode: selectedMode, title: getTitle(message), updatedAt: now, messages: [userMessage] };

    setActiveId(conversationId);
    saveConversation(optimistic);

    try {
      const endpoint = selectedMode === 'scenario'
        ? '/customer-base/ask'
        : selectedMode === 's4'
          ? '/s4/ask'
          : selectedMode === 'sales'
            ? '/sales/ask'
            : selectedMode === 'maintenance'
              ? '/maintenance/ask'
              : '/ask';
      const response = await fetch(endpoint, {
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

  if (showAgentDirectory) {
    return (
      <div className="app-frame agent-directory-frame">
        <header className="topbar directory-topbar">
          <div className="brand-lockup">
            <div className="brand-mark"><img src={yashLogo} alt="YASH Technologies" /></div>
            <div className="brand-copy"><strong>Yash-SAP AI Agent Dashboard</strong><span>Planners Workspace</span></div>
          </div>
          <div className="directory-topbar-label"><span className="directory-live-dot" /> Planning intelligence network</div>
        </header>
        <main className="agent-directory">
          <section className="directory-intro">
            <div>
              <span className="directory-kicker"><span /> AGENT WORKSPACES <i /> SAP CONNECTED</span>
              <h1>Choose your<br /><span>planning perspective.</span></h1>
              <p>Five specialist agents. One connected planning workspace.</p>
            </div>
            <div className="directory-network" aria-hidden="true">
              <div className="network-core"><Sparkles size={22} /></div>
              <span className="network-node node-one"><Activity size={15} /></span>
              <span className="network-node node-two"><Factory size={15} /></span>
              <span className="network-node node-three"><ShieldCheck size={15} /></span>
              <span className="network-line line-one" />
              <span className="network-line line-two" />
              <span className="network-line line-three" />
            </div>
          </section>

          <section className="agent-directory-grid" aria-label="Available agent workspaces">
            {AGENTS.map((agent) => {
              const Icon = agent.icon;
              return (
                <article className={`directory-agent-card ${agent.tone}`} key={agent.id}>
                  <div className="directory-card-topline"><span>WORKSPACE {agent.index}</span><span className="directory-card-state"><i /> READY</span></div>
                  <div className="directory-agent-icon"><Icon size={21} strokeWidth={1.8} /></div>
                  <span className="directory-agent-descriptor">{agent.descriptor}</span>
                  <h2>{agent.name}</h2>
                  <p>{agent.description}</p>
                  <ul>{agent.capabilities.map((capability) => <li key={capability}>{capability}</li>)}</ul>
                  <button type="button" className="directory-open-button" onClick={() => openAgent(agent.id)}>
                    Open workspace <ArrowRight size={16} />
                  </button>
                </article>
              );
            })}
          </section>

          <footer className="directory-footer">
            <span><i /> SHARED PLANNING FOUNDATION</span>
            <p>SAP data access <b /> Conversation history <b /> Read-only analysis where applicable</p>
          </footer>
        </main>
      </div>
    );
  }

  if (agentMode === 'sales') {
    return (
      <SalesAssurance
        data={activeConversation?.agentMode === 'sales' ? dashboardData : null}
        messages={messages}
        loading={loading}
        error={error}
        onAsk={(value) => void submitQuestion(value)}
        onNewConversation={startNewConversation}
        onSelectMode={selectAgentMode}
        onShowAgents={() => setShowAgentDirectory(true)}
      />
    );
  }
  if (agentMode === 'scenario') {
    return (
      <ScenarioTestAgent
        messages={messages}
        loading={loading}
        error={error}
        onAsk={(value) => void submitQuestion(value)}
        onNewConversation={startNewConversation}
        onShowAgents={() => setShowAgentDirectory(true)}
      />
    );
  }
  const salesModeSelected = (agentMode as string) === 'sales';
  return (
    <div className={`app-frame ${historyOpen ? 'history-open' : ''} ${historyCollapsed ? 'history-collapsed' : ''}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><img src={yashLogo} alt="YASH Technologies" /></div>
          <div className="brand-copy"><strong>Yash-SAP AI Agent Dashboard</strong><span>Planners Workspace</span></div>
        </div>
        <div className="topbar-agent-switch" role="radiogroup" aria-label="Choose planning agent">
          <button type="button" role="radio" aria-checked={agentMode === 'ibp'} className={agentMode === 'ibp' ? 'selected' : ''} onClick={() => selectAgentMode('ibp')} disabled={loading}>IBP</button>
          <button type="button" role="radio" aria-checked={agentMode === 's4'} className={agentMode === 's4' ? 'selected' : ''} onClick={() => selectAgentMode('s4')} disabled={loading}>S/4 Capacity</button>
          <button type="button" role="radio" aria-checked={salesModeSelected} className={salesModeSelected ? 'selected' : ''} onClick={() => selectAgentMode('sales')} disabled={loading}>Sales</button>
          <button type="button" role="radio" aria-checked={agentMode === 'maintenance'} className={agentMode === 'maintenance' ? 'selected' : ''} onClick={() => selectAgentMode('maintenance')} disabled={loading}>Maintenance</button>
        </div>
        <div className="topbar-right">
          <button type="button" className="directory-return-button" onClick={() => setShowAgentDirectory(true)}><PanelLeftOpen size={15} /> All agents</button>
          <span className={`connection-state ${connection}`}><i />{connection === 'running' ? 'Analyzing' : connection === 'offline' ? 'Agent unavailable' : 'Agent connected'}</span>
          <button className="icon-button history-toggle" aria-label="Toggle conversation history" title="Toggle conversation history" onClick={toggleHistory}>
            {historyCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
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
                onClick={() => { setActiveId(conversation.id); setAgentMode(conversation.agentMode || 'ibp'); setShowAgentDirectory(false); setError(''); setHistoryOpen(false); }}
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

        <aside className="agent-panel" aria-label={`${chatAgentName} chat`}>
          <div className="agent-header">
            <div className="agent-avatar"><img src={jouleLogo} alt="SAP Joule" /></div>
            <div className="agent-heading"><strong>{chatAgentName}</strong><span><i /> Ready to analyze</span></div>
          </div>
          <div className="chat-context"><span className="context-line" />{activeConversation ? activeConversation.title : 'New conversation'}</div>
          <div className="message-list" ref={messageListRef} aria-live="polite">
            {messages.length === 0 ? (
              <div className="chat-welcome">
                <span className="welcome-glyph"><Sparkles size={19} /></span>
                <h2>{agentMode === 'maintenance' ? 'Review upcoming maintenance windows.' : agentMode === 's4' ? 'Read your S/4 capacity setup.' : 'Good planning starts with a clear signal.'}</h2>
                <p>{agentMode === 'maintenance' ? 'Inspect scheduled calls, their order intervals, and linked production work centers.' : agentMode === 's4' ? 'Inspect configured work-center capacity and shift intervals.' : 'Ask about forecasts, anomalies, data readiness, or capacity constraints.'}</p>
                <div className="prompt-list">
                  {(agentMode === 'maintenance'
                    ? [
                        'Show scheduled maintenance calls in the next 30 days.',
                        'Which production work centers are affected by upcoming maintenance?',
                        'Show maintenance calls for work center [work center] between YYYY-MM-DD and YYYY-MM-DD.',
                      ]
                    : agentMode === 's4'
                      ? ['Is there a possibility of a capacity bottleneck for any resource from DD/MM/YYYY to DD/MM/YYYY?']
                      : PROMPTS).map((prompt) => <button key={prompt} onClick={() => void submitQuestion(prompt)} disabled={loading}>{prompt}<ArrowUpRight size={14} /></button>)}
                </div>
              </div>
            ) : messages.map((message) => (
              <article className={`message ${message.role}`} key={message.id}>
                <span className="message-label">{message.role === 'user' ? 'You' : chatAgentName}</span>
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
                placeholder={agentMode === 'maintenance' ? 'Ask about scheduled maintenance...' : agentMode === 's4' ? 'Ask about work-center capacity...' : 'Ask about your plan...'}
                rows={2}
                aria-label={`Message ${chatAgentName}`}
                disabled={loading}
              />
              <div className="composer-controls"><span>Enter to send <i /> Shift + Enter for a new line</span><button type="submit" aria-label="Send message" disabled={!question.trim() || loading}><Send size={16} /></button></div>
            </form>
            <p className="privacy-note">{agentMode === 'maintenance' ? 'Read-only S/4 maintenance schedule data.' : agentMode === 's4' ? 'Read-only S/4 capacity data.' : 'Analysis is based on available planning data.'}</p>
          </div>
        </aside>
      </div>
      <button className="mobile-backdrop" aria-label="Close conversation history" onClick={() => setHistoryOpen(false)} />
    </div>
  );
}