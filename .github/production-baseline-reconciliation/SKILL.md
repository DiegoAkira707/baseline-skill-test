---
name: production-baseline-reconciliation
description: Reconcile the current Git repository's main branch, or another branch explicitly supplied by the user, with a required production tag and matching commit. Validate that the tag resolves to the supplied commit, review every first-parent commit after production, classify each change as architecture, logic, or uncertain, consult the LT whenever logic exists, wait for a new production tag when the LT confirms deployment, and when the LT rejects deployment show a mandatory reverse proposal, wait for explicit user approval, create the approved reverse branch, push it to GitHub, and leave pull request creation to the user. Use for production-derived migrations, repository onboarding, main baseline checks, undeployed code exclusion, or preserving architecture while reversing application logic.
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
- Optional output root; default to `baseline-analysis/`. Every execution must use a new isolated child run directory.

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
- Never merge or open a pull request. Push only the approved reverse branch after
  the mandatory reverse proposal has been shown and the user has explicitly
  approved it.
- Never select a final candidate while a commit is UNCERTAIN.
- ALWAYS show the complete reverse proposal before creating any reverse branch,
  even when only one logic commit exists or the user has already confirmed
  `LT = NO`. The proposal must show the commits and logic paths to reverse and
  the architecture paths that will remain.
- Never create or push a reverse branch until the user explicitly approves that
  proposal with `OK` or an adjusted final selection.
- Do not create JSON evidence, review, selection, or reverse-result files. Use
  Markdown reports/evidence only.
- Treat every fresh invocation as a new analysis run. Create a unique child directory
  under `baseline-analysis/` and never overwrite, update, or reuse evidence from a
  previous run.
- After inspection, capture the exact `Run directory:` printed by the script as
  `RUN_DIR`. Read and write evidence, patches, reports, and reverse validation only
  inside that `RUN_DIR`. Ignore older sibling directories and legacy files directly
  under `baseline-analysis/`.
- When resuming the same paused LT/reverse-approval conversation, continue using the
  same `RUN_DIR` only while the frozen analyzed HEAD still matches. If the branch
  moved or the skill is started again as a fresh analysis, create a new run directory.
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

The script creates a new directory such as:

```text
baseline-analysis/20260916T203000Z-prod-v1-95bff03a1b2c/
```

and prints it as `Run directory: ...`. Capture that exact path as `RUN_DIR`. Inside
that directory it creates:

- `production-baseline-evidence.md`; and
- one complete patch per post-production commit under `patches/`.

Do not create a JSON evidence file. Never read evidence from another run directory.

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

Record the reviewed classifications, `logic_paths`, and `architecture_paths` in
`<RUN_DIR>/production-baseline-report.md`. Do not create `production-baseline-reviewed.json`
or any other reviewed manifest JSON.

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
2. Save an interim report in `<RUN_DIR>/production-baseline-report.md` with `Estado: AWAITING_LT_RESPONSE` and
   `Partir desde (commit final): PENDIENTE`.
3. Ask the user exactly this, substituting real values:

> Se detectaron cambios de logica posteriores a produccion. Confirma con el LT
>  "Se desplegaran en produccion todos los cambios de logica
> comprendidos hasta el commit `<analyzed_head>`?" Responde SI o NO.

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

### 5. ALWAYS show and obtain approval for the reverse proposal

For an LT response of `NO`, ALWAYS show the reverse proposal before changing
Git. This is a mandatory pause and cannot be skipped, even when there is only
one logic commit or the expected reverse appears obvious.

For each proposed commit show:

- full and short SHA;
- subject and author;
- exact logic paths that will be reversed; and
- exact architecture paths that will remain unchanged.

Also state that the approved branch will be created from the frozen analyzed HEAD,
pushed to `origin`, and that no pull request will be created.

Save/update the interim `<RUN_DIR>/production-baseline-report.md` with
`Estado: AWAITING_REVERSE_APPROVAL`. Ask:

> Propongo hacer reverse de los siguientes commits de logica: `<commit list>`.
> Se revertiran estos paths: `<logic paths>` y se conservaran estos paths de
> arquitectura: `<architecture paths>`. Si confirmas, creare la rama
> `reverse-prd-<short-analyzed-head>` (o una variante unica), la pusheare a GitHub y NO creare
> ningun PR. Confirma `OK`, o indica `QUITAR <sha>` / `AGREGAR <sha>`.

