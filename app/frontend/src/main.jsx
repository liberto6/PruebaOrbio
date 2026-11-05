import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const COPY = {
  es: {
    assistant: 'Asistente virtual',
    newApplication: 'Nueva candidatura',
    confirmNew: 'Esta candidatura quedará incompleta. ¿Empezar otra?',
    confirmYes: 'Sí, empezar otra',
    confirmNo: 'Cancelar',
    loading: 'Cargando…',
    inputLabel: 'Tu respuesta',
    placeholder: 'Escribe tu respuesta',
    send: 'Enviar',
    sending: 'Enviando tu mensaje…',
    retry: 'Reintentar',
    pending: 'Tu mensaje está pendiente de confirmación. Puedes reintentar sin que se duplique.',
    unconfirmed: 'Pendiente de confirmación',
    closed: 'Esta conversación ha terminado.',
    network: 'Ha habido un problema técnico. Por favor, inténtalo de nuevo.',
  },
  en: {
    assistant: 'Virtual assistant',
    newApplication: 'New application',
    confirmNew: 'This application will stay incomplete. Start another?',
    confirmYes: 'Yes, start another',
    confirmNo: 'Cancel',
    loading: 'Loading…',
    inputLabel: 'Your answer',
    placeholder: 'Write your answer',
    send: 'Send',
    sending: 'Sending your message…',
    retry: 'Retry',
    pending: 'Your message is waiting for confirmation. You can retry without duplicating it.',
    unconfirmed: 'Waiting for confirmation',
    closed: 'This conversation has ended.',
    network: 'There was a technical problem. Please try again.',
  },
};

const APPLICATION_KEY = 'sazon.application';
const UNCONFIRMED_KEY = 'sazon.unconfirmed';

// Browser storage can be unavailable; the conversation still works until a reload.
function readStored(key) {
  try {
    return JSON.parse(localStorage.getItem(key));
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Nothing else to do: the value stays in memory.
  }
}

async function call(path, options) {
  const response = await fetch(path, options);
  return { ok: response.ok, body: await response.json().catch(() => null) };
}

