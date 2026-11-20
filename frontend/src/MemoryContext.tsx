import type { MemoryContext as MemoryContextData } from './types';
import './memory-context.css';

export default function MemoryContext({ context }: { context: MemoryContextData }) {
  if (context.status === 'not_forwarded') return <p className="memory-context-note">Conversation context was not forwarded for this response.</p>;
  if (context.status === 'none') return <p className="memory-context-note">No earlier conversation context was supplied for this response.</p>;
  return <details className="memory-context">
    <summary>Conversation context supplied</summary>
    <p>{context.recent_turns} recent {context.recent_turns === 1 ? 'exchange' : 'exchanges'} from this conversation; summary covering {context.summary_turns} saved {context.summary_turns === 1 ? 'exchange' : 'exchanges'}.</p>
    {context.summary_refresh_failed && <p>The summary could not be refreshed; the last saved summary was supplied.</p>}
    {context.excerpts.length > 0 && <><p>Related earlier requests:</p><ul>{context.excerpts.map((excerpt, index) => <li key={index}><span>{excerpt.source}{excerpt.date ? ` · ${excerpt.date.slice(0, 10)}` : ''}</span><br />{excerpt.question}</li>)}</ul></>}
    <p>This shows the context supplied, not whether it affected the answer. Summaries can omit details.</p>
  </details>;
}
