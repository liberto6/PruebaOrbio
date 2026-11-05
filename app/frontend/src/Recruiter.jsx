import React, { useEffect, useRef, useState } from 'react';

const API = '/api/recruiter/applications';
const NETWORK = 'Ha habido un problema técnico. Inténtalo de nuevo.';
const NO_NAME = 'Nombre no recogido';
const NOT_COLLECTED = 'No recogido';
const NEEDS_REVIEW = 'Requiere revisión';
const SAVED = 'Decisión guardada.';

const OUTCOMES = {
  incomplete: 'Incompleto',
  qualified: 'Screening superado',
  disqualified: 'Descalificado',
  pending_review: 'Pendiente de revisión',
};
const DECISIONS = { unreviewed: 'Sin revisar', advance: 'Avanza', rejected: 'Descartado' };
const FIELDS = {
  full_name: 'Nombre completo',
  driving_license: 'Permiso de conducir',
  work_locations: 'Zona de trabajo',
  availability: 'Disponibilidad',
  preferred_schedule: 'Horario preferido',
  delivery_experience: 'Experiencia en reparto',
  start_timing: 'Incorporación',
};
const FIELD_REASONS = {
  incomplete_name: 'Nombre incompleto',
  in_progress: 'Permiso en trámite',
  validity_unclear: 'Validez del permiso sin confirmar',
  retracted: 'Se retractó sin dar otro dato',
  residence_only: 'Solo indicó dónde vive',
  no_place: 'No indicó un lugar concreto',
  not_answered: 'No respondió a la pregunta',
  experience_without_duration: 'Experiencia sin duración',
  experience_without_platform: 'Experiencia sin empresa o plataforma',
  experience_without_platforms: 'Experiencia sin empresa o plataforma',
  experience_zero_with_platforms: 'Experiencia contradictoria (0 años con plataforma)',
  start_imprecise: 'Incorporación imprecisa',
};
const OUTCOME_REASONS = {
  screening_passed: 'Datos completos, permiso y cobertura confirmados',
  no_driving_license: 'Sin permiso de conducir',
  location_not_covered: 'Zona fuera de cobertura',
  coverage_unresolved: 'Cobertura por aclarar',
};
const AVAILABILITY = {
  full_time: 'Jornada completa',
  part_time: 'Jornada parcial',
  weekends: 'Fines de semana',
};
const SCHEDULE = { morning: 'Mañana', afternoon: 'Tarde', evening: 'Noche', flexible: 'Flexible' };
const COUNTRIES = { ES: 'España', MX: 'México' };
// Shortcuts over the two filters; they use the same list request.
// Each folder holds one recruiter decision, so a decided application leaves the inbox.
const FOLDERS = [
  { decision: 'unreviewed', label: 'Bandeja', empty: 'No hay candidaturas por revisar' },
  { decision: 'advance', label: 'Avanzadas', empty: 'Todavía no hay candidaturas avanzadas' },
  { decision: 'rejected', label: 'Descartadas', empty: 'Todavía no hay candidaturas descartadas' },
];
// Inbox order: what a recruiter can act on first. Within an outcome, the list's newest first.
const OUTCOME_PRIORITY = { qualified: 0, pending_review: 1, incomplete: 2, disqualified: 3 };
const PLACES = { ciudad_de_mexico: 'Ciudad de México' };

async function call(path, options) {
  const response = await fetch(path, options);
  return { ok: response.ok, body: await response.json().catch(() => null) };
}

function formatDate(iso) {
  return new Date(iso).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' });
}

function placeName({ country, locality }) {
  const name = PLACES[locality]
    ?? locality.split('_').map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
  return `${name} (${COUNTRIES[country] ?? country})`;
}

function locationsText({ identified, unresolved }) {
  const parts = identified.map(placeName);
  if (unresolved.length) parts.push(`Por aclarar: ${unresolved.join(', ')}`);
  return parts.join(' · ');
}

