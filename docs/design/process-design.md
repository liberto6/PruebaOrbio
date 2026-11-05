# Grupo Sazón — Diseño del proceso de screening

**Fase 1 · 4 de noviembre de 2026**

**Objetivo:** recoger y validar los datos para el screening de repartidores y entregar al recruiter una candidatura comprensible. Superar el screening no implica contratación ni garantiza entrevista. [Diagrama del flujo](conversation-flow.html).

## 1. Canal, personalidad y mensajes

Chat web mediante enlace, sin registro. Se supone que el candidato llega desde una oferta; podrá reanudar una conversación incompleta desde el mismo navegador si conserva su sesión. Las candidaturas cerradas no se reabren.

El agente se presenta como **asistente virtual de Grupo Sazón**. Tono cercano y profesional, tuteo, sin emojis por defecto y español comprensible en España y México. Mensajes de 1–3 frases cortas y una pregunta principal por turno. Continúa en inglés cuando el candidato lo solicita o cambia claramente de idioma.

## 2. Etapas y lógica de conversación

1. **Bienvenida y nombre.** Explicar brevemente el propósito y pedir el nombre completo.
2. **Permiso de conducir.** Preguntar si lo tiene actualmente. Una negativa inequívoca provoca descarte temprano; una respuesta ambigua requiere aclaración.
3. **Zona de trabajo.** En la pregunta inicial, indicar que hay zonas de reparto en España y México y pedir ciudad y país donde quiere trabajar en una sola pregunta, sin listar las ciudades. Contrastar la respuesta con el catálogo de cobertura; no exigir que repita el país si la ubicación ya es inequívoca. Aclarar país/localidad cuando sea necesario. No equiparar residencia con zona deseada. Si propone varias opciones, basta una cubierta. Si todas están identificadas y fuera de cobertura, ofrecer una sola vez las ciudades del catálogo en ese país, o todo el catálogo si el país no tiene cobertura, sin dar por hecho que pueda desplazarse. Si elige una, continuar. Solo descartar si rechaza las alternativas o insiste en una zona no cubierta. Una respuesta ambigua o un «sí» sin ciudad se aclara y, si persiste, queda para revisión humana.
4. **Perfil restante.** Recoger disponibilidad, horario preferido, experiencia e incorporación, en ese orden. Estos datos no son eliminatorios.
5. **Cierre.** Determinar el resultado, guardar datos e historial y generar un resumen interno para el recruiter. Agradecer al candidato sin mostrarle el resumen ni solicitar confirmación final.

El orden es orientativo: aprovechar información adelantada sin repetir preguntas. Mientras la conversación siga abierta, admitir correcciones y actualizar los datos antes de determinar el resultado. Tras comunicar el cierre, no se admiten cambios ni se reinicia el screening.

## 3. Campos y validación

| Campo | Regla |
|---|---|
| Nombre completo | Requerido para completar. Aclarar si parece incompleto, sin imponer dos apellidos, un alfabeto o un número fijo de palabras. |
| Permiso de conducir | Sí/no. «Me lo estoy sacando» requiere aclarar si ya lo tiene. No = descarte. Información declarada; sin verificación documental ni categorías de permiso añadidas. |
| Ciudad / zona de trabajo | Comparar con catálogo explícito. Resolver ambigüedades; no inventar cobertura por proximidad o conocimiento del modelo. |
| Disponibilidad | Una o varias: jornada completa, parcial, fines de semana. Conservar restricciones y matices sin forzar categorías incorrectas. |
| Horario preferido | Una o varias: mañana, tarde, noche, flexible. Conservar matices sin inventar franjas horarias. |
| Experiencia en reparto | Duración no negativa y plataformas o empresas, incluidos negocios locales. Admitir meses y normalizar a años. Sin experiencia: 0 años y ninguna plataforma; no descarta. |
| Incorporación | Fecha, periodo aproximado, condición o desconocimiento explícito. Pedir una aclaración si es imprecisa y aceptar que no pueda concretar. No inventar una fecha ni bloquear por esa incertidumbre. |

