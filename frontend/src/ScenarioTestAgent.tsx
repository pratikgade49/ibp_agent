import { FormEvent, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowLeft, ArrowUpRight, Layers3, MessageSquarePlus, Send } from 'lucide-react';
import type { ChatMessage } from './types';
import ScenarioForecastPreview from './ScenarioForecastPreview';
import './scenario-test-agent.css';

interface ScenarioTestAgentProps {
  messages: ChatMessage[];
  loading: boolean;
  error: string;
  onAsk: (message: string) => void;
  onNewConversation: () => void;
  onShowAgents: () => void;
}

const EXAMPLE = 'Prepare the customer base forecast for planning version BASELINE and scenario <SCENARIOID>, from 2026-10 through 2027-05. Use the 2026-09 Customer Base Forecast value as the seed; calculate October and each following month recursively from the previous month forecast.';

export default function ScenarioTestAgent({
  messages,
  loading,
  error,
  onAsk,
  onNewConversation,
  onShowAgents,
}: ScenarioTestAgentProps) {
  const [draft, setDraft] = useState('');

  function submit(value: string) {
    const text = value.trim();
    if (!text || loading) return;
    setDraft('');
    onAsk(text);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit(draft);
  }

  return (
    <main className="scenario-agent-shell">
      <header className="scenario-agent-topbar">
        <button className="scenario-back" type="button" onClick={onShowAgents}>
          <ArrowLeft size={16} /> <span>All agents</span>
        </button>
        <div className="scenario-agent-brand">
          <span className="scenario-brand-icon"><Layers3 size={18} /></span>
          <span><strong>IBP Scenario Test Agent</strong><small>Customer Base Forecast · INSPROD</small></span>
        </div>
        <button className="scenario-new-chat" type="button" onClick={onNewConversation} aria-label="Start a new scenario chat" title="New chat">
          <MessageSquarePlus size={17} />
        </button>
      </header>

      <section className="scenario-chat-layout">
        <div className="scenario-chat-heading">
          <span className="scenario-kicker"><i /> SCENARIO TEST WORKSPACE</span>
          <h1>Test a customer-base scenario.</h1>
          <p>Enter the parent planning version, scenario ID, and forecast period. For Baseline, use BASELINE. The agent previews results before import.</p>
        </div>

        <ScenarioForecastPreview messages={messages} />

        <section className="scenario-chat-window" aria-label="IBP Scenario Test Agent chat">
          <div className="scenario-thread" aria-live="polite">
            {messages.length === 0 ? (
              <div className="scenario-welcome">
                <span className="scenario-welcome-mark"><Layers3 size={20} /></span>
                <div>
                  <span className="scenario-kicker">CUSTOMER BASE FORECAST</span>
                  <h2>Start with the version and scenario IDs.</h2>
                  <p>Use an IBP scenario shared with the integration user. BASELINE is accepted as the parent version name; the existing customer-base forecast seeds the calculation.</p>
                </div>
                <button type="button" className="scenario-example" onClick={() => setDraft(EXAMPLE)} disabled={loading}>
                  <span>Example request</span><ArrowUpRight size={15} />
                  <strong>{EXAMPLE}</strong>
                </button>
              </div>
            ) : messages.map((message) => (
              <article className={`scenario-message ${message.role}`} key={message.id}>
                <span className="scenario-message-label">{message.role === 'user' ? 'PLANNER' : 'SCENARIO AGENT'}</span>
                {message.role === 'assistant'
                  ? <div className="scenario-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>
                  : <p>{message.content}</p>}
              </article>
            ))}
            {loading && <div className="scenario-thinking"><i /><i /><i /> Checking scenario inputs</div>}
          </div>
          {error && <p className="scenario-error" role="alert">{error}</p>}
          <form className="scenario-composer" onSubmit={handleSubmit}>
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Enter planning version, scenario ID, and forecast request..."
              aria-label="Message the IBP Scenario Test Agent"
              rows={3}
              disabled={loading}
            />
            <div className="scenario-composer-footer">
              <span>Version and scenario IDs are used for read and write</span>
              <button type="submit" aria-label="Send request" disabled={!draft.trim() || loading}><Send size={16} /></button>
            </div>
          </form>
        </section>
        <footer className="scenario-agent-footer"><span>IBP connection: separate scenario-agent credentials</span><span>Writes require the exact approval phrase</span></footer>
      </section>
    </main>
  );
}