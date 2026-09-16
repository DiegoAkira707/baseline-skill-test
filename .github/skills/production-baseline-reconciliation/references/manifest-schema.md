# Reviewed Manifest and Reverse Selection

Create `production-baseline-reviewed.json` only after reviewing every patch.
The reverse-branch script validates this file strictly.

## Reviewed manifest shape

```json
{
  "production_tag": "az0.1.0-43",
  "production_commit": "FULL_PRODUCTION_SHA",
  "default_head": "FULL_DEFAULT_HEAD_SHA",
  "default_branch": "main",
  "commits": [
    {
      "sha": "FULL_COMMIT_SHA_1",
      "classification": "ARCHITECTURE",
      "architecture_paths": [
        ".github/workflows/cd-prd.yml"
      ],
      "logic_paths": [],
      "notes": "Adds repository delivery workflow only."
    },
    {
      "sha": "FULL_COMMIT_SHA_2",
      "classification": "LOGIC",
      "architecture_paths": [
        ".github/CODEOWNERS"
      ],
      "logic_paths": [
        "src/main/java/com/example/Service.java"
      ],
      "notes": "CODEOWNERS is separable from the Java change."
    }
  ]
}
```

## Manifest invariants

- Include the exact user-supplied `production_tag`. It must resolve to
  `production_commit`.
- Use full SHAs for `production_commit`, `default_head`, and every commit.
- Include every first-parent commit after production, in exact chronological
  order. Do not omit merge commits.
- Use only `ARCHITECTURE`, `LOGIC`, or `UNCERTAIN`.
- Assign every changed path exactly once.
- For renames, include both old and new paths.
- ARCHITECTURE must contain architecture paths only.
- LOGIC must contain at least one logic path. It may also contain architecture
  paths when both groups are safely separable by complete file paths.
- Never run an automatic reverse while any entry is UNCERTAIN.
- Never classify the same path as architecture in one commit and logic in
  another; that path requires manual handling.

## Reverse selection shape

Create this file only after showing the proposed reverse list and receiving
explicit user approval:

```json
{
  "approved": true,
  "reverse_commits": [
    "FULL_LOGIC_COMMIT_SHA_1",
    "FULL_LOGIC_COMMIT_SHA_2"
  ],
  "user_notes": "Approved after removing commit abc1234."
}
```

## Selection invariants

- `approved` must be exactly `true`.
- `reverse_commits` must contain at least one full or unambiguous SHA.
- Every selected SHA must exist in the reviewed manifest.
- Every selected SHA must be classified LOGIC.
- Preserve the user's final approved list exactly; do not silently add or remove
  commits.

## Branch command

Run only after LT = NO and explicit reverse-list approval:

```bash
python scripts/create_reverse_branch.py \
  --manifest baseline-analysis/production-baseline-reviewed.json \
  --selection baseline-analysis/reverse-selection.json \
  --output baseline-analysis/reverse-branch-result.json
```

The script starts from the frozen analyzed HEAD, reverses only approved logic
paths, creates one local synthetic commit, and never pushes or opens a pull
request.
