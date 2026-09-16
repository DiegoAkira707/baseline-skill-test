# Classification Rules

## Objective

Classify every first-parent commit after the production commit. Review the full
diff. Do not classify from the commit message, file extension, author, or path
alone.

For this workflow, "architecture" means repository, delivery, governance, or
non-runtime structure that can be carried forward without importing undeployed
application behavior.

## Final values

### ARCHITECTURE

Use only when all effective changes are non-application changes such as:

- `.github/workflows/**` and reusable GitHub Actions.
- `.github/CODEOWNERS`, root `CODEOWNERS`, or `docs/CODEOWNERS`.
- Pull request templates, issue templates, Dependabot metadata, labels, and
  repository governance files.
- CI/CD orchestration files such as Azure Pipelines, Jenkins, GitLab CI, or
  CircleCI definitions.
- Backstage or documentation scaffolding such as `catalog-info.yaml`,
  `mkdocs.yml`, `docs/**`, and README changes that do not alter runtime logic.

A workflow file remains architecture for this process, but mark it UNCERTAIN if
it embeds or generates application source, rewrites application files, or mixes
runtime logic that cannot be separated safely.

### LOGIC

Use when the change can alter the application, produced artifact, data model,
dependencies, runtime behavior, or deployment behavior. If a commit contains
both logic and architecture changes that are safely separable by complete file
paths, classify the commit as `LOGIC` and record both `logic_paths` and
`architecture_paths` for possible architecture-only extraction. Examples:

- Source code such as `.java`, `.kt`, `.ts`, `.js`, `.py`, `.go`, `.cs`, or
  `.sql`.
- `pom.xml`, Gradle files, package manifests, lock files, or dependency changes.
- `application.yml`, runtime properties, environment files, feature flags, or
  service configuration.
- Database migrations and schema changes.
- Dockerfiles, Helm charts, Kubernetes manifests, Terraform, or other runtime
  infrastructure.
- Tests when they accompany or prove an application behavior change. Pure test
  metadata can be reviewed case by case.

Treat a change as LOGIC even when it appears small. A one-line dependency,
configuration, or migration change can break the application.

### UNCERTAIN

Use only when a safe classification or safe path-level separation cannot be
proven after reviewing the complete diff. Prefer resolving the change as
ARCHITECTURE or LOGIC whenever the evidence supports it. Examples:

- One file contains both architecture and application logic.
- Binary, generated, vendored, or very large changes cannot be reviewed fully.
- A rename or merge hides the effective change.
- The purpose and runtime effect remain ambiguous after reading the diff.

Stop automatic baseline selection while any commit is UNCERTAIN.

## Review procedure

For each commit, in first-parent order:

1. Read its metadata and changed-file list from
   `production-baseline-evidence.json`.
2. Read the complete patch saved under `patches/`.
3. If needed, inspect one file at a time with:

   ```bash
   git diff <first-parent> <commit> -- <path>
   ```

4. Assign every changed path exactly once to `architecture_paths` or
   `logic_paths`.
5. For a rename, list both the old path and the new path.
6. Assign ARCHITECTURE, LOGIC, or UNCERTAIN. When both architecture and
   logic paths are safely separable, assign LOGIC and keep both path groups.
7. Write a one-line reason based on the actual diff.

The heuristic hint emitted by the inspection script is never the final answer.
