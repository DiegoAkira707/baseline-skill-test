---
name: production-baseline-reconciliation
description: Reconcile the current Git repository's main branch, or another branch explicitly supplied by the user, with a required production tag and matching commit. Validate that the tag resolves to the supplied commit, review every first-parent commit after production, classify each change as architecture, logic, or uncertain, consult the LT whenever logic exists, wait for a new production tag when the LT confirms deployment, and when the LT rejects deployment prepare an approved local reverse branch without pushing or opening a pull request. Use for production-derived migrations, repository onboarding, main baseline checks, undeployed code exclusion, or preserving architecture while reversing application logic.
---

# Production Baseline Reconciliation

## Purpose

Determine the exact Git commit that may be used as the next baseline when the
GitHub branch can contain changes that are not deployed in production. Use
`main` by default unless the user explicitly names another branch. Require both
the production tag and its matching commit from the user. Operate only on the
Git repository where the current session is positioned.

## Required input

Collect these values before running:

- `production_tag`: exact Git tag supplied by the user, for example `az0.1.0-43`.
- `production_commit`: full SHA or unambiguous short SHA associated with that tag,
  for example `281a1b4`.
- Optional branch override; use `main` when the user does not specify one.
- Optional output directory; default to `baseline-analysis/`.

Ask only for a missing production tag or production commit. Do not ask for a
repository path or `owner/repo`; use the Git repository containing the current
working directory.

## Hard constraints

- Treat the user-supplied production tag + commit pair as the source of truth.
- Require both values. Resolve the exact tag under `refs/tags/` and verify that it
  points to the supplied commit before any analysis. Block on any mismatch.
- Never infer the production tag or commit from Azure DevOps, releases, dates, or
  commit messages.
- Operate only on the repository where the current session is positioned; never
  switch to, clone, or infer another repository.
- Use `main` as the default branch. Use another branch only when the user
  explicitly supplies it; never auto-discover or infer a replacement branch.
- Analyze first-parent history so each change is evaluated as it landed on the
  analyzed branch.
- Review the full diff of every post-production commit. Never trust only a path
  heuristic or commit message.
- Never modify the analyzed branch directly.
- Never push, merge, or open a pull request.
- Never select a final candidate while a commit is UNCERTAIN.
- Never create a reverse branch before showing the proposed reverse commits and
  receiving explicit user approval.
- Use the frozen analyzed HEAD. If it moves before completion, rerun the analysis.

## Workflow

### 1. Prepare and inspect

Verify `git` is installed and the repository is accessible. Fetch the current
remote state when credentials allow it.

Run:

```bash
python scripts/inspect_production_baseline.py \
  --production-tag <user-supplied-tag> \
  --production-commit <user-supplied-commit> \
  --fetch \
  --output-dir baseline-analysis
```

Omit `--default-branch` to analyze `main`. Add
`--default-branch <user-supplied-name>` only when the user explicitly requests
another branch.

Stop and report BLOCKED when:

- the production tag does not exist;
- the supplied production commit does not resolve;
- the production tag does not resolve to the supplied production commit;
- production is not an ancestor of the analyzed HEAD;
- production is not on the analyzed first-parent chain; or
- repository access/fetch fails and current refs cannot be verified.

The script creates:

- `production-baseline-evidence.json`;
- `production-baseline-evidence.md`; and
- one complete patch per post-production commit under `patches/`.

### 2. Classify every commit

Read `references/classification-rules.md` before classifying.

For each commit in chronological first-parent order:

1. Inspect metadata and every changed path.
2. Read the complete patch.
3. Assign every changed path exactly once to architecture or logic.
4. Classify the commit as `ARCHITECTURE`, `LOGIC`, or `UNCERTAIN`. If a
   commit contains separable architecture and logic paths, classify it as
   `LOGIC` and preserve both path groups so a later reverse removes only logic.
5. Record a concise diff-based reason. Use `UNCERTAIN` only after reviewing the
   full diff and only when a safe classification or separation cannot be proven.

Treat `.github/workflows/**` and CODEOWNERS changes like the provided examples
as architecture unless the actual diff mixes application source/runtime logic
that cannot be separated safely. Treat Java and other application source,
dependencies, runtime configuration, migrations, and deploy/runtime manifests
as logic.

Create `production-baseline-reviewed.json` using
`references/manifest-schema.md`. Include every post-production commit in exact
order, including every changed path. Do not create a manifest from heuristic
hints alone.

### 3. Apply the initial decision rules

Read `references/decision-matrix.md` and use these outcomes:

- No post-production commits: use the production commit.
- Architecture only: use the analyzed HEAD, which is the last commit of `main`
  or of the explicitly supplied branch.
- Any LOGIC commit: pause for LT confirmation before selecting a candidate or
  creating a branch.
- Any UNCERTAIN item or unsafe same-file mixture: stop for manual review.


### 4. Pause for the LT decision

When at least one LOGIC commit exists:

