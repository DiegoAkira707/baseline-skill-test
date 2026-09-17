# Report Template

Write `<RUN_DIR>/production-baseline-report.md` with this compact structure. Never reuse or update a report from another run directory.

```markdown
# Production Baseline Report

## Resultado
- Estado: `<OK_ALIGNED | OK_DEFAULT_HEAD | AWAITING_LT_RESPONSE | AWAITING_PRODUCTION_DEPLOYMENT | AWAITING_REVERSE_APPROVAL | OK_REVERSE_BRANCH | BLOCKED>`
- Repositorio: `<current local Git path>`
- Ejecucion: `<RUN_DIR>`
- Rama analizada: `<branch>`
- Tag de produccion: `<tag>`
- Commit de produccion: `<full SHA>`
- Referencia de produccion: `<tag> / <commit>`
- HEAD analizado: `<full SHA>`
- Partir desde (commit final): `<full SHA or PENDIENTE>`
- Rama reverse: `<name or NO APLICA>`
- Decision: `<one concise sentence>`

## Commits posteriores a produccion
| # | Commit | Fecha | Autor | Clasificacion | Evidencia breve |
|---:|---|---|---|---|---|
| 1 | `<sha>` | `<date>` | `<author>` | `<ARQUITECTURA/LOGICA/INCIERTO>` | `<files and reason>` |

## Consulta al LT
- Requerida: `<SI/NO>`
- Contacto sugerido: `<latest logic author or NO APLICA>`
- Respuesta: `<SI/NO/PARCIAL/PENDIENTE/NO APLICA>`
- Resultado: `<esperar despliegue / preparar reverse / no aplica>`

## Propuesta y plan de reverse
- Requerido: `<SI/NO>`
- Propuesta mostrada al usuario: `<SI/NO>`
- Estado de aprobacion: `<PENDIENTE/APROBADO/NO APLICA>`
- Commits propuestos: `<SHA list or NO APLICA>`
- Paths de logica propuestos: `<paths or NO APLICA>`
- Paths de arquitectura a preservar: `<paths or NO APLICA>`
- Commits aprobados: `<SHA list or PENDIENTE/NO APLICA>`
- Ajustes solicitados: `<added/removed SHAs or NINGUNO>`

## Acciones realizadas
- `<inspection, validation, reverse branch creation>`
- Push realizado: `<SI/NO/NO APLICA>`
- Pull request creado: `NO`

## Bloqueos o riesgos
- `<NINGUNO or concise issue>`
```

For `AWAITING_LT_RESPONSE`, `AWAITING_PRODUCTION_DEPLOYMENT`, and
`AWAITING_REVERSE_APPROVAL`, set `Partir desde (commit final)` to `PENDIENTE`,
save the interim report, provide the required instruction, and stop. Update the
same report only when resuming the same paused run. A fresh skill execution or a
changed analyzed HEAD must create a new `RUN_DIR` and a new report.

For `OK_REVERSE_BRANCH`, record `Push realizado: SI`, the pushed branch name,
and `Pull request creado: NO`.

For `OK_REVERSE_BRANCH`, `Partir desde (commit final)` MUST equal the full
`Commit candidato` generated on the reverse branch. Never report the original
frozen analyzed HEAD as the final starting commit after a successful reverse.
