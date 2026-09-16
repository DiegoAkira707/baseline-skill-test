# Decision Matrix

## Definitions

- `production_tag`: the exact user-supplied Git tag used for production.
- `production`: the exact user-supplied commit after verifying that
  `production_tag` resolves to it.
- `default_head`: the frozen head of `main`, unless the user explicitly named
  another branch.
- `logic-bearing commit`: a commit classified LOGIC.
- `candidate`: the exact commit from which the next branch/process must start.

Analyze `main` by default and use another branch only when the user explicitly
provides it. Always use the frozen `default_head`. If that branch moves before
completion, rerun the analysis.

## Outcomes

| Situation | Required action | Candidate |
|---|---|---|
| No commits after production | No mutation | `production` |
| Architecture commits only | Use the last analyzed commit of `main` or the explicit branch | `default_head` |
| Any logic exists and LT has not answered | Pause and consult the LT | None |
| LT says YES for every logic change through `default_head` | Wait until the changes are actually deployed and a new production tag + matching commit exists; then rerun | None |
| LT says NO and history contains only logic | Show the proposed logic commits, obtain user approval, then create a local reverse branch from `default_head` | New reverse branch HEAD |
| LT says NO and history contains architecture plus logic | Show the proposed logic commits, obtain user approval, then create a local reverse branch from `default_head` that reverses logic and preserves architecture | New reverse branch HEAD |
| LT answer is partial or ambiguous | Stop and request the exact commits that will and will not be deployed | None |
| Reverse list is not yet approved | Stop after showing the proposed commits and paths | None |
| Any UNCERTAIN commit, divergent history, unsafe same-file mixture, or failed reverse patch | Stop for manual review | None |


## LT pause

When logic exists, identify the author of the newest logic-bearing commit and
ask the user to consult the LT or that developer. Use this question:

> Se detectaron cambios de logica posteriores a produccion. Confirma con el LT
> o con `<author>`: "Se desplegaran en produccion todos los cambios de logica
> comprendidos hasta el commit `<default_head>`?" Responde SI, NO o PARCIAL.

After asking, stop. Do not create branches, select a final candidate, push, or
open a pull request.

An explicit SI means wait for the real deployment and a new production tag; it
does not authorize using `default_head` immediately. An explicit NO activates
the reverse proposal and approval flow. PARCIAL requires exact commit scope.

## Reverse approval pause

For an LT response of NO, propose every LOGIC commit by default and show its
logic paths. Ask the user to respond with `OK`, `QUITAR <sha>`, or
`AGREGAR <sha>`. Create no branch until the final list is explicitly approved.