Stop and wait. The user may approve the complete list or request additions and
removals. Only LOGIC commits from the reviewed post-production history may be
selected. If the final list is empty, stop without creating or pushing a branch.

After approval, fetch again and verify that the analyzed HEAD still equals the
frozen `default_head`; rerun if it changed. Do not create any JSON selection file.

### 6. Create and push the reverse branch

Read `references/reverse-plan.md` before invoking the script. Run this step for
both cases below when the LT says `NO` and the user has explicitly approved the
final reverse proposal:

- the post-production history contains only logic; or
- the history contains architecture and logic.

Invoke `scripts/create_reverse_branch.py` with:

- `--approved`;
- the production tag and production commit;
- the frozen analyzed HEAD and analyzed branch;
- one `--reverse-commit <sha>` for every user-approved logic commit;
- one `--logic-path "<sha>::<path>"` for every reviewed logic path in the entire
  post-production history; and
- one `--architecture-path "<sha>::<path>"` for every reviewed architecture path
  in the entire post-production history.

Example:

```bash
python scripts/create_reverse_branch.py \
  --approved \
  --production-tag az0.1.0-43 \
  --production-commit 281a1b4 \
  --default-head <frozen-head> \
  --default-branch main \
  --reverse-commit <logic-commit> \
  --logic-path "<logic-commit>::src/main/java/com/example/Service.java" \
  --architecture-path "<architecture-commit>::.github/workflows/ci.yml" \
  --output <RUN_DIR>/reverse-validation.md
```

Repeat the path arguments as needed. The script validates that every changed path
in every post-production commit has been reviewed before applying the reverse.

The script must:

- create a branch from the frozen analyzed HEAD;
- name it `reverse-prd-<short-analyzed-head>` by default;
- if that branch already exists locally or remotely, append `-2`, `-3`, etc.;
- always generate a new dynamic branch name for every reverse execution;
- reverse only the reviewed logic paths belonging to the user-approved commits;
- preserve reviewed architecture paths, including architecture paths contained
  in a LOGIC commit when they are safely separable;
- create one synthetic reverse commit in a temporary worktree;
- remove the temporary worktree after success;
- push the reverse branch to `origin` and verify the remote SHA;
- create/update `<RUN_DIR>/reverse-validation.md`; and
- never create a pull request.

If the same path is architecture in one commit and logic in another, if one file
contains inseparable architecture and logic, if the analyzed HEAD changes, or if
a reverse patch/push cannot be completed safely, stop as BLOCKED and preserve
Markdown evidence for manual review.

After branch creation and push, verify that the synthetic commit changes only
approved logic paths relative to the frozen analyzed HEAD.

### 7. Produce the report

Read `references/report-template.md`. Write or update
`<RUN_DIR>/production-baseline-report.md`. Never update a report from another run.

Always include:

- production tag, production commit, and frozen analyzed HEAD;
- every post-production commit with final classification and evidence;
- LT consultation, suggested contact, and response;
- the reverse proposal shown to the user and the finally approved commits when applicable;
- actions performed;
- reverse branch name and candidate commit when created;
- exact final starting SHA, clearly labeled as the commit to start from;
- whether the reverse branch was pushed;
- confirmation that no pull request was created; and
- any blocker or uncertainty.

Use one of these final states:

- `OK_ALIGNED`: production already equals the analyzed HEAD.
- `OK_DEFAULT_HEAD`: only architecture exists and the analyzed HEAD is the candidate.
- `AWAITING_LT_RESPONSE`: logic exists and the LT response is pending.
- `AWAITING_PRODUCTION_DEPLOYMENT`: the LT said SI; wait for a new production tag
  and matching commit, then rerun.
- `AWAITING_REVERSE_APPROVAL`: the LT said NO; the mandatory reverse proposal was
  shown and the skill is waiting for user approval or adjustments.
- `OK_REVERSE_BRANCH`: the approved reverse branch was created and pushed; no PR
  was created.
  For this state, `Partir desde (commit final)` MUST be the full candidate SHA created on the reverse branch, never the frozen analyzed HEAD.
- `BLOCKED`: safe automatic selection or reverse is not possible.

Return the compact result, the active `RUN_DIR`, and the report path. Make `Partir desde (commit final)`
immediately visible. Use `PENDIENTE` while waiting for the LT, deployment, or
reverse approval. Never present an abbreviated SHA as the final starting commit;
report the full SHA.
