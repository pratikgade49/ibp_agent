import { useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Volume2, VolumeX } from 'lucide-react';
import './voice.css';

type RecognitionResult = { isFinal: boolean; [index: number]: { transcript: string } };
type RecognitionEvent = { resultIndex: number; results: ArrayLike<RecognitionResult> };
type Recognition = { lang: string; continuous: boolean; interimResults: boolean; onresult: ((event: RecognitionEvent) => void) | null; onerror: ((event: { error: string }) => void) | null; onend: (() => void) | null; start: () => void; stop: () => void; abort: () => void };
type RecognitionConstructor = new () => Recognition;

export function VoiceInput({ value, onChange, disabled = false }: { value: string; onChange: (value: string) => void; disabled?: boolean }) {
  const recognition = useRef<Recognition | null>(null);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState('');
  const browser = window as typeof window & { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
  const Constructor = browser.SpeechRecognition || browser.webkitSpeechRecognition;
  useEffect(() => () => { if (recognition.current) { recognition.current.onend = null; recognition.current.onerror = null; recognition.current.onresult = null; recognition.current.abort(); } }, []);
  useEffect(() => {
    if (disabled && recognition.current) {
      recognition.current.onresult = null;
      recognition.current.stop();
    }
  }, [disabled]);
  function toggle() {
    if (recognition.current) { recognition.current.stop(); return; }
    if (!Constructor) return;
    setError('');
    const instance = new Constructor();
    recognition.current = instance;
    instance.lang = document.documentElement.lang || 'en';
    instance.continuous = true;
    instance.interimResults = true;
    const prefix = value.trimEnd();
    let finalText = '';
    instance.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interim += result[0].transcript;
      }
      onChange([prefix, (finalText + interim).trim()].filter(Boolean).join(' '));
    };
    instance.onerror = (event) => setError(event.error === 'not-allowed' || event.error === 'service-not-allowed' ? 'Microphone access was denied. Check browser permissions.' : `Speech recognition: ${event.error}.`);
    instance.onend = () => { recognition.current = null; setListening(false); };
    try { instance.start(); setListening(true); } catch { recognition.current = null; setError('Could not start the microphone.'); }
  }
  return <div className="voice-input"><button type="button" className="voice-button" onClick={toggle} disabled={!Constructor || disabled} aria-label={listening ? 'Stop listening' : 'Dictate message'} aria-pressed={listening} title={Constructor ? (listening ? 'Stop listening' : 'Dictate message') : 'Speech recognition is not supported in this browser'}>{listening ? <MicOff size={16} /> : <Mic size={16} />}<span>{listening ? 'Stop' : 'Dictate'}</span></button>{!Constructor && <span className="voice-status">Speech input unavailable in this browser.</span>}{error && <span className="voice-status" role="alert">{error}</span>}</div>;
}

