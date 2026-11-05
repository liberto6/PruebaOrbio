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
    <div className="field">
      <dt>{FIELDS[name]}</dt>
      <dd>
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

function Filter({ label, value, options, onChange }) {
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Todos</option>
        {Object.entries(options).map(([key, text]) => (
          <option key={key} value={key}>{text}</option>
        ))}
      </select>
    </label>
  );
}

export default function Recruiter() {
  const [outcome, setOutcome] = useState('');
  const [decisionFilter, setDecisionFilter] = useState('');
  const [items, setItems] = useState(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(null);

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);

  const [choice, setChoice] = useState('unreviewed');
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState(null);
  // A save whose result is unknown: no other save until the detail has been read again.
  const [unconfirmed, setUnconfirmed] = useState(null);

  const latestList = useRef(0);
  const latestDetail = useRef(0);
  const current = useRef({});
  current.current = { outcome, decisionFilter, selectedId };
  const detailPane = useRef(null);
  const decisionBox = useRef(null);
  const lastSelected = useRef(null);

  async function loadList() {
    const request = ++latestList.current;
    setListLoading(true);
    setListError(null);
    const params = new URLSearchParams();
    if (current.current.outcome) params.set('outcome', current.current.outcome);
    if (current.current.decisionFilter) params.set('recruiter_decision', current.current.decisionFilter);
    let notice = NETWORK;
    try {
      const { ok, body } = await call(`${API}?${params}`);
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
  }, [outcome, decisionFilter]);

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
    setChoice(body.recruiter_decision);
  }

  function select(id) {
    setSelectedId(id);
    current.current.selectedId = id;
    lastSelected.current = id;
    setDetail(null);
    setSaveResult(null);
    setUnconfirmed(null);
    openDetail(id);
  }

  function backToList() {
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
    else if (lastSelected.current) document.getElementById(`row-${lastSelected.current}`)?.focus();
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

  async function save() {
    const { id } = detail;
    const attempted = choice;
    // The button is about to be disabled: keep the keyboard position in this box.
    decisionBox.current?.focus();
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

  const filtered = Boolean(outcome || decisionFilter);
  const canSave = detail && !saving && !detailLoading && !unconfirmed
    && choice !== detail.recruiter_decision;
  const summary = detail?.summary ?? (detail?.outcome === 'incomplete'
    ? 'Sin resumen: conversación incompleta'
    : 'Sin resumen');
  const doubts = detail ? doubtsOf(detail.fields) : [];

  return (
    <div className={`recruiter${selectedId ? ' has-selection' : ''}`}>
      <header>
        <div>
          <p className="brand">Grupo Sazón</p>
          <p className="role">Panel de selección</p>
        </div>
        <a className="switch-view" href="/">Chat del candidato</a>
      </header>

      <main className="workspace">
        <section className="pane list-pane" aria-label="Candidaturas">
          <div className="filters">
            <Filter label="Resultado del screening" value={outcome} options={OUTCOMES} onChange={setOutcome} />
            <Filter label="Decisión del recruiter" value={decisionFilter} options={DECISIONS}
              onChange={setDecisionFilter} />
            <button type="button" className="secondary" onClick={loadList}>Actualizar</button>
          </div>

          {listLoading && <p className="status" role="status">Cargando candidaturas…</p>}
          {listError && (
            <div className="problem">
              <p className="notice" role="alert">{listError}</p>
              <button type="button" onClick={loadList}>Reintentar</button>
            </div>
          )}
          {items && !items.length && !listLoading && (
            <p className="status">
              {filtered ? 'Ninguna candidatura coincide con estos filtros' : 'Todavía no hay candidaturas'}
            </p>
          )}
          {items && (
            <ul className="applications">
              {items.map((item) => (
                <li key={item.id}>
                  <button type="button" id={`row-${item.id}`} className="application"
                    aria-current={item.id === selectedId ? 'true' : undefined}
                    onClick={() => select(item.id)}>
                    <strong>{item.full_name ?? NO_NAME}</strong>
                    <span>{workAreaText(item.work_locations)}</span>
                    <span>{formatDate(item.created_at)} · {item.resolved_fields}/7 campos recogidos</span>
                    <span>Screening: {OUTCOMES[item.outcome]}</span>
                    <span>Decisión: {DECISIONS[item.recruiter_decision]}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="pane detail-pane" aria-label="Detalle de la candidatura" tabIndex={-1} ref={detailPane}>
          {!selectedId && <p className="status">Selecciona una candidatura para ver su detalle.</p>}
          {selectedId && (
            <button type="button" className="secondary back" onClick={backToList}>Volver al listado</button>
          )}
          {detailLoading && <p className="status" role="status">Cargando candidatura…</p>}
          {detailError && (
            <div className="problem">
              <p className="notice" role="alert">{detailError}</p>
              <button type="button" onClick={retryDetail}>Reintentar</button>
            </div>
          )}

          {detail && (
            <>
              <h1>{detail.fields.full_name.normalized ?? NO_NAME}</h1>
              <p className="status">Creada el {formatDate(detail.created_at)}</p>

              <div className="decision" tabIndex={-1} ref={decisionBox}>
                <p><strong>Decisión guardada: {DECISIONS[detail.recruiter_decision]}</strong></p>
                <label>
                  Decisión del recruiter
                  <select value={choice} disabled={saving}
                    onChange={(event) => {
                      setChoice(event.target.value);
                      setSaveResult(null);
                    }}>
                    {Object.entries(DECISIONS).map(([key, text]) => (
                      <option key={key} value={key}>{text}</option>
                    ))}
                  </select>
                </label>
                <button type="button" onClick={save} disabled={!canSave}>Guardar decisión</button>
                {saving && <p className="status" role="status">Guardando…</p>}
                {saveResult && (
                  <p className="notice" role={saveResult.saved ? 'status' : 'alert'}>{saveResult.text}</p>
                )}
              </div>

              <h2>Datos recogidos</h2>
              <dl>
                {Object.keys(FIELDS).map((name) => (
                  <Field key={name} name={name} state={detail.fields[name]} />
                ))}
              </dl>

              <h2>Resultado del screening</h2>
              <p>{OUTCOMES[detail.outcome]}</p>
              {detail.outcome === 'incomplete'
                ? <p className="status">Conversación sin cerrar</p>
                : (
                  <ul>
                    {detail.outcome_reasons.map((code) => <li key={code}>{outcomeReason(code)}</li>)}
                  </ul>
                )}

              <h2>Resumen interno</h2>
              <p className="text">{summary}</p>

              <h2>Dudas sin resolver</h2>
              {doubts.length
                ? <ul>{doubts.map((doubt) => <li key={doubt}>{doubt}</li>)}</ul>
                : <p className="status">Sin dudas pendientes</p>}

              <h2>Consultas sobre el puesto</h2>
              {detail.pending_questions.length
                ? <ul>{detail.pending_questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
                : <p className="status">Sin consultas</p>}

              <h2>Conversación</h2>
              <ol className="conversation">
                {detail.messages.map((message) => (
                  <li key={message.sequence} className={`message ${message.role}`}>
                    <small>
                      {message.role === 'candidate' ? 'Candidato' : 'Asistente'} · {formatDate(message.created_at)}
                    </small>
                    {message.text}
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