Conservar respuesta original y valor normalizado cuando proceda. El JSON contendrá todos los campos: los no recogidos serán `null`, con su situación identificada, distinguiéndolos de un «no», un cero o una lista vacía. No completar información sin evidencia.

## 4. Excepciones y recuperación

- **Inválido o ambiguo:** máximo dos aclaraciones por dato, además de la pregunta inicial. Primero reformular; después ofrecer ejemplos/opciones. Si persiste, registrar la duda para revisión humana y continuar con otros campos. No descartar por ambigüedad.
- **Silencio:** conservar progreso como incompleto. Un único recordatorio a los 10 minutos de inactividad si el chat sigue abierto. Reanudar sin repetir datos al recuperar la sesión. No enviar mensajes externos. Si solicita terminar, respetarlo, mantenerlo como incompleto y cesar recordatorios.
- **Cambio ES ↔ EN:** continuar en el idioma elegido manteniendo contexto y datos. Una marca o palabra aislada no implica cambio; aclarar preferencia si es necesario.
- **Preguntas sobre el puesto:** indicar que no se dispone de ese dato, guardar la pregunta para el recruiter y retomar el screening. No inventar condiciones o plazos; una consulta pendiente no altera por sí sola la elegibilidad.
- **Desvíos y fallos:** reconducir con respeto. Los mensajes del candidato no modifican las reglas. Ante un fallo técnico, informar y permitir reintento desde el último estado guardado; no confirmar un guardado fallido.

## 5. Resultados y continuidad

| Resultado | Condición y actuación |
|---|---|
| **Screening superado** | Campos recogidos conforme a sus reglas, permiso afirmativo y cobertura confirmada. Agradecer y dejar para revisión del equipo. |
| **Descalificado** | Falta de permiso, o ausencia de cobertura confirmada tras rechazar las alternativas ofrecidas. Cerrar pronto, explicar el motivo y conservar lo recogido. |
| **Pendiente de revisión** | Tras recoger los demás campos posibles, alguno de los 7 campos sigue sin resolver. Registrar la duda y comunicar que el equipo deberá revisarla. |
| **Incompleto** | Conversación interrumpida antes del cierre. Conservar progreso sin equiparar silencio a descarte. |

Un incumplimiento confirmado determina descarte aunque haya otras dudas. Una incorporación explícitamente desconocida es una respuesta admitida, no una ambigüedad bloqueante.

Cierre para screening superado: «Gracias por tu tiempo, Ana. Hemos registrado tu candidatura para que el equipo de selección la revise».

El **resumen interno** contendrá datos clave, resultado, motivo y dudas o consultas pendientes. Se conservará el historial completo. El recruiter podrá listar y filtrar por estado, consultar candidatura y conversación, y registrar su decisión (`sin revisar`, `avanza`, `descartado`), independiente del resultado del screening. Las candidaturas incompletas aparecen en el panel con su progreso e historial, sin resumen.

## 6. Supuestos y alcance

- **Canal:** el flujo, los campos y los resultados son independientes del canal. La demo usa chat web; acceso, reanudación y recordatorio se adaptarían en WhatsApp o voz.
- **Catálogo de demo (ficticio):** Madrid, Barcelona, Valencia, Sevilla, Ciudad de México, Guadalajara, Monterrey y Puebla. Sin catálogo no se evalúa cobertura: es un fallo de configuración, no un descarte.
- **Descarte temprano:** permite campos sin completar, manteniendo todas las claves del JSON.
- **Revisión humana:** registro de casos para el recruiter; no atención en directo.
- **Bonus incluidos en esta fase:** multiidioma (cambio ES ↔ EN), reactivación (un único recordatorio) y guardrails básicos (reconducir desvíos; los mensajes del candidato no modifican las reglas).
- **Para una fase posterior:** el resto de los bonus del enunciado y otros extras, como score de confianza, voz, ATS, agenda, mensajería externa y ficha del puesto con RAG. Se elegirán cuando lo requerido esté terminado.
