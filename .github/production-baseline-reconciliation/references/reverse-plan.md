# Reverse Plan Contract

Use this reference only after `LT = NO` and after the mandatory reverse proposal
has been shown to the user and explicitly approved.

## No JSON intermediates

Do not create manifest, selection, evidence-result, or reverse-result JSON files.
Keep reviewed classifications in `<RUN_DIR>/production-baseline-report.md` and pass the
approved plan directly to `scripts/create_reverse_branch.py`.

## Required script inputs

Pass:

- `--approved` only after explicit user approval.
- `--production-tag <tag>` and `--production-commit <sha>`.
- `--default-head <full frozen SHA>` and `--default-branch <branch>`.
- Repeat `--reverse-commit <sha>` for every approved LOGIC commit.
- Repeat `--logic-path "<sha>::<path>"` for every reviewed logic path in every
  post-production commit.
- Repeat `--architecture-path "<sha>::<path>"` for every reviewed architecture
  path in every post-production commit.
- Use `--output <RUN_DIR>/reverse-validation.md`.

The script requires every changed path from every post-production commit to be
classified through those path arguments. This preserves the same safety gate that
a reviewed manifest provided without writing JSON files.


## Run isolation

Use only the active `RUN_DIR` created by the current inspection. Never read or
write reverse evidence in a previous run directory, and never use legacy files
directly under `baseline-analysis/`. If the analyzed branch moved, start a fresh
analysis and use its new `RUN_DIR`.

## Mandatory proposal gate

Before invoking the script, ALWAYS show:

1. each proposed reverse commit;
2. its subject and author;
3. the exact logic paths that will be reversed;
4. the exact architecture paths that will remain;
5. the proposed dynamic reverse branch naming pattern `reverse-prd-<tag>-<UTC timestamp>`;
6. that the branch will be pushed to `origin`; and
7. that no pull request will be created.

Then stop and wait for `OK`, `QUITAR <sha>`, or `AGREGAR <sha>`.

## Expected result

After approval, the script:

- creates the reverse branch from the frozen analyzed HEAD;
- reverses only approved logic paths;
- preserves architecture paths;
- creates one synthetic reverse commit;
- pushes the branch to `origin`;
- verifies the remote branch SHA;
- writes `<RUN_DIR>/reverse-validation.md`; and
- does not create a pull request.