function workAreaText(state) {
  if (state.status === 'missing') return NOT_COLLECTED;
  if (state.status === 'unresolved') return `Por aclarar: ${state.raw}`;
  return locationsText(state.normalized);
}

function optionsText({ options, notes }, labels) {
  const parts = [];
  if (options.length) parts.push(options.map((option) => labels[option]).join(', '));
  if (notes) parts.push(`Matices: ${notes}`);
  return parts.join(' · ');
}

function experienceText({ years, platforms }) {
  if (years === 0) return 'Sin experiencia (0 años)';
  const months = Math.round(years * 12);
  let duration;
  if (years < 1) duration = months === 1 ? '1 mes' : `${months} meses`;
  else if (years === 1) duration = '1 año';
  else duration = `${years.toLocaleString('es-ES', { maximumFractionDigits: 1 })} años`;
  return [duration, platforms.join(', ')].filter(Boolean).join(' · ');
}

function startText({ kind, value }) {
  if (kind === 'unknown') return 'Desconocida (lo ha indicado el candidato)';
  if (kind === 'date') return `Fecha: ${value.split('-').reverse().join('/')}`;
  return `${kind === 'period' ? 'Periodo aproximado' : 'Condición'}: ${value}`;
}

const VALUE_TEXT = {
  full_name: (value) => value,
  driving_license: (value) => (value ? 'Sí' : 'No'),
  work_locations: locationsText,
  availability: (value) => optionsText(value, AVAILABILITY),
  preferred_schedule: (value) => optionsText(value, SCHEDULE),
  delivery_experience: experienceText,
  start_timing: startText,
};

function fieldReason(reason) {
  return FIELD_REASONS[reason] ?? NEEDS_REVIEW;
}

function outcomeReason(code) {
  const field = code.endsWith('_unresolved') && FIELDS[code.slice(0, -'_unresolved'.length)];
  return OUTCOME_REASONS[code] ?? (field ? `${field}: por aclarar` : NEEDS_REVIEW);
}

function doubtsOf(fields) {
  const doubts = Object.keys(FIELDS)
    .filter((name) => fields[name].status === 'unresolved')
    .map((name) => `${FIELDS[name]}: ${fieldReason(fields[name].reason)} («${fields[name].raw}»)`);
  const places = fields.work_locations.normalized?.unresolved ?? [];
  return [...doubts, ...places.map((place) => `${FIELDS.work_locations}: cobertura por aclarar («${place}»)`)];
}

function Field({ name, state }) {
  return (
    <div>
      <dt>{FIELDS[name]}</dt>
      <dd className={state.status}>
        {state.status === 'missing' && NOT_COLLECTED}
        {state.status === 'unresolved' && (
          <>
            Por aclarar: {state.raw}
            <small>{fieldReason(state.reason)}</small>
          </>
        )}
        {state.status === 'resolved' && (
          <>
            {VALUE_TEXT[name](state.normalized)}
            <small>Respuesta original: {state.raw}</small>
          </>
        )}
      </dd>
    </div>
  );
}


// Saved summaries are plain "Label: value" lines; any other line is shown whole.
function Summary({ text }) {
  const lines = text.split('\n').filter((line) => line.trim());
  return (
    <div className="summary">
      {lines.map((line, index) => {
        const match = line.match(/^([^:.]{1,40}):\s*(.+)$/);
        return match
          ? <p key={index}><strong>{match[1]}</strong><span>{match[2]}</span></p>
          : <p key={index} className="whole">{line}</p>;
      })}
    </div>
  );
}

