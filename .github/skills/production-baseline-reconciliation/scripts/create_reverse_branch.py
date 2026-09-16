#!/usr/bin/env python3
"""Create a local reverse branch for approved undeployed logic changes.

The script validates a reviewed manifest and an explicit user-approved reverse
selection. It creates a branch from the frozen analyzed HEAD, applies the inverse
of each selected commit only for its reviewed logic paths, creates one synthetic
local commit, removes the temporary worktree after success, and never pushes or
opens a pull request.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


ALLOWED_CLASSIFICATIONS = {"ARCHITECTURE", "LOGIC", "UNCERTAIN"}


class CommandError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, stderr: str):
        self.command = list(command)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"Command failed ({returncode}): {' '.join(command)}"
            + (f"\n{self.stderr}" if self.stderr else "")
        )


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise CommandError(command, result.returncode, stderr)
    return result


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = run_command(["git", "-C", str(repo), *args], check=check)
    return result.stdout.strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    result = run_command(
        ["git", "-C", str(repo), *args],
        check=True,
        text=False,
    )
    return result.stdout


def resolve_repo(path: Path) -> Path:
    if shutil.which("git") is None:
        raise RuntimeError("git is not installed or is not available in PATH")
    root = git(path, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def resolve_commit(repo: Path, ref: str) -> str:
    resolved = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if not resolved:
        raise RuntimeError(f"Cannot resolve commit ref '{ref}'")
    return resolved.splitlines()[-1].strip()


def resolve_tag_commit(repo: Path, tag: str) -> str:
    resolved = git(
        repo,
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
        check=False,
    )
    if not resolved:
        raise RuntimeError(f"Cannot resolve production tag '{tag}'")
    return resolved.splitlines()[-1].strip()


def normalize_path(path: str) -> str:
    value = path.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value or value.startswith("/"):
        raise RuntimeError(f"Unsafe or empty repository path: {path!r}")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise RuntimeError(f"Unsafe repository path: {path!r}")
    return value


def parse_name_status(raw: str) -> set[str]:
    paths: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0][0]
        if code in {"R", "C"} and len(parts) >= 3:
            paths.add(normalize_path(parts[1]))
            paths.add(normalize_path(parts[2]))
        elif len(parts) >= 2:
            paths.add(normalize_path(parts[1]))
    return paths


def first_parent_commits(repo: Path, production: str, head: str) -> list[str]:
    raw = git(
        repo,
        "rev-list",
        "--first-parent",
        "--reverse",
        f"{production}..{head}",
    )
    return [line for line in raw.splitlines() if line.strip()]


def changed_paths_for_commit(repo: Path, sha: str) -> set[str]:
    parents = git(repo, "show", "-s", "--format=%P", sha).split()
    if not parents:
        raise RuntimeError(f"Commit {sha} has no parent")
    raw = git(repo, "diff", "--name-status", "-M", "-C", parents[0], sha)
    return parse_name_status(raw)


def load_json(path: Path, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{label} root must be a JSON object")
    return data


def as_string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"Field '{field}' must be a list of strings")
    return [normalize_path(item) for item in value]


def validate_manifest(
    repo: Path,
    manifest: dict[str, object],
) -> tuple[str, str, str, str, list[dict[str, object]], set[str], set[str]]:
    production_tag_raw = manifest.get("production_tag")
    production_raw = manifest.get("production_commit")
    head_raw = manifest.get("default_head")
    branch_raw = manifest.get("default_branch")
    entries_raw = manifest.get("commits")

    if not isinstance(production_tag_raw, str) or not production_tag_raw.strip():
        raise RuntimeError("Manifest requires non-empty string field production_tag")
    if not isinstance(production_raw, str) or not isinstance(head_raw, str):
        raise RuntimeError("Manifest requires string fields production_commit and default_head")
    if not isinstance(branch_raw, str) or not branch_raw.strip():
        raise RuntimeError("Manifest requires non-empty string field default_branch")
    if not isinstance(entries_raw, list):
        raise RuntimeError("Manifest requires a commits array")

    production = resolve_commit(repo, production_raw)
    production_from_tag = resolve_tag_commit(repo, production_tag_raw)
    if production_from_tag != production:
        raise RuntimeError(
            f"Manifest production tag '{production_tag_raw}' resolves to {production_from_tag}, "
            f"not production_commit {production}"
        )

    head = resolve_commit(repo, head_raw)
    ancestry = run_command(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", production, head],
        check=False,
    )
    if ancestry.returncode != 0:
        raise RuntimeError("production_commit is not an ancestor of default_head")

    actual_commits = first_parent_commits(repo, production, head)
    if len(entries_raw) != len(actual_commits):
        raise RuntimeError(
            "Manifest must contain every first-parent commit after production in exact order "
            f"(expected {len(actual_commits)}, found {len(entries_raw)})"
        )

    normalized_entries: list[dict[str, object]] = []
    global_architecture_paths: set[str] = set()
    global_logic_paths: set[str] = set()

    for index, (entry_raw, expected_sha) in enumerate(
        zip(entries_raw, actual_commits), start=1
    ):
        if not isinstance(entry_raw, dict):
            raise RuntimeError(f"Manifest commit entry {index} must be an object")
        sha_raw = entry_raw.get("sha")
        classification_raw = entry_raw.get("classification")
        if not isinstance(sha_raw, str) or not isinstance(classification_raw, str):
            raise RuntimeError(
                f"Manifest commit entry {index} requires sha and classification strings"
            )

        sha = resolve_commit(repo, sha_raw)
        classification = classification_raw.upper()
        if sha != expected_sha:
            raise RuntimeError(
                f"Manifest commit order mismatch at position {index}: "
                f"expected {expected_sha}, found {sha}"
            )
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise RuntimeError(
                f"Invalid classification {classification!r} for commit {sha}"
            )
        if classification == "UNCERTAIN":
            raise RuntimeError(
                f"Commit {sha} is UNCERTAIN. Resolve it manually before creating a reverse branch."
            )

        architecture_paths = set(
            as_string_list(entry_raw.get("architecture_paths", []), "architecture_paths")
        )
        logic_paths = set(
            as_string_list(entry_raw.get("logic_paths", []), "logic_paths")
        )
        overlap = architecture_paths & logic_paths
        if overlap:
            raise RuntimeError(
                f"Commit {sha} assigns paths to both architecture and logic: {sorted(overlap)}"
            )

        actual_paths = changed_paths_for_commit(repo, sha)
        accounted_paths = architecture_paths | logic_paths
        missing = actual_paths - accounted_paths
        extra = accounted_paths - actual_paths
        if missing:
            raise RuntimeError(
                f"Commit {sha} has unclassified changed paths: {sorted(missing)}"
            )
        if extra:
            raise RuntimeError(
                f"Commit {sha} lists paths not changed by the commit: {sorted(extra)}"
            )

        if classification == "ARCHITECTURE" and (not architecture_paths or logic_paths):
            raise RuntimeError(
                f"ARCHITECTURE commit {sha} must have architecture_paths only"
            )
        if classification == "LOGIC" and not logic_paths:
            raise RuntimeError(
                f"LOGIC commit {sha} must contain at least one logic path"
            )

        global_architecture_paths.update(architecture_paths)
        global_logic_paths.update(logic_paths)
        normalized_entries.append(
            {
                "index": index,
                "sha": sha,
                "classification": classification,
                "architecture_paths": sorted(architecture_paths),
                "logic_paths": sorted(logic_paths),
                "notes": entry_raw.get("notes", ""),
            }
        )

    cross_type_overlap = global_architecture_paths & global_logic_paths
    if cross_type_overlap:
        raise RuntimeError(
            "The same path is classified as architecture in one commit and logic in another. "
            f"Automatic reverse is unsafe: {sorted(cross_type_overlap)}"
        )

    return (
        production_tag_raw,
        production,
        head,
        branch_raw,
        normalized_entries,
        global_architecture_paths,
        global_logic_paths,
    )


def resolve_current_branch_head(repo: Path, branch: str) -> str | None:
    candidates = [
        f"refs/remotes/origin/{branch}",
        f"refs/heads/{branch}",
        branch,
    ]
    for candidate in candidates:
        value = git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}", check=False)
        if value:
            return value.splitlines()[-1].strip()
    return None


def validate_selection(
    repo: Path,
    selection: dict[str, object],
    entries: list[dict[str, object]],
) -> tuple[list[dict[str, object]], str]:
    if selection.get("approved") is not True:
        raise RuntimeError("Reverse selection is not explicitly approved")

    selected_raw = selection.get("reverse_commits")
    if not isinstance(selected_raw, list) or not selected_raw:
        raise RuntimeError("reverse_commits must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in selected_raw):
        raise RuntimeError("Every reverse_commits item must be a non-empty SHA string")

    by_sha = {str(entry["sha"]): entry for entry in entries}
    resolved_selected: list[str] = []
    seen: set[str] = set()
    for raw in selected_raw:
        resolved = resolve_commit(repo, raw)
        if resolved in seen:
            raise RuntimeError(f"Duplicate reverse commit in approved selection: {resolved}")
        seen.add(resolved)
        resolved_selected.append(resolved)

    missing = [sha for sha in resolved_selected if sha not in by_sha]
    if missing:
        raise RuntimeError(
            f"Approved reverse commits are not present in the reviewed manifest: {missing}"
        )

    selected_entries: list[dict[str, object]] = []
    for sha in resolved_selected:
        entry = by_sha[sha]
        if entry["classification"] != "LOGIC":
            raise RuntimeError(
                f"Approved reverse commit {sha} is not classified LOGIC"
            )
        selected_entries.append(entry)

    selected_entries.sort(key=lambda entry: int(entry["index"]), reverse=True)
    notes = selection.get("user_notes", "")
    if not isinstance(notes, str):
        raise RuntimeError("user_notes must be a string when provided")
    return selected_entries, notes


def write_result(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def branch_exists(repo: Path, branch: str) -> bool:
    result = run_command(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    )
    return result.returncode == 0


def normalize_branch_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    component = re.sub(r"-+", "-", component).strip("-_")
    return component.lower() or "production"


def unique_branch_name(repo: Path, requested: str | None, production_tag: str) -> str:
    base = requested or f"reverse-prd-{normalize_branch_component(production_tag)}"
    check = run_command(["git", "check-ref-format", "--branch", base], check=False)
    if check.returncode != 0:
        raise RuntimeError(f"Invalid branch name: {base}")
    if not branch_exists(repo, base):
        return base

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    candidate = f"{base}-{timestamp}"
    counter = 2
    while branch_exists(repo, candidate):
        candidate = f"{base}-{timestamp}-{counter}"
        counter += 1
    return candidate


def reverse_patch_for_entry(repo: Path, entry: dict[str, object]) -> bytes:
    sha = str(entry["sha"])
    parents = git(repo, "show", "-s", "--format=%P", sha).split()
    if not parents:
        raise RuntimeError(f"Commit {sha} has no parent")
    logic_paths = [str(path) for path in entry["logic_paths"]]
    patch = git_bytes(
        repo,
        "diff",
        "--binary",
        "--full-index",
        "--find-renames",
        parents[0],
        sha,
        "--",
        *logic_paths,
    )
    if not patch.strip():
        raise RuntimeError(
            f"Commit {sha} produced no reverse patch for its reviewed logic paths"
        )
    return patch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a local branch that reverses user-approved logic changes."
    )
    parser.add_argument("--manifest", required=True, help="Reviewed classification manifest JSON")
    parser.add_argument("--selection", required=True, help="Explicitly approved reverse selection JSON")
    parser.add_argument(
        "--branch",
        help="Optional local branch name. Defaults to reverse-prd-<normalized-production-tag>.",
    )
    parser.add_argument(
        "--output",
        default="reverse-branch-result.json",
        help="Result JSON path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).resolve()
    temp_parent: Path | None = None
    worktree_path: Path | None = None
    branch: str | None = None
    result: dict[str, object] = {
        "status": "FAILED",
        "pushed": False,
        "pull_request_created": False,
    }

    try:
        repo = resolve_repo(Path.cwd())
        manifest_path = Path(args.manifest).resolve()
        selection_path = Path(args.selection).resolve()
        manifest = load_json(manifest_path, "manifest")
        selection = load_json(selection_path, "selection")

        (
            production_tag,
            production,
            head,
            default_branch,
            entries,
            architecture_paths,
            _all_logic_paths,
        ) = validate_manifest(repo, manifest)
        selected_entries, user_notes = validate_selection(repo, selection, entries)

        current_head = resolve_current_branch_head(repo, default_branch)
        if current_head is None:
            raise RuntimeError(
                f"Cannot resolve current analyzed branch '{default_branch}' to verify the frozen HEAD"
            )
        if current_head != head:
            raise RuntimeError(
                f"Analyzed branch moved: manifest HEAD is {head}, current '{default_branch}' HEAD is {current_head}. "
                "Rerun the analysis before creating the reverse branch."
            )

        selected_logic_paths = {
            str(path)
            for entry in selected_entries
            for path in entry["logic_paths"]
        }
        if not selected_logic_paths:
            raise RuntimeError("Approved reverse selection contains no logic paths")

        branch = unique_branch_name(repo, args.branch, production_tag)

        user_name = git(repo, "config", "user.name", check=False)
        user_email = git(repo, "config", "user.email", check=False)
        if not user_name or not user_email:
            raise RuntimeError(
                "Git user.name and user.email must be configured before creating the reverse commit"
            )

        temp_parent = Path(tempfile.mkdtemp(prefix="production-reverse-"))
        worktree_path = temp_parent / "worktree"
        patch_dir = temp_parent / "patches"
        patch_dir.mkdir(parents=True, exist_ok=True)

        git(repo, "worktree", "add", "-b", branch, str(worktree_path), head)

        applied: list[dict[str, object]] = []
        for sequence, entry in enumerate(selected_entries, start=1):
            sha = str(entry["sha"])
            patch = reverse_patch_for_entry(repo, entry)
            patch_path = patch_dir / f"{sequence:04d}-{sha}.diff"
            patch_path.write_bytes(patch)
            run_command(
                [
                    "git",
                    "-C",
                    str(worktree_path),
                    "apply",
                    "--reverse",
                    "--index",
                    "--3way",
                    "--whitespace=nowarn",
                    str(patch_path),
                ]
            )
            applied.append(
                {
                    "sha": sha,
                    "logic_paths": list(entry["logic_paths"]),
                    "architecture_paths_preserved": list(entry["architecture_paths"]),
                }
            )

        staged = run_command(
            ["git", "-C", str(worktree_path), "diff", "--cached", "--quiet"],
            check=False,
        )
        if staged.returncode == 0:
            raise RuntimeError("Approved reverse produced no staged changes")
        if staged.returncode not in {0, 1}:
            raise RuntimeError("Cannot verify staged reverse changes")

        changed_paths = parse_name_status(
            git(worktree_path, "diff", "--cached", "--name-status", "-M", "-C")
        )
        unexpected = changed_paths - selected_logic_paths
        if unexpected:
            raise RuntimeError(
                "Reverse changed paths outside the approved logic path set: "
                f"{sorted(unexpected)}"
            )
        architecture_touched = changed_paths & architecture_paths
        if architecture_touched:
            raise RuntimeError(
                "Reverse would modify reviewed architecture paths: "
                f"{sorted(architecture_touched)}"
            )

        selected_shas_chronological = [
            str(entry["sha"])
            for entry in sorted(selected_entries, key=lambda item: int(item["index"]))
        ]
        commit_message = (
            "revert(baseline): remove undeployed production logic\n\n"
            f"Production-Tag: {production_tag}\n"
            f"Production-Commit: {production}\n"
            f"Source-Analyzed-Head: {head}\n"
            f"Reversed-Commits: {', '.join(selected_shas_chronological)}\n"
        )
        run_command(
            [
                "git",
                "-C",
                str(worktree_path),
                "commit",
                "--no-gpg-sign",
                "-m",
                commit_message,
            ]
        )

        candidate = git(worktree_path, "rev-parse", "HEAD")
        changed_files = git(
            worktree_path,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            "-M",
            "HEAD",
        ).splitlines()

        git(repo, "worktree", "remove", "--force", str(worktree_path))
        shutil.rmtree(temp_parent, ignore_errors=True)
        temp_parent = None
        worktree_path = None

        result.update(
            {
                "status": "REVERSE_BRANCH_CREATED",
                "repository": str(repo),
                "manifest": str(manifest_path),
                "selection": str(selection_path),
                "production_tag": production_tag,
                "production_commit": production,
                "default_branch": default_branch,
                "default_head": head,
                "branch": branch,
                "candidate_commit": candidate,
                "approved_reverse_commits": selected_shas_chronological,
                "approved_logic_paths": sorted(selected_logic_paths),
                "changed_files": changed_files,
                "applied_reverse_plan": applied,
                "user_notes": user_notes,
                "worktree_removed": True,
                "next_action": "Review the local branch and push/open the pull request manually if approved.",
            }
        )
        write_result(output_path, result)
        print(f"Result JSON: {output_path}")
        print(f"Local reverse branch: {branch}")
        print(f"Candidate commit: {candidate}")
        print("No push or pull request was performed.")
        return 0
    except (RuntimeError, CommandError, OSError, ValueError) as exc:
        result.update(
            {
                "status": "FAILED",
                "error": str(exc),
                "branch": branch,
                "preserved_worktree": str(worktree_path) if worktree_path else None,
                "preserved_temp_directory": str(temp_parent) if temp_parent else None,
            }
        )
        try:
            write_result(output_path, result)
        except OSError:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        if worktree_path:
            print(
                f"A temporary worktree may remain for inspection: {worktree_path}",
                file=sys.stderr,
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
