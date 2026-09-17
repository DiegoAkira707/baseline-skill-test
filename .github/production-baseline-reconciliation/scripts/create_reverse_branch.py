#!/usr/bin/env python3
"""Create and publish an approved reverse branch without opening a pull request.

The caller must already have shown the reverse proposal to the user and received
explicit approval. Classification is supplied through repeated --logic-path and
--architecture-path arguments so no JSON manifest/selection files are needed.
The script validates the complete first-parent post-production history, reverses
only approved logic paths from the frozen analyzed HEAD, creates one synthetic
commit, pushes the branch to the configured remote, writes Markdown validation,
and never creates a pull request.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


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
    return run_command(["git", "-C", str(repo), *args], check=check).stdout.strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    return run_command(
        ["git", "-C", str(repo), *args], check=True, text=False
    ).stdout


def resolve_repo(path: Path) -> Path:
    if shutil.which("git") is None:
        raise RuntimeError("git is not installed or is not available in PATH")
    root = git(path, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def resolve_commit(repo: Path, ref: str) -> str:
    value = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if not value:
        raise RuntimeError(f"Cannot resolve commit ref '{ref}'")
    return value.splitlines()[-1].strip()


def resolve_tag_commit(repo: Path, tag: str) -> str:
    value = git(
        repo,
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
        check=False,
    )
    if not value:
        raise RuntimeError(f"Cannot resolve production tag '{tag}'")
    return value.splitlines()[-1].strip()


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
    raw = git(repo, "rev-list", "--first-parent", "--reverse", f"{production}..{head}")
    return [line for line in raw.splitlines() if line.strip()]


def changed_paths_for_commit(repo: Path, sha: str) -> set[str]:
    parents = git(repo, "show", "-s", "--format=%P", sha).split()
    if not parents:
        raise RuntimeError(f"Commit {sha} has no parent")
    raw = git(repo, "diff", "--name-status", "-M", "-C", parents[0], sha)
    return parse_name_status(raw)


def resolve_current_branch_head(repo: Path, branch: str, remote: str) -> str | None:
    candidates = [
        f"refs/remotes/{remote}/{branch}",
        f"refs/heads/{branch}",
        branch,
    ]
    for candidate in candidates:
        value = git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}", check=False)
        if value:
            return value.splitlines()[-1].strip()
    return None


def parse_path_specs(
    repo: Path,
    specs: Iterable[str],
    label: str,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for raw in specs:
        if "::" not in raw:
            raise RuntimeError(
                f"Invalid {label} value {raw!r}. Use <commit>::<repository-path>."
            )
        commit_raw, path_raw = raw.split("::", 1)
        sha = resolve_commit(repo, commit_raw.strip())
        path = normalize_path(path_raw.strip())
        result.setdefault(sha, set()).add(path)
    return result


def validate_reviewed_history(
    repo: Path,
    production_tag: str,
    production_raw: str,
    head_raw: str,
    branch: str,
    remote: str,
    reverse_raw: list[str],
    logic_specs: list[str],
    architecture_specs: list[str],
) -> tuple[str, str, list[str], list[str], dict[str, set[str]], dict[str, set[str]]]:
    production = resolve_commit(repo, production_raw)
    production_from_tag = resolve_tag_commit(repo, production_tag)
    if production_from_tag != production:
        raise RuntimeError(
            f"Production tag '{production_tag}' resolves to {production_from_tag}, "
            f"not production commit {production}."
        )

    head = resolve_commit(repo, head_raw)
    current_head = resolve_current_branch_head(repo, branch, remote)
    if current_head is None:
        raise RuntimeError(f"Cannot resolve analyzed branch '{branch}'")
    if current_head != head:
        raise RuntimeError(
            f"Analyzed branch moved: frozen HEAD is {head}, current '{branch}' HEAD is "
            f"{current_head}. Rerun the analysis before creating the reverse branch."
        )

    ancestry = run_command(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", production, head],
        check=False,
    )
    if ancestry.returncode != 0:
        raise RuntimeError("production_commit is not an ancestor of the frozen analyzed HEAD")

    actual_commits = first_parent_commits(repo, production, head)
    logic_by_commit = parse_path_specs(repo, logic_specs, "--logic-path")
    architecture_by_commit = parse_path_specs(
        repo, architecture_specs, "--architecture-path"
    )

    actual_set = set(actual_commits)
    referenced = set(logic_by_commit) | set(architecture_by_commit)
    extra_commits = referenced - actual_set
    missing_commits = actual_set - referenced
    if extra_commits:
        raise RuntimeError(
            f"Classification contains commits outside post-production first-parent history: "
            f"{sorted(extra_commits)}"
        )
    if missing_commits:
        raise RuntimeError(
            "Every post-production commit must have reviewed path classification before reverse. "
            f"Missing commits: {sorted(missing_commits)}"
        )

    global_logic: set[str] = set()
    global_architecture: set[str] = set()
    for sha in actual_commits:
        logic_paths = logic_by_commit.get(sha, set())
        architecture_paths = architecture_by_commit.get(sha, set())
        overlap = logic_paths & architecture_paths
        if overlap:
            raise RuntimeError(
                f"Commit {sha} assigns paths to both logic and architecture: {sorted(overlap)}"
            )
        actual_paths = changed_paths_for_commit(repo, sha)
        reviewed_paths = logic_paths | architecture_paths
        missing = actual_paths - reviewed_paths
        extra = reviewed_paths - actual_paths
        if missing:
            raise RuntimeError(f"Commit {sha} has unclassified paths: {sorted(missing)}")
        if extra:
            raise RuntimeError(
                f"Commit {sha} classifies paths not changed by that commit: {sorted(extra)}"
            )
        global_logic.update(logic_paths)
        global_architecture.update(architecture_paths)

    cross_type_overlap = global_logic & global_architecture
    if cross_type_overlap:
        raise RuntimeError(
            "The same path is classified as architecture in one commit and logic in another. "
            f"Automatic reverse is unsafe: {sorted(cross_type_overlap)}"
        )

    reverse_commits: list[str] = []
    seen: set[str] = set()
    for raw in reverse_raw:
        sha = resolve_commit(repo, raw)
        if sha in seen:
            raise RuntimeError(f"Duplicate approved reverse commit: {sha}")
        if sha not in actual_set:
            raise RuntimeError(f"Approved reverse commit is outside reviewed history: {sha}")
        if not logic_by_commit.get(sha):
            raise RuntimeError(f"Approved reverse commit has no reviewed logic paths: {sha}")
        seen.add(sha)
        reverse_commits.append(sha)

    if not reverse_commits:
        raise RuntimeError("At least one approved --reverse-commit is required")

    reverse_commits.sort(key=actual_commits.index, reverse=True)
    return (
        production,
        head,
        actual_commits,
        reverse_commits,
        logic_by_commit,
        architecture_by_commit,
    )


def local_branch_exists(repo: Path, branch: str) -> bool:
    result = run_command(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    )
    return result.returncode == 0


def remote_branch_exists(repo: Path, remote: str, branch: str) -> bool:
    result = run_command(
        ["git", "-C", str(repo), "ls-remote", "--exit-code", "--heads", remote, branch],
        check=False,
    )
    return result.returncode == 0


def normalize_branch_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    component = re.sub(r"-+", "-", component).strip("-_")
    return component.lower() or "production"


def unique_branch_name(
    repo: Path,
    remote: str,
    requested: str | None,
    default_head: str
) -> str:
    short_head = git(repo, "rev-parse", "--short=7", default_head)
    base = requested or f"reverse-prd-{short_head}"

    check = run_command(
        ["git", "check-ref-format", "--branch", base],
        check=False
    )
    if check.returncode != 0:
        raise RuntimeError(f"Invalid branch name: {base}")

    if not local_branch_exists(repo, base) and not remote_branch_exists(repo, remote, base):
        return base

    counter = 2
    candidate = f"{base}-{counter}"

    while local_branch_exists(repo, candidate) or remote_branch_exists(repo, remote, candidate):
        counter += 1
        candidate = f"{base}-{counter}"

    return candidate


def reverse_patch(repo: Path, sha: str, logic_paths: list[str]) -> bytes:
    parents = git(repo, "show", "-s", "--format=%P", sha).split()
    if not parents:
        raise RuntimeError(f"Commit {sha} has no parent")
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
        raise RuntimeError(f"Commit {sha} produced no reverse patch for approved logic paths")
    return patch


def md_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_validation(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Reverse Validation",
        "",
        "## Resultado",
        f"- Estado: {md_escape(data.get('status', 'FAILED'))}",
        f"- Rama: {md_escape(data.get('branch', 'NO CREADA'))}",
        f"- Commit candidato: {md_escape(data.get('candidate_commit', 'PENDIENTE'))}",
        f"- Push realizado: {'SI' if data.get('pushed') else 'NO'}",
        "- Pull request creado: NO",
        "",
        "## Referencia",
        f"- Tag de produccion: {md_escape(data.get('production_tag', ''))}",
        f"- Commit de produccion: {md_escape(data.get('production_commit', ''))}",
        f"- Rama analizada: {md_escape(data.get('default_branch', ''))}",
        f"- HEAD analizado: {md_escape(data.get('default_head', ''))}",
        "",
        "## Reverse aprobado",
    ]
    commits = data.get("approved_reverse_commits") or []
    if commits:
        for sha in commits:
            lines.append(f"- `{md_escape(sha)}`")
    else:
        lines.append("- NINGUNO")
    lines.extend(["", "## Paths de logica revertidos"])
    logic_paths = data.get("approved_logic_paths") or []
    if logic_paths:
        for item in logic_paths:
            lines.append(f"- `{md_escape(item)}`")
    else:
        lines.append("- NINGUNO")
    preserved = data.get("architecture_paths_preserved") or []
    lines.extend(["", "## Arquitectura preservada"])
    if preserved:
        for item in preserved:
            lines.append(f"- `{md_escape(item)}`")
    else:
        lines.append("- NO APLICA")
    if data.get("error"):
        lines.extend(["", "## Error", f"- {md_escape(data['error'])}"])
    if data.get("user_notes"):
        lines.extend(["", "## Notas de aprobacion", f"- {md_escape(data['user_notes'])}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and push a user-approved reverse branch without opening a pull request."
    )
    parser.add_argument("--approved", action="store_true", help="Confirm explicit user approval was received")
    parser.add_argument("--production-tag", required=True)
    parser.add_argument("--production-commit", required=True)
    parser.add_argument("--default-head", required=True, help="Frozen analyzed HEAD")
    parser.add_argument("--default-branch", default="main")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--reverse-commit", action="append", default=[], help="Approved LOGIC commit; repeat as needed")
    parser.add_argument(
        "--logic-path",
        action="append",
        default=[],
        help="Reviewed mapping <commit>::<path>; repeat for every logic path in post-production history",
    )
    parser.add_argument(
        "--architecture-path",
        action="append",
        default=[],
        help="Reviewed mapping <commit>::<path>; repeat for every architecture path in post-production history",
    )
    parser.add_argument("--branch", help="Optional explicit branch name; otherwise uses reverse-prd-<production-tag>-<UTC timestamp>")
    parser.add_argument("--user-notes", default="")
    parser.add_argument(
        "--output",
        required=True,
        help="Markdown validation output path inside the active analysis run directory",
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
        "production_tag": args.production_tag,
        "production_commit": args.production_commit,
        "default_branch": args.default_branch,
        "default_head": args.default_head,
        "user_notes": args.user_notes,
    }

    try:
        if not args.approved:
            raise RuntimeError(
                "Reverse branch creation requires explicit user approval. Show the proposal first, "
                "wait for OK/adjustments, then rerun with --approved."
            )

        repo = resolve_repo(Path.cwd())
        if not git(repo, "remote", "get-url", args.remote, check=False):
            raise RuntimeError(f"Remote '{args.remote}' does not exist")

        git(repo, "fetch", "--prune", "--tags", args.remote)

        (
            production,
            head,
            _actual_commits,
            reverse_commits,
            logic_by_commit,
            architecture_by_commit,
        ) = validate_reviewed_history(
            repo,
            args.production_tag,
            args.production_commit,
            args.default_head,
            args.default_branch,
            args.remote,
            args.reverse_commit,
            args.logic_path,
            args.architecture_path,
        )

        branch = unique_branch_name(repo, args.remote, args.branch, head)
        result.update(
            {
                "production_commit": production,
                "default_head": head,
                "branch": branch,
                "approved_reverse_commits": list(reversed(reverse_commits)),
            }
        )

        user_name = git(repo, "config", "user.name", check=False)
        user_email = git(repo, "config", "user.email", check=False)
        if not user_name or not user_email:
            raise RuntimeError("Git user.name and user.email must be configured before creating the reverse commit")

        selected_logic_paths = {
            path for sha in reverse_commits for path in logic_by_commit.get(sha, set())
        }
        architecture_preserved = {
            path for paths in architecture_by_commit.values() for path in paths
        }
        if not selected_logic_paths:
            raise RuntimeError("Approved reverse selection contains no logic paths")

        temp_parent = Path(tempfile.mkdtemp(prefix="production-reverse-"))
        worktree_path = temp_parent / "worktree"
        patch_dir = temp_parent / "patches"
        patch_dir.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-b", branch, str(worktree_path), head)

        for sequence, sha in enumerate(reverse_commits, start=1):
            logic_paths = sorted(logic_by_commit[sha])
            patch = reverse_patch(repo, sha, logic_paths)
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
                f"Reverse changed paths outside approved logic paths: {sorted(unexpected)}"
            )
        architecture_touched = changed_paths & architecture_preserved
        if architecture_touched:
            raise RuntimeError(
                f"Reverse would modify reviewed architecture paths: {sorted(architecture_touched)}"
            )

        chronological = list(reversed(reverse_commits))
        commit_message = (
            "revert(baseline): remove undeployed production logic\n\n"
            f"Production-Tag: {args.production_tag}\n"
            f"Production-Commit: {production}\n"
            f"Source-Analyzed-Head: {head}\n"
            f"Reversed-Commits: {', '.join(chronological)}\n"
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

        current_head_after = resolve_current_branch_head(repo, args.default_branch, args.remote)
        if current_head_after != head:
            raise RuntimeError(
                f"Analyzed branch moved during reverse preparation: expected {head}, "
                f"found {current_head_after}. Do not push; rerun the analysis."
            )

        git(repo, "worktree", "remove", "--force", str(worktree_path))
        shutil.rmtree(temp_parent, ignore_errors=True)
        temp_parent = None
        worktree_path = None

        run_command(
            ["git", "-C", str(repo), "push", "--set-upstream", args.remote, branch]
        )
        remote_sha_raw = git(repo, "ls-remote", "--heads", args.remote, f"refs/heads/{branch}")
        remote_sha = remote_sha_raw.split()[0] if remote_sha_raw.strip() else ""
        if remote_sha != candidate:
            raise RuntimeError(
                f"Remote verification failed after push: expected {candidate}, found {remote_sha or 'nothing'}"
            )

        result.update(
            {
                "status": "REVERSE_BRANCH_CREATED_AND_PUSHED",
                "candidate_commit": candidate,
                "pushed": True,
                "approved_logic_paths": sorted(selected_logic_paths),
                "architecture_paths_preserved": sorted(architecture_preserved),
            }
        )
        write_validation(output_path, result)
        print(f"Reverse branch: {branch}")
        print(f"Candidate commit: {candidate}")
        print(f"Pushed to {args.remote}: YES")
        print("Pull request created: NO")
        print(f"Validation Markdown: {output_path}")
        return 0

    except (RuntimeError, CommandError, OSError, ValueError) as exc:
        result.update(
            {
                "status": "FAILED",
                "error": str(exc),
                "branch": branch,
            }
        )
        try:
            write_validation(output_path, result)
        except OSError:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        if worktree_path:
            print(f"A temporary worktree may remain: {worktree_path}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