export default function Recruiter() {
  const [folderDecision, setFolderDecision] = useState('unreviewed');
  const [items, setItems] = useState(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(null);

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);

  const [saving, setSaving] = useState(false);
  const [attempt, setAttempt] = useState(null);
  const [saveResult, setSaveResult] = useState(null);
  // A save whose result is unknown: no other save until the detail has been read again.
  const [unconfirmed, setUnconfirmed] = useState(null);

  const latestList = useRef(0);
  const latestDetail = useRef(0);
  const current = useRef({});
  current.current = { selectedId };
  const detailPane = useRef(null);
  const listHeading = useRef(null);
  const lastSelected = useRef(null);

  async function loadList() {
    const request = ++latestList.current;
    setListLoading(true);
    setListError(null);
    let notice = NETWORK;
    try {
      // The whole list is read once: the sidebar needs a count for every folder.
      const { ok, body } = await call(API);
      if (request !== latestList.current) return;
      if (ok) {
        setItems(body.items);
        setListLoading(false);
        return;
      }
      notice = body?.notice ?? NETWORK;
    } catch {
      if (request !== latestList.current) return;
    }
    setItems(null);
    setListError(notice);
    setListLoading(false);
  }

  useEffect(() => {
    loadList();
  }, []);

  // The detail, or null if it failed or stopped being the one wanted.
  async function readDetail(id) {
    const request = ++latestDetail.current;
    setDetailLoading(true);
    setDetailError(null);
    let notice = NETWORK;
    try {
      const { ok, body } = await call(`${API}/${encodeURIComponent(id)}`);
      if (request !== latestDetail.current) return null;
      setDetailLoading(false);
      if (ok) return body;
      notice = body?.notice ?? NETWORK;
    } catch {
      if (request !== latestDetail.current) return null;
      setDetailLoading(false);
    }
    setDetailError(notice);
    return null;
  }

  async function openDetail(id) {
    const body = await readDetail(id);
    if (!body) return;
    setDetail(body);
  }

  function select(id) {
    if (id === selectedId) return;
    setSelectedId(id);
    current.current.selectedId = id;
    lastSelected.current = id;
    setDetail(null);
    setSaveResult(null);
    setUnconfirmed(null);
    openDetail(id);
  }

  function closeDetail() {
    latestDetail.current += 1;
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(false);
    setSaveResult(null);
    setUnconfirmed(null);
  }

  useEffect(() => {
    if (selectedId) detailPane.current?.focus();
    else if (lastSelected.current) {
      // The row may have left the list if a saved decision no longer matches the filters.
      (document.getElementById(`row-${lastSelected.current}`) ?? listHeading.current)?.focus();
    }
  }, [selectedId]);

  // A click outside closes the drawer, except on a row, which switches to that application.
  useEffect(() => {
    if (!selectedId) return undefined;
    function closeOnOutsideClick(event) {
      if (detailPane.current?.contains(event.target)) return;
      if (event.target.closest?.('.applications tbody tr')) return;
      lastSelected.current = null; // Focus stays on what was clicked, not on the row.
      closeDetail();
    }
    document.addEventListener('pointerdown', closeOnOutsideClick);
    return () => document.removeEventListener('pointerdown', closeOnOutsideClick);
  }, [selectedId]);

  async function reconcile(id, attempted, failure) {
    const body = await readDetail(id);
    if (!body) return;
    setDetail(body);
    setUnconfirmed(null);
    if (body.recruiter_decision === attempted) {
      setSaveResult({ saved: true, text: SAVED });
      loadList();
    } else {
      setSaveResult({ saved: false, text: failure });
    }
  }

  // Choosing saves. After a failure the select returns to the saved value.
  async function save(attempted) {
    if (decisionBlocked || attempted === detail.recruiter_decision) return;
    const { id } = detail;
    setAttempt(attempted);
    setSaving(true);
    setSaveResult(null);
    let failure = NETWORK;
    try {
      const { ok, body } = await call(`${API}/${encodeURIComponent(id)}/decision`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recruiter_decision: attempted }),
      });
      if (ok) {
        if (current.current.selectedId === id) {
          setDetail((shown) => ({ ...shown, recruiter_decision: body.recruiter_decision }));
          setSaveResult({ saved: true, text: SAVED });
        }
        loadList();
        return;
      }
      failure = body?.notice ?? NETWORK;
    } catch {
      // The save may have been committed even though its response was lost.
    } finally {
      setAttempt(null);
      setSaving(false);
    }
    if (current.current.selectedId !== id) {
      loadList();
      return;
    }
    setUnconfirmed({ attempted, failure });
    await reconcile(id, attempted, failure);
  }

  function retryDetail() {
    if (unconfirmed) reconcile(selectedId, unconfirmed.attempted, unconfirmed.failure);
    else openDetail(selectedId);
  }

  const inFolder = (decision) => (items ?? []).filter((item) => item.recruiter_decision === decision);
  const folder = FOLDERS.find((entry) => entry.decision === folderDecision);
  const visible = inFolder(folderDecision)
    .sort((a, b) => OUTCOME_PRIORITY[a.outcome] - OUTCOME_PRIORITY[b.outcome]);
  const decisionBlocked = saving || detailLoading || Boolean(unconfirmed);
  const summary = detail?.summary ?? (detail?.outcome === 'incomplete'
    ? 'Sin resumen: conversación incompleta'
    : 'Sin resumen');
  const doubts = detail ? doubtsOf(detail.fields) : [];
  const collected = detail
    ? Object.keys(FIELDS).filter((name) => detail.fields[name].status === 'resolved').length
    : 0;
  let listStatus = '';
  if (listLoading) listStatus = 'Cargando candidaturas…';
  else if (items) listStatus = visible.length === 1 ? '1 candidatura' : `${visible.length} candidaturas`;

  return (
    <div className="recruiter">
      <header>
        <div className="brand-block">
          <span className="monogram" aria-hidden="true">GS</span>
          <div>
            <p className="brand">Grupo Sazón</p>
            <p className="role">Panel de selección</p>
          </div>
        </div>
        <a className="switch-view" href="/">Chat del candidato</a>
      </header>

      <div className="inbox">
      <nav className="folders" aria-label="Carpetas">
        {FOLDERS.map((entry) => (
          <button key={entry.decision} type="button"
            aria-current={entry.decision === folderDecision ? 'page' : undefined}
            onClick={() => setFolderDecision(entry.decision)}>
            <span>{entry.label}</span>
            {items && <span>{inFolder(entry.decision).length}</span>}
          </button>
        ))}
      </nav>

      <div className="inbox-main">
      <section className="toolbar">
        <div className="toolbar-title">
          <h1 tabIndex={-1} ref={listHeading}>{folder.label}</h1>
          <p className="status" role="status">{listStatus}</p>
        </div>
        <button type="button" className="secondary" onClick={loadList}>Actualizar</button>
      </section>

      <main className="table-area">
        {listError && (
          <div className="problem table-message">
            <p className="notice" role="alert">{listError}</p>
            <button type="button" onClick={loadList}>Reintentar</button>
          </div>
        )}
        {items && !items.length && !listLoading && (
          <p className="status table-message">Todavía no hay candidaturas</p>
        )}
        {items && items.length > 0 && !visible.length && !listLoading && (
          <p className="status table-message">{folder.empty}</p>
        )}
        {visible.length > 0 && (
          <table className="applications">
            <thead>
              <tr>
                <th scope="col">Candidato</th>
                <th scope="col">Zona de trabajo</th>
                <th scope="col">Screening</th>
                <th scope="col">Decisión</th>
                <th scope="col">Campos recogidos</th>
                <th scope="col">Fecha</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => (
                <tr key={item.id} className={item.id === selectedId ? 'current' : undefined}
                  onClick={() => select(item.id)}>
                  <td>
                    <button type="button" id={`row-${item.id}`}
                      className={`name${item.full_name ? '' : ' unnamed'}`}
                      aria-current={item.id === selectedId ? 'true' : undefined}>
                      {item.full_name ?? NO_NAME}
                    </button>
                  </td>
                  <td className="secondary-cell">{workAreaText(item.work_locations)}</td>
                  <td><span className={`pill ${item.outcome}`}>{OUTCOMES[item.outcome]}</span></td>
                  <td>
                    <span className={`verdict ${item.recruiter_decision}`}>
                      {DECISIONS[item.recruiter_decision]}
                    </span>
                  </td>
                  <td className="secondary-cell">{item.resolved_fields}/7</td>
                  <td className="secondary-cell nowrap">{formatDate(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
      </div>
      </div>

      {selectedId && (
        <aside className="drawer" aria-label="Detalle de la candidatura" tabIndex={-1} ref={detailPane}>
          <div className="drawer-head">
            {detail && (
              <div>
                <h2>{detail.fields.full_name.normalized ?? NO_NAME}</h2>
                <p>{workAreaText(detail.fields.work_locations)} · {formatDate(detail.created_at)}</p>
              </div>
            )}
            <button type="button" className="secondary" onClick={closeDetail}>Cerrar</button>
          </div>

          <div className="drawer-body">
            {detailLoading && <p className="status" role="status">Cargando candidatura…</p>}
            {detailError && (
              <div className="problem">
                <p className="notice" role="alert">{detailError}</p>
                <button type="button" onClick={retryDetail}>Reintentar</button>
              </div>
            )}

            {detail && (
              <>
                <section className="card columns">
                  <div className="outcome-box">
                    <span className="box-label">Resultado del screening</span>
                    <span className={`pill ${detail.outcome}`}>{OUTCOMES[detail.outcome]}</span>
                    {detail.outcome === 'incomplete'
                      ? <p>Conversación sin cerrar</p>
                      : detail.outcome_reasons.map((code) => <p key={code}>{outcomeReason(code)}</p>)}
                  </div>
                  <div className="decision-box">
                    <label>
                      Decisión del recruiter
                      <select value={attempt ?? detail.recruiter_decision} aria-disabled={decisionBlocked}
                        onChange={(event) => save(event.target.value)}>
                        {Object.entries(DECISIONS).map(([key, text]) => (
                          <option key={key} value={key}>{text}</option>
                        ))}
                      </select>
                    </label>
                    {saving &&<p className="status" role="status">Guardando…</p>}
                    {saveResult && (
                      <p className="notice" role={saveResult.saved ? 'status' : 'alert'}>{saveResult.text}</p>
                    )}
                  </div>
                </section>

                <section className="card">
                  <h3>Resumen interno</h3>
                  {detail.summary
                    ? <Summary text={detail.summary} />
                    : <p className="text empty">{summary}</p>}
                </section>

                {(doubts.length > 0 || detail.pending_questions.length > 0) && (
                  <section className="card attention columns">
                    {doubts.length > 0 && (
                      <div>
                        <h3>Dudas sin resolver</h3>
                        <ul>{doubts.map((doubt) => <li key={doubt}>{doubt}</li>)}</ul>
                      </div>
                    )}
                    {detail.pending_questions.length > 0 && (
                      <div>
                        <h3>Consultas sobre el puesto</h3>
                        <ul>
                          {detail.pending_questions.map((question, index) => <li key={index}>{question}</li>)}
                        </ul>
                      </div>
                    )}
                  </section>
                )}

                {!detail.summary && (
                  <section className="card">
                    <h3>Datos recogidos <span>{collected} de 7</span></h3>
                    <dl className="fields">
                      {Object.keys(FIELDS).map((name) => (
                        <Field key={name} name={name} state={detail.fields[name]} />
                      ))}
                    </dl>
                  </section>
                )}

                <section className="card">
                  <h3>Conversación</h3>
                  <ol className="conversation" tabIndex={0} aria-label="Conversación completa">
                    {detail.messages.map((message) => (
                      <li key={message.sequence} className={`turn ${message.role}`}>
                        <small>
                          {message.role === 'candidate' ? 'Candidato' : 'Asistente'} · {formatDate(message.created_at)}
                        </small>
                        <p>{message.text}</p>
                      </li>
                    ))}
                  </ol>
                </section>
              </>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
