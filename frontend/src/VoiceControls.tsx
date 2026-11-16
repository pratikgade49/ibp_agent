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
