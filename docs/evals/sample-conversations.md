# Sample conversations

Four actual synthetic conversations with the real model. Dialogue, outcome and summary are copied
by script from the saved run files, with no edits. The candidate messages are fixed eval scripts.
The dialogue is what the candidate sees; the saved result and internal summary are recruiter-side
only and never reach the candidate. `run` is the 0-based index in the file's `runs` list.

Model: `gpt-6-luna`. E3.4 prompts hash for these runs: `fce960c48adb8f67`. The final prompts `7cd32219725ef7e1` differ only in how a bare acceptance right after a reminder is read, which these conversations do not contain.

## Qualified

Source: [`e3-2-20261105T104154Z.json`](e3-2-20261105T104154Z.json), scenario `imprecise_start_then_condition`, run 33. The start answer is imprecise, gets its one clarification and is kept as a condition.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Luis Romero Vega

**Assistant:** ¿Tienes actualmente una licencia de conducir?

**Candidate:** Sí, tengo carnet de conducir

**Assistant:** ¿En qué ciudad te gustaría trabajar?

**Candidate:** En Madrid

**Assistant:** ¿Tienes disponibilidad para trabajar a tiempo completo, a tiempo parcial o los fines de semana?

**Candidate:** Jornada completa

**Assistant:** ¿Qué horario prefieres: mañana, tarde, noche o tienes flexibilidad?

**Candidate:** Por las mañanas

**Assistant:** ¿Cuánto tiempo has trabajado en reparto y para qué plataformas, empresas o negocios locales? Si no tienes experiencia, también puedes decírmelo.

**Candidate:** Dos años en Glovo

**Assistant:** Luis, ¿cuándo podrías empezar a trabajar?

**Candidate:** Más adelante

**Assistant:** ¿Podrías indicar aproximadamente cuándo podrías empezar? También está bien si todavía no puedes saberlo.

**Candidate:** Cuando termine mi contrato actual

**Assistant:** Gracias, Luis. Tu solicitud queda registrada para que el equipo de selección la revise.

### Saved result (internal)

Outcome: `qualified`. Reasons: screening_passed.

Internal summary:

```text
Nombre: Luis Romero Vega.
Carnet de conducir: sí.
Zona de trabajo: Madrid.
Disponibilidad: jornada completa. Horario preferido: mañana.
Experiencia en reparto: 2 años en Glovo.
Inicio: cuando termine su contrato actual.
Resultado: Screening superado.
Dudas pendientes: ninguna. Preguntas para el equipo: ninguna.
```

## Early rejection

Source: [`e3-2-20261105T052616Z.json`](e3-2-20261105T052616Z.json), scenario `no_license`, run 4. Closes as soon as the licence answer is a clear no. This run used the E3.2 prompts (hash `b3292b70208a07a9`), before the two E3.4 prompt changes; the case was not rerun.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Carlos Pérez Mora

**Assistant:** ¿Tienes actualmente licencia de conducir?

**Candidate:** No, no tengo carnet

**Assistant:** Gracias, Carlos. Por ahora no podemos continuar con tu candidatura, ya que el puesto requiere tener licencia de conducir.

### Saved result (internal)

Outcome: `disqualified`. Reasons: no_driving_license.

Internal summary:

```text
Nombre: Carlos Pérez Mora
Carnet de conducir: no
Resultado: Descalificado
Motivo: no tiene carnet de conducir
Pendiente: lugares de trabajo, no recogido; disponibilidad, no recogido; horario preferido, no recogido; experiencia en reparto, no recogido; fecha de inicio, no recogido
Preguntas para el equipo: ninguna
```

## Pending review

Source: [`e3-2-20261105T104154Z.json`](e3-2-20261105T104154Z.json), scenario `guadalajara_unresolved_reaches_review`, run 3. Guadalajara stays ambiguous after the two clarifications, so the screening continues and closes for review.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Luis Romero Vega

**Assistant:** ¿Tienes actualmente licencia de conducir?

**Candidate:** Sí, tengo carnet de conducir

**Assistant:** ¿En qué ciudad te gustaría trabajar?

**Candidate:** En Guadalajara

**Assistant:** ¿Te refieres a Guadalajara, España o a Guadalajara, México?

**Candidate:** Guadalajara, ya te lo he dicho