1. Identify the author of the newest logic-bearing commit.
2. Save an interim report with `Estado: AWAITING_LT_RESPONSE` and
   `Partir desde (commit final): PENDIENTE`.
3. Ask the user exactly this, substituting real values:

> Se detectaron cambios de logica posteriores a produccion. Confirma con el LT
> o con `<author>`: "Se desplegaran en produccion todos los cambios de logica
> comprendidos hasta el commit `<analyzed_head>`?" Responde SI, NO o PARCIAL.

4. Stop and wait for the user. Do not take Git actions.

On resume, fetch and verify that the analyzed branch still equals the frozen
`default_head`. Rerun everything if it changed.

Interpret the response strictly:

- `SI`, explicitly covering every logic change through `default_head`: do not use
  `default_head` yet. Set `Estado: AWAITING_PRODUCTION_DEPLOYMENT`, tell the user
  to wait until the LT deploys the changes and a new production tag + matching
  commit exists, then rerun the skill with that new pair. Do not create a branch
  and do not publish a final candidate.
- `NO`: prepare a reverse proposal covering every LOGIC commit by default, then
  continue to the mandatory user approval gate below.
- `PARCIAL` or ambiguous: stop and request the exact commits that will and will
  not be deployed. Do not guess or create a branch.

When the LT eventually deploys the changes, the new production tag should point
to the deployed commit. On rerun, if that commit is also the analyzed HEAD, the
workflow ends as `OK_ALIGNED`.

### 5. Obtain approval for the reverse plan

For an LT response of `NO`, list every proposed reverse commit before changing
Git. For each commit show:

- full and short SHA;
- subject and author;
- logic paths that will be reversed; and
- architecture paths that will remain unchanged.

Save the interim report with `Estado: AWAITING_REVERSE_APPROVAL`. Ask:

> Propongo hacer reverse de los siguientes commits de logica en una rama local:
> `<commit list>`. Confirma `OK`, o indica `QUITAR <sha>` / `AGREGAR <sha>`.

Stop and wait. The user may approve the complete list or request additions and
removals. Only LOGIC commits from the reviewed post-production history may be
selected. If the final list is empty, stop without creating a branch.

After approval, create `baseline-analysis/reverse-selection.json` using the
shape in `references/manifest-schema.md`. Fetch again and verify that the
analyzed HEAD still equals the frozen `default_head`; rerun if it changed.

### 6. Create the reverse branch

Run this step for both cases below when the LT says `NO` and the user approves
the reverse list:

- the post-production history contains only logic; or
- the history contains architecture and logic.

Run:

```bash
python scripts/create_reverse_branch.py \
  --manifest baseline-analysis/production-baseline-reviewed.json \
  --selection baseline-analysis/reverse-selection.json \
  --output baseline-analysis/reverse-branch-result.json
```

The script must:

- create a local branch from the frozen analyzed HEAD;
- name it `reverse-prd-<normalized-production-tag>` by default and append a UTC
  timestamp when that name already exists;
- reverse only the reviewed logic paths belonging to the user-approved commits;
- preserve reviewed architecture paths, including architecture paths contained
  in a LOGIC commit when they are safely separable;
- create one local synthetic reverse commit in a temporary worktree;
- remove the temporary worktree after success;
- leave the local branch ready for manual review and a user-created pull request;
- perform no push and no pull request.

If the same path is architecture in one commit and logic in another, if one file
contains inseparable architecture and logic, or if a reverse patch cannot be
applied safely, stop as BLOCKED and preserve evidence for manual review.

After branch creation, verify that the synthetic commit changes only approved
logic paths relative to the frozen analyzed HEAD.

### 7. Produce the report

Read `references/report-template.md`. Write or update
`production-baseline-report.md` in the selected output directory.

Always include:

- production tag, production commit, and frozen analyzed HEAD;
- every post-production commit with final classification and evidence;
- LT consultation, suggested contact, and response;
- proposed and finally approved reverse commits when applicable;
- actions performed;
- temporary reverse branch when created;
- exact final starting SHA, clearly labeled as the commit to start from;
- confirmation that no push or pull request was performed; and
- any blocker or uncertainty.

Use one of these final states:

- `OK_ALIGNED`: production already equals the analyzed HEAD.
- `OK_DEFAULT_HEAD`: only architecture exists and the analyzed HEAD is the candidate.
- `AWAITING_LT_RESPONSE`: logic exists and the LT response is pending.
- `AWAITING_PRODUCTION_DEPLOYMENT`: the LT said SI; wait for a new production tag
  and matching commit, then rerun.
- `AWAITING_REVERSE_APPROVAL`: the LT said NO; show the proposed reverse list and
  wait for user approval or adjustments.
- `OK_REVERSE_BRANCH`: the approved local reverse branch was created.
- `BLOCKED`: safe automatic selection or reverse is not possible.

Return the compact result and the report path. Make `Partir desde (commit final)`
immediately visible. Use `PENDIENTE` while waiting for the LT, deployment, or
reverse approval. Never present an abbreviated SHA as the final starting commit;
report the full SHA.
