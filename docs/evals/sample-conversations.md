# Sample conversations

5 actual synthetic conversations with the real model. Dialogue, outcome and summary are copied
by script from the saved run file, with no edits. The candidate messages are fixed eval scripts.
The dialogue is what the candidate sees; the saved result and internal summary are recruiter-side
only and never reach the candidate. `run` is the 0-based index in the file's `runs` list.

Model: `gpt-6-luna`. Prompts hash, the delivered prompts: `fcb5ab222c799356`. Each sample is the first
run of its scenario that passed the runner's checks, not a chosen best one. The runner does not measure
tone; see the [known limits](../planning/e4-5-agent-tone.md#user-decision-closed-with-known-limits).

## Qualified

Source: [`e3-2-20261105T164750Z.json`](e3-2-20261105T164750Z.json), scenario `imprecise_start_then_condition`, run 104. The start answer is imprecise, gets its one clarification and is kept as a condition.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Luis Romero Vega

**Assistant:** ¿Tienes actualmente carnet de conducir?

**Candidate:** Sí, tengo carnet de conducir

**Assistant:** Tenemos zonas de reparto en España y México. ¿En qué ciudad y país te gustaría trabajar?

**Candidate:** En Madrid

**Assistant:** Perfecto. ¿Buscas jornada completa, jornada parcial o trabajar los fines de semana?

**Candidate:** Jornada completa

**Assistant:** Perfecto. ¿Qué horario prefieres: mañana, tarde, noche o flexible?

**Candidate:** Por las mañanas

**Assistant:** Perfecto. ¿Cuánto tiempo has trabajado en reparto y con qué plataformas, empresas o negocios locales? Si no tienes experiencia, también puedes decírmelo.

**Candidate:** Dos años en Glovo

**Assistant:** Perfecto. ¿Cuándo podrías empezar?

**Candidate:** Más adelante

**Assistant:** Entiendo. Me falta una fecha o un periodo aproximado para saber cuándo podrías empezar; también puedes decirme si todavía no lo sabes. ¿Qué plazo tienes en mente?

**Candidate:** Cuando termine mi contrato actual

**Assistant:** Gracias, Luis. Tu candidatura queda registrada para que el equipo de selección la revise.

### Saved result (internal)

Outcome: `qualified`. Reasons: screening_passed.

Internal summary:

```text
Nombre: Luis Romero Vega.
Carnet de conducir: sí.
Lugar de trabajo: Madrid.
Disponibilidad: jornada completa. Horario preferido: mañana.
Experiencia en reparto: 2 años, en Glovo.
Incorporación: cuando termine su contrato actual.
Resultado: Screening superado. Motivo: proceso superado.
Dudas pendientes: ninguna. Preguntas para el equipo: ninguna.
```

## Early rejection

Source: [`e3-2-20261105T164750Z.json`](e3-2-20261105T164750Z.json), scenario `no_license`, run 4. Closes as soon as the licence answer is a clear no.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Carlos Pérez Mora

**Assistant:** ¿Tienes actualmente carnet de conducir?

**Candidate:** No, no tengo carnet

**Assistant:** Gracias, Carlos. Para este puesto hace falta tener carnet de conducir ahora mismo, así que tu candidatura no puede seguir adelante.

### Saved result (internal)

Outcome: `disqualified`. Reasons: no_driving_license.

Internal summary:

```text
Nombre: Carlos Pérez Mora.
Carnet de conducir: No.
Resultado: Descalificado. Motivo: no tiene carnet de conducir.
Pendientes: lugares de trabajo, disponibilidad, horario preferido, experiencia en reparto y fecha de incorporación: no recogido.
Preguntas para el equipo: ninguna.
```

## Covered cities offered

Source: [`e3-2-20261105T164750Z.json`](e3-2-20261105T164750Z.json), scenario `alternatives_chosen_city_qualifies`, run 52. The wanted city is not covered, so the agent offers the covered cities in that country once, and the candidate chooses one.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Luis Romero Vega

**Assistant:** ¿Tienes actualmente carnet de conducir?

**Candidate:** Sí, tengo carnet de conducir

**Assistant:** Tenemos zonas de reparto en España y México. ¿En qué ciudad y país te gustaría trabajar?

**Candidate:** Estoy viviendo en España ahora, me gustaría trabajar en Málaga

**Assistant:** Entiendo; por ahora no tenemos zona de reparto en Málaga. ¿Te gustaría trabajar en Barcelona, Madrid, Sevilla o Valencia?

**Candidate:** Vale, en Sevilla

**Assistant:** Perfecto. ¿Buscas jornada completa, parcial o trabajar los fines de semana?

**Candidate:** Jornada completa

**Assistant:** Perfecto. ¿Prefieres trabajar por la mañana, por la tarde, por la noche o tienes disponibilidad flexible?

**Candidate:** Por las mañanas

**Assistant:** Perfecto. ¿Cuánto tiempo has trabajado en reparto y para qué plataformas o empresas, incluidos negocios locales? Si no tienes experiencia, también puedes decírmelo.

**Candidate:** Dos años en Glovo

**Assistant:** Perfecto. ¿Cuándo podrías empezar?

**Candidate:** Puedo empezar el 1 de diciembre de 2026

**Assistant:** Gracias, Luis. Tu candidatura queda registrada para que el equipo de selección la revise.

### Saved result (internal)

Outcome: `qualified`. Reasons: screening_passed.

Internal summary:

```text
Candidato: Luis Romero Vega.
Carnet de conducir: sí.
Ubicación de trabajo: Sevilla.
Disponibilidad: jornada completa. Horario preferido: mañana.
Experiencia en reparto: 2 años en Glovo.
Inicio: 1 de diciembre de 2026.
Resultado: Screening superado.
Dudas pendientes: ninguna. Preguntas para el equipo: ninguna.
```

## Pending review

Source: [`e3-2-20261105T164750Z.json`](e3-2-20261105T164750Z.json), scenario `guadalajara_unresolved_reaches_review`, run 35. Guadalajara stays ambiguous after two clarifications, the screening continues and the doubt goes to the team.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Luis Romero Vega

**Assistant:** ¿Tienes actualmente carnet de conducir?

**Candidate:** Sí, tengo carnet de conducir

**Assistant:** Tenemos zonas de reparto en España y México. ¿En qué ciudad y país te gustaría trabajar?

**Candidate:** En Guadalajara

**Assistant:** Entiendo. Para concretarlo, ¿te refieres a Guadalajara, España, o a Guadalajara, México?

**Candidate:** Guadalajara, ya te lo he dicho

**Assistant:** Entiendo que quieras evitar repetirlo; aún necesito aclarar a qué Guadalajara te refieres. ¿Te gustaría trabajar en Barcelona, Madrid, Sevilla, Valencia, Ciudad de México, Guadalajara, Monterrey o Puebla?

**Candidate:** Pues eso, Guadalajara

**Assistant:** Entiendo; la ciudad sigue pendiente de aclarar y quedará abierta para el equipo de selección. ¿Buscas jornada completa, parcial o fines de semana?

**Candidate:** Jornada completa

**Assistant:** Perfecto. ¿Prefieres trabajar por la mañana, por la tarde, por la noche o tienes disponibilidad flexible?

**Candidate:** Por las mañanas

**Assistant:** Perfecto. ¿Cuánto tiempo has trabajado en reparto y para qué plataformas, empresas o negocios locales? Si no tienes experiencia, también puedes decírmelo.

**Candidate:** Dos años en Glovo

**Assistant:** Perfecto. ¿Cuándo podrías empezar?

**Candidate:** Puedo empezar el 1 de diciembre de 2026

**Assistant:** Gracias, Luis. El equipo de selección debe revisar tu candidatura porque queda pendiente confirmar algunos detalles.

### Saved result (internal)

Outcome: `pending_review`. Reasons: coverage_unresolved.

Internal summary:

```text
Nombre: Luis Romero Vega.
Carnet de conducir: sí.
Disponibilidad: jornada completa; horario preferido: mañana.
Experiencia en reparto: 2 años en Glovo. Inicio: 1 de diciembre de 2026.
Resultado: Pendiente de revisión. Motivo: cobertura sin confirmar.
Duda pendiente: no se pudo confirmar la cobertura para Guadalajara.
Preguntas para el equipo: ninguna.
```

## Stop and resume

Source: [`e3-2-20261105T164750Z.json`](e3-2-20261105T164750Z.json), scenario `stop_job_question_then_explicit_continuation`, run 95. The candidate stops, asks a job question while stopped and resumes.

### Candidate-visible dialogue

**Assistant:** Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?

**Candidate:** Luis Romero Vega

**Assistant:** ¿Tienes actualmente permiso de conducir?

**Candidate:** Me lo estoy sacando

**Assistant:** Entiendo, lo tienes en trámite. Para este puesto necesitamos saber si ya tienes permiso de conducir actualmente, aunque sea provisional. ¿Ya lo tienes?

**Candidate:** Ya lo tengo, me lo dieron la semana pasada

**Assistant:** Perfecto. Tenemos zonas de reparto en España y México. ¿En qué ciudad y país te gustaría trabajar?

**Candidate:** Tengo que dejarlo por ahora, luego sigo

**Assistant:** Entendido, Luis. Puedes volver cuando quieras y continuar con tu candidatura.

**Candidate:** ¿Cuánto se paga al mes?

**Assistant:** No tengo ese dato y lo dejo anotado para el equipo. Entiendo que quieras dejarlo por ahora, Luis; puedes volver cuando quieras y continuar con tu candidatura.

**Candidate:** Vale, ya puedo seguir

**Assistant:** Perfecto, Luis. Tenemos zonas de reparto en España y México. ¿En qué ciudad y país te gustaría trabajar?

**Candidate:** En Madrid

**Assistant:** Perfecto. ¿Buscas jornada completa, parcial o trabajar los fines de semana?

**Candidate:** Jornada completa

**Assistant:** Perfecto, Luis. ¿Prefieres trabajar por la mañana, por la tarde, por la noche o tienes disponibilidad flexible?

**Candidate:** Por las mañanas

**Assistant:** Perfecto, Luis. ¿Cuánto tiempo has trabajado en reparto y para qué plataformas, empresas o negocios locales? También puedes decirme si no tienes experiencia.

**Candidate:** Dos años en Glovo

**Assistant:** Perfecto, Luis. ¿Cuándo podrías empezar?

**Candidate:** Puedo empezar el 1 de diciembre de 2026

**Assistant:** Gracias, Luis. Tu candidatura ha quedado registrada para que el equipo de selección la revise.

### Saved result (internal)

Outcome: `qualified`. Reasons: screening_passed.

Internal summary:

```text
Nombre: Luis Romero Vega.
Carné de conducir: Sí.
Ubicación: Madrid.
Disponibilidad: jornada completa; horario preferido: mañana.
Experiencia en reparto: 2 años en Glovo.
Inicio: 1 de diciembre de 2026.
Resultado: Screening superado.
Dudas pendientes: ninguna.
Pregunta para el equipo: ¿Cuánto se paga al mes?
```