function App() {
  const [conversation, setConversation] = useState(null);
  const [unconfirmed, setUnconfirmed] = useState(() => readStored(UNCONFIRMED_KEY));
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [starting, setStarting] = useState(true);
  const [sending, setSending] = useState(false);
  const [confirmingNew, setConfirmingNew] = useState(false);
  const inputRef = useRef(null);
  const endRef = useRef(null);
  const started = useRef(false);

  const language = conversation?.language ?? 'es';
  const copy = COPY[language];
  const closed = conversation?.closed ?? false;
  const pending = conversation?.pending ?? null;
  const pendingText = pending
    && conversation.messages.find((message) => message.sequence === pending.candidate_sequence)?.text;
  // The saved turn goes first: the server refuses anything else until it finishes.
  const retryable = pending
    ? { request_id: pending.request_id, text: pendingText }
    : unconfirmed;
  const canWrite = conversation && !closed && !notFound && !sending && !retryable;
  const unsaved = unconfirmed && unconfirmed.request_id !== pending?.request_id ? unconfirmed : null;

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [conversation?.messages.length, unsaved?.request_id, sending]);

  useEffect(() => {
    if (canWrite) inputRef.current?.focus();
  }, [canWrite]);

  function forgetUnconfirmed(request) {
    const current = readStored(UNCONFIRMED_KEY);
    if (!request || current?.request_id === request.request_id) writeStored(UNCONFIRMED_KEY, null);
    setUnconfirmed((value) => (!request || value?.request_id === request.request_id ? null : value));
  }

  async function createApplication() {
    setStarting(true);
    setNotice(null);
    try {
      const { ok, body } = await call('/api/applications', { method: 'POST' });
      if (!ok) {
        setNotice(body?.notice ?? copy.network);
        return;
      }
      // Only now: until a new record exists, the current one stays in place.
      writeStored(APPLICATION_KEY, body.id);
      forgetUnconfirmed(null);
      setConversation(body);
      setNotFound(false);
      setConfirmingNew(false);
      setDraft('');
    } catch {
      setNotice(copy.network);
    } finally {
      setStarting(false);
    }
  }

  // SQLite is the authority: a failed read keeps what is on screen and any local request.
  async function loadApplication(id, reportFailure) {
    try {
      const { ok, body } = await call(`/api/applications/${encodeURIComponent(id)}`);
      if (ok) {
        setConversation(body);
      } else if (body?.code === 'application_not_found') {
        setNotFound(true);
        setNotice(body.notice);
      } else if (reportFailure) {
        setNotice(body?.notice ?? copy.network);
      }
    } catch {
      if (reportFailure) setNotice(copy.network);
    }
  }

  async function start() {
    const id = readStored(APPLICATION_KEY);
    if (!id) {
      await createApplication();
      return;
    }
    setStarting(true);
    setNotice(null);
    await loadApplication(id, true);
    setStarting(false);
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    start();
  }, []);

  async function deliver(request) {
    const id = conversation.id;
    setSending(true);
    setNotice(null);
    try {
      const { ok, body } = await call(`/api/applications/${encodeURIComponent(id)}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: request.request_id, text: request.text }),
      });
      if (ok) {
        forgetUnconfirmed(request);
        setConversation(body);
        return;
      }
      setNotice(body?.notice ?? copy.network);
      if (['invalid_request', 'request_conflict', 'turn_pending'].includes(body?.code)) {
        // The server refused this text for good: give it back instead of resending it.
        forgetUnconfirmed(request);
        setDraft(request.text);
      } else if (body?.code === 'application_closed') {
        forgetUnconfirmed(request);
      }
      if (body?.code !== 'invalid_request') await loadApplication(id, false);
    } catch {
      setNotice(copy.network);
      await loadApplication(id, false);
    } finally {
      setSending(false);
    }
  }

  function submit(event) {
    event.preventDefault();
    if (!canWrite || !draft.trim()) return;
    const request = { request_id: crypto.randomUUID(), text: draft };
    writeStored(UNCONFIRMED_KEY, request);
    setUnconfirmed(request);
    setDraft('');
    deliver(request);
  }

  function pressKey(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  }

  function askForNewApplication() {
    if (conversation && !closed && !notFound) setConfirmingNew(true);
    else createApplication();
  }

  return (
    <div className="chat">
      <header>
        <div>
          <p className="brand">Grupo Sazón</p>
          <p className="role">{copy.assistant}</p>
        </div>
        <button type="button" className="secondary" onClick={askForNewApplication}
          disabled={sending || starting || confirmingNew}>
          {copy.newApplication}
        </button>
      </header>

      {confirmingNew && (
        <div className="bar">
          <p>{copy.confirmNew}</p>
          <div className="actions">
            <button type="button" className="secondary" onClick={() => setConfirmingNew(false)}>
              {copy.confirmNo}
            </button>
            <button type="button" onClick={createApplication} disabled={starting}>
              {copy.confirmYes}
            </button>
          </div>
        </div>
      )}

      <main role="log" aria-live="polite">
        {conversation?.messages.map((message) => (
          <p key={message.sequence} className={`message ${message.role}`}>{message.text}</p>
        ))}
        {!notFound && unsaved && (
          <div className="message candidate unsaved">
            {unsaved.text}
            {!sending && <small>{copy.unconfirmed}</small>}
          </div>
        )}
        {starting && <p className="status">{copy.loading}</p>}
        {sending && <p className="status">{copy.sending}</p>}
        <div ref={endRef} />
      </main>

      <footer>
        {notice && <p className="notice" role="alert">{notice}</p>}
        {conversation && !notice && !sending && retryable && (
          <p className="notice">{copy.pending}</p>
        )}
        {conversation && !notFound && !sending && retryable && (
          <button type="button" onClick={() => deliver(retryable)}>{copy.retry}</button>
        )}
        {!conversation && !notFound && !starting && (
          <button type="button" onClick={start}>{copy.retry}</button>
        )}
        {notFound && (
          <button type="button" onClick={createApplication} disabled={starting}>
            {copy.newApplication}
          </button>
        )}
        {closed && !retryable && !notice && <p className="notice">{copy.closed}</p>}
        {conversation && !closed && !notFound && (
          <form onSubmit={submit}>
            <textarea ref={inputRef} rows="2" value={draft} aria-label={copy.inputLabel}
              placeholder={copy.placeholder} disabled={!canWrite}
              onChange={(event) => setDraft(event.target.value)} onKeyDown={pressKey} />
            <button type="submit" disabled={!canWrite || !draft.trim()}>{copy.send}</button>
          </form>
        )}
      </footer>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