export function SpeakResponse({ text }: { text: string }) {
  const [speaking, setSpeaking] = useState(false);
  const utterance = useRef<SpeechSynthesisUtterance | null>(null);
  const supported = 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;
  useEffect(() => () => { if (utterance.current) { utterance.current.onend = null; utterance.current.onerror = null; window.speechSynthesis.cancel(); utterance.current = null; } }, []);
  function toggle() {
    if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); utterance.current = null; return; }
    window.speechSynthesis.cancel();
    const next = new SpeechSynthesisUtterance(text.replace(/```[\s\S]*?```/g, ' ').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[*_`#|]/g, ' '));
    utterance.current = next;
    next.onend = next.onerror = () => { utterance.current = null; setSpeaking(false); };
    setSpeaking(true);
    window.speechSynthesis.speak(next);
  }
  return <button type="button" className="voice-speak" disabled={!supported} onClick={toggle} aria-label={speaking ? 'Stop spoken response' : 'Read response aloud'} title={supported ? 'Read response aloud' : 'Spoken responses are unavailable in this browser'}>{speaking ? <VolumeX size={15} /> : <Volume2 size={15} />}{speaking ? 'Stop' : 'Listen'}</button>;
}

export function VoiceConversation({ onSubmit, disabled = false }: { onSubmit: (text: string) => Promise<string | undefined>; disabled?: boolean }) {
  const [phase, setPhase] = useState<'off' | 'listening' | 'thinking' | 'speaking'>('off');
  const [error, setError] = useState('');
  const active = useRef(false);
  const generation = useRef(0);
  const recognition = useRef<Recognition | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const submitRef = useRef(onSubmit);
  submitRef.current = onSubmit;
  const browser = window as typeof window & { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
  const Constructor = browser.SpeechRecognition || browser.webkitSpeechRecognition;
  const supported = !!Constructor && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;

  function stop() {
    active.current = false;
    generation.current += 1;
    if (retryTimer.current) clearTimeout(retryTimer.current);
    if (recognition.current) {
      recognition.current.onresult = null; recognition.current.onerror = null; recognition.current.onend = null;
      recognition.current.abort(); recognition.current = null;
    }
    window.speechSynthesis?.cancel();
    setPhase('off');
  }
  useEffect(() => () => {
    active.current = false; generation.current += 1;
    if (retryTimer.current) clearTimeout(retryTimer.current);
    if (recognition.current) { recognition.current.onresult = null; recognition.current.onerror = null; recognition.current.onend = null; recognition.current.abort(); recognition.current = null; }
    window.speechSynthesis?.cancel();
  }, []);

  function listen(token: number) {
    if (!active.current || generation.current !== token || !Constructor) return;
    const instance = new Constructor();
    recognition.current = instance;
    instance.lang = document.documentElement.lang || 'en';
    instance.continuous = false;
    instance.interimResults = false;
    let transcript = '';
    let fatal = false;
    instance.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) if (event.results[i].isFinal) transcript += event.results[i][0].transcript;
    };
    instance.onerror = (event) => {
      if (event.error === 'no-speech' || event.error === 'aborted') return;
      fatal = true;
      setError(event.error === 'not-allowed' || event.error === 'service-not-allowed' ? 'Microphone access was denied. Check browser permissions.' : `Voice conversation stopped: ${event.error}.`);
      active.current = false;
      setPhase('off');
    };
    instance.onend = () => {
      if (recognition.current === instance) recognition.current = null;
      if (!active.current || generation.current !== token || fatal) return;
      const text = transcript.trim();
      if (!text) { retryTimer.current = setTimeout(() => listen(token), 500); return; }
      setPhase('thinking');
      void submitRef.current(text).then((reply) => {
        if (!active.current || generation.current !== token) return;
        if (!reply) { setError('The agent did not return a response. Voice conversation ended.'); stop(); return; }
        const speech = new SpeechSynthesisUtterance(reply.replace(/```[\s\S]*?```/g, ' ').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/[*_`#|]/g, ' '));
        speech.onend = () => { if (active.current && generation.current === token) listen(token); };
        speech.onerror = () => { if (active.current && generation.current === token) { setError('Could not play the response. Voice conversation ended.'); stop(); } };
        setPhase('speaking');
        window.speechSynthesis.speak(speech);
      }).catch(() => { if (active.current && generation.current === token) { setError('Could not contact the agent. Voice conversation ended.'); stop(); } });
    };
    try { instance.start(); setPhase('listening'); } catch { recognition.current = null; active.current = false; setPhase('off'); setError('Could not start the microphone.'); }
  }
  function toggle() {
    if (active.current) { stop(); return; }
    if (!supported) return;
    setError(''); active.current = true;
    const token = ++generation.current;
    window.speechSynthesis.cancel();
    listen(token);
  }
  return <div className="voice-conversation"><button type="button" className="voice-conversation-button" onClick={toggle} disabled={!supported || (disabled && phase === 'off')} aria-pressed={phase !== 'off'}><Mic size={16} />{phase === 'off' ? 'Start voice conversation' : 'End voice conversation'}</button><span className="voice-status" role="status">{!supported ? 'Voice conversation is unavailable in this browser.' : phase === 'listening' ? 'Listening…' : phase === 'thinking' ? 'Agent is thinking…' : phase === 'speaking' ? 'Agent is speaking…' : ''}</span>{error && <span className="voice-status" role="alert">{error}</span>}</div>;
}