**Assistant:** Entiendo, Luis. Para confirmar la ciudad, ¿te refieres a Guadalajara, España, o Guadalajara, México? También puedes elegir entre Barcelona, Madrid, Sevilla, Valencia, Ciudad de México, Monterrey y Puebla.

**Candidate:** Pues eso, Guadalajara

**Assistant:** Gracias, Luis. ¿Qué disponibilidad tendrías: tiempo completo, medio tiempo, fines de semana o una combinación?

**Candidate:** Jornada completa

**Assistant:** ¿Qué horario prefieres: mañana, tarde, noche o flexible?

**Candidate:** Por las mañanas

**Assistant:** ¿Cuánto tiempo has trabajado en reparto y para qué plataformas, empresas o negocios locales? Si no tienes experiencia, también puedes decírmelo.

**Candidate:** Dos años en Glovo

**Assistant:** ¿Cuándo podrías empezar a trabajar, Luis?

**Candidate:** Puedo empezar el 1 de diciembre de 2026

**Assistant:** Gracias, Luis. El equipo de selección debe revisar tu solicitud porque aún hay algunos detalles pendientes.

### Saved result (internal)

Outcome: `pending_review`. Reasons: coverage_unresolved.

Internal summary:

```text
Nombre: Luis Romero Vega.
Carnet de conducir: sí.
Disponibilidad: jornada completa; horario preferido: mañana.
Experiencia en reparto: 2 años en Glovo.
Inicio: 1 de diciembre de 2026.
Resultado: Pendiente de revisión; no se pudo confirmar cobertura en Guadalajara.
Dudas pendientes: cobertura en Guadalajara no confirmada.
Preguntas para el equipo: ninguna.
```

## Stop and resume

Source: [`e3-2-20261105T104154Z.json`](e3-2-20261105T104154Z.json), scenario `stop_job_question_then_explicit_continuation`, run 24. The candidate stops, asks a job question while stopped and then continues explicitly.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Luis Romero Vega

**Assistant:** ¿Tienes actualmente permiso de conducir?

**Candidate:** Me lo estoy sacando

**Assistant:** Gracias por aclararlo, Luis. ¿Tienes ya el permiso de conducir o todavía no lo tienes?

**Candidate:** Ya lo tengo, me lo dieron la semana pasada

**Assistant:** ¿En qué ciudad te gustaría trabajar?

**Candidate:** Tengo que dejarlo por ahora, luego sigo

**Assistant:** Entendido, Luis. Puedes volver cuando quieras y continuar con tu solicitud.

**Candidate:** ¿Cuánto se paga al mes?

**Assistant:** Entiendo, Luis. No tengo esa información y he anotado tu pregunta para el equipo. Puedes volver cuando quieras y continuar con tu solicitud.

**Candidate:** Vale, ya puedo seguir

**Assistant:** ¿En qué ciudad te gustaría trabajar?

**Candidate:** En Madrid

**Assistant:** ¿Tienes disponibilidad a tiempo completo, a tiempo parcial o los fines de semana? Puedes indicarme más de una opción.

**Candidate:** Jornada completa

**Assistant:** ¿En qué horario prefieres trabajar: por la mañana, por la tarde, por la noche o tienes disponibilidad flexible?

**Candidate:** Por las mañanas

**Assistant:** ¿Cuánto tiempo has trabajado en reparto y para qué plataformas, empresas o negocios locales? Si no tienes experiencia, también puedes decírmelo.

**Candidate:** Dos años en Glovo

**Assistant:** ¿Cuándo podrías empezar? Puedes darme un periodo aproximado o decirme si todavía no lo sabes.

**Candidate:** Puedo empezar el 1 de diciembre de 2026

**Assistant:** Gracias, Luis. Tu solicitud ha quedado registrada para que el equipo de selección la revise.

### Saved result (internal)

Outcome: `qualified`. Reasons: screening_passed.

Internal summary:

```text
Nombre: Luis Romero Vega.
Permiso de conducir: sí. Ubicación: Madrid.
Disponibilidad: jornada completa. Horario preferido: mañana.
Experiencia en reparto: 2 años en Glovo. Inicio: 1 de diciembre de 2026.
Resultado: Screening superado.
Dudas pendientes: ninguna.
Preguntas para el equipo: ¿Cuánto se paga al mes?
```
