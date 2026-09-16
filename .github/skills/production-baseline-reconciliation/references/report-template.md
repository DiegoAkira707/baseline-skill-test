# Report Template

Write `production-baseline-report.md` with this compact structure.

```markdown
# Production Baseline Report

## Resultado
- Estado: `<OK_ALIGNED | OK_DEFAULT_HEAD | AWAITING_LT_RESPONSE | AWAITING_PRODUCTION_DEPLOYMENT | AWAITING_REVERSE_APPROVAL | OK_REVERSE_BRANCH | BLOCKED>`
- Repositorio: `<current local Git path>`
- Rama analizada: `<branch>`
- Tag de produccion: `<tag>`
- Commit de produccion: `<full SHA>`
- Referencia de produccion: `<tag> / <commit>`
- HEAD analizado: `<full SHA>`
- Partir desde (commit final): `<full SHA or PENDIENTE>`
- Rama temporal: `<name or NO APLICA>`
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

## Plan de reverse
- Requerido: `<SI/NO>`
- Estado de aprobacion: `<PENDIENTE/APROBADO/NO APLICA>`
- Commits propuestos: `<SHA list or NO APLICA>`
- Commits aprobados: `<SHA list or PENDIENTE/NO APLICA>`
- Ajustes solicitados: `<added/removed SHAs or NINGUNO>`

## Acciones realizadas
- `<inspection, validation, reverse branch creation>`
- Push realizado: `NO`
- Pull request creado: `NO`

## Bloqueos o riesgos
- `<NINGUNO or concise issue>`
```

For `AWAITING_LT_RESPONSE`, `AWAITING_PRODUCTION_DEPLOYMENT`, and
`AWAITING_REVERSE_APPROVAL`, set `Partir desde (commit final)` to `PENDIENTE`,
save the interim report, provide the required instruction, and stop. Update the
same report after the user responds or reruns with the new production tag.
