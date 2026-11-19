import { FormEvent, useId, useState } from 'react';
import { ThumbsDown, ThumbsUp } from 'lucide-react';
import './feedback-controls.css';

type Rating = 'helpful' | 'unhelpful';

export default function FeedbackControls({ turnId }: { turnId: string }) {
  const inputId = useId();
  const [rating, setRating] = useState<Rating | null>(null);
  const [correction, setCorrection] = useState('');
  const [showCorrection, setShowCorrection] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function save(nextRating: Rating, feedbackCorrection = '') {
    if (busy) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch('/memory/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ turn_id: turnId, rating: nextRating, ...(feedbackCorrection.trim() ? { correction: feedbackCorrection.trim() } : {}) }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not save your feedback.');
      }
      setRating(nextRating); setShowCorrection(false); setCorrection(''); setMessage('Feedback saved.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save your feedback.');
    } finally { setBusy(false); }
  }

  function submitCorrection(event: FormEvent) { event.preventDefault(); void save('unhelpful', correction); }

  return <div className="response-feedback" aria-label="Feedback for this response">
    <div className="response-feedback-actions"><span>Was this helpful?</span>
      <button type="button" aria-label="Mark response helpful" aria-pressed={rating === 'helpful'} disabled={busy} onClick={() => void save('helpful')}><ThumbsUp size={14} /> Helpful</button>
      <button type="button" aria-label="Mark response unhelpful" aria-pressed={rating === 'unhelpful'} disabled={busy} onClick={() => { setShowCorrection(true); setMessage(''); setError(''); }}><ThumbsDown size={14} /> Not helpful</button>
    </div>
    {showCorrection && <form className="response-feedback-form" onSubmit={submitCorrection}>
      <label htmlFor={inputId}>What should the response say? <span>(optional)</span></label>
      <textarea id={inputId} value={correction} onChange={event => setCorrection(event.target.value)} rows={3} maxLength={4000} disabled={busy} placeholder="Add a correction or suggestion" />
      <div><button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Submit feedback'}</button><button type="button" disabled={busy} onClick={() => setShowCorrection(false)}>Cancel</button></div>
    </form>}
    {message && <span className="response-feedback-success" role="status">{message}</span>}
    {error && <span className="response-feedback-error" role="alert">{error}</span>}
  </div>;
}
