#!/usr/bin/env python3
"""Collect deterministic evidence for production baseline reconciliation.

This script is read-only except when --fetch is used. It validates a supplied
production tag + commit pair in the current Git repository, uses main unless another
branch is explicitly supplied, verifies ancestry, and records every first-parent
commit after production with a full patch.

It deliberately emits only heuristic file/commit hints. Final classification
must be performed by reviewing each diff under the skill rules.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


SCHEMA_VERSION = "1.1"


class CommandError(RuntimeError):
    """Raised when a subprocess command fails."""

    def __init__(self, command: Sequence[str], returncode: int, stderr: str):
        self.command = list(command)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"Command failed ({returncode}): {' '.join(command)}"
            + (f"\n{self.stderr}" if self.stderr else "")
        )


@dataclass(frozen=True)
class DefaultBranch:
    name: str
    ref: str
    source: str
    head: str


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
    if not ref.strip():
        raise RuntimeError("production commit cannot be empty")
    result = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if not result:
        raise RuntimeError(
            f"Cannot resolve production commit '{ref}'. Fetch the remote or provide a valid SHA."
        )
    return result.splitlines()[-1].strip()


def resolve_tag_commit(repo: Path, tag: str) -> str:
    if not tag.strip():
        raise RuntimeError("production tag cannot be empty")
    result = git(
        repo,
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
        check=False,
    )
    if not result:
        raise RuntimeError(
            f"Cannot resolve production tag '{tag}'. Fetch tags or provide an existing tag."
        )
    return result.splitlines()[-1].strip()


def ref_to_branch_name(ref: str, remote: str) -> str:
    prefixes = (
        f"refs/remotes/{remote}/",
        f"{remote}/",
        "refs/heads/",
    )
    for prefix in prefixes:
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def candidate_refs(branch: str, remote: str) -> list[str]:
    if branch.startswith("refs/"):
        return [branch]
    if branch.startswith(f"{remote}/"):
        return [f"refs/remotes/{branch}", branch]
    return [f"refs/remotes/{remote}/{branch}", branch, f"refs/heads/{branch}"]


def resolve_existing_ref(repo: Path, candidates: Iterable[str]) -> tuple[str, str] | None:
    for candidate in candidates:
        head = git(
            repo,
            "rev-parse",
            "--verify",
            f"{candidate}^{{commit}}",
            check=False,
        )
        if head:
            return candidate, head.splitlines()[-1].strip()
    return None


def discover_remote_default(repo: Path, remote: str) -> tuple[str, str] | None:
    symbolic = git(
        repo,
        "symbolic-ref",
        "--quiet",
        "--short",
        f"refs/remotes/{remote}/HEAD",
        check=False,
    )
    if symbolic:
        return ref_to_branch_name(symbolic, remote), "remote-head"

    remote_probe = git(repo, "ls-remote", "--symref", remote, "HEAD", check=False)
    for line in remote_probe.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            branch = line[len("ref: refs/heads/") :].split("\t", 1)[0]
            return branch, "ls-remote"

    if shutil.which("gh"):
        result = run_command(
            [
                "gh",
                "repo",
                "view",
                "--json",
                "defaultBranchRef",
                "--jq",
                ".defaultBranchRef.name",
            ],
            cwd=repo,
            check=False,
        )
        branch = result.stdout.strip()
        if result.returncode == 0 and branch:
            return branch, "github-cli"

    return None


def resolve_default_branch(
    repo: Path,
    *,
    remote: str,
    explicit_branch: str | None,
) -> DefaultBranch:
    branch = explicit_branch or "main"
    resolved = resolve_existing_ref(repo, candidate_refs(branch, remote))
    if not resolved:
        if explicit_branch:
            raise RuntimeError(
                f"Cannot resolve user-supplied branch '{branch}'. Use --fetch or provide an existing ref."
            )
        raise RuntimeError(
            "Cannot resolve default branch 'main'. The skill uses main unless the user explicitly supplies another branch."
        )
    ref, head = resolved
    return DefaultBranch(
        name=ref_to_branch_name(branch, remote),
        ref=ref,
        source="explicit" if explicit_branch else "main-default",
        head=head,
    )


def normalize_path(path: str) -> str:
    value = path.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def path_hint(path: str) -> str:
    normalized = normalize_path(path)
    lower = normalized.lower()
    name = lower.rsplit("/", 1)[-1]

    architecture_exact = {
        ".github/codeowners",
        "codeowners",
        "docs/codeowners",
        ".github/dependabot.yml",
        ".github/dependabot.yaml",
        ".github/labeler.yml",
        ".github/labeler.yaml",
        ".github/release.yml",
        ".github/release.yaml",
        ".gitlab-ci.yml",
        ".gitlab-ci.yaml",
        "bitbucket-pipelines.yml",
        "bitbucket-pipelines.yaml",
        "jenkinsfile",
        "catalog-info.yaml",
        "catalog-info.yml",
        "mkdocs.yml",
        "mkdocs.yaml",
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
    }
    architecture_prefixes = (
        ".github/workflows/",
        ".github/actions/",
        ".github/issue_template/",
        ".github/pull_request_template/",
        ".azuredevops/",
        ".circleci/",
        ".codeql/",
        "docs/",
    )
    architecture_name_prefixes = (
        "readme",
        "license",
        "azure-pipelines",
    )

    if lower in architecture_exact:
        return "ARCHITECTURE"
    if lower.startswith(architecture_prefixes):
        return "ARCHITECTURE"
    if any(name.startswith(prefix) for prefix in architecture_name_prefixes):
        return "ARCHITECTURE"

    logic_exact = {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pnpm-lock.yml",
        "pyproject.toml",
        "poetry.lock",
        "requirements.txt",
        "go.mod",
        "go.sum",
        "cargo.toml",
        "cargo.lock",
        "nuget.config",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
    logic_prefixes = (
        "src/",
        "app/",
        "lib/",
        "migrations/",
        "db/migration/",
        "db/migrations/",
        "liquibase/",
        "flyway/",
        "helm/",
        "charts/",
        "k8s/",
        "kubernetes/",
        "manifests/",
        "terraform/",
        "infra/",
    )
    logic_extensions = {
        ".java",
        ".kt",
        ".kts",
        ".groovy",
        ".scala",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".py",
        ".go",
        ".cs",
        ".fs",
        ".fsx",
        ".rb",
        ".php",
        ".sql",
        ".rs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".swift",
        ".dart",
        ".vue",
        ".svelte",
        ".tf",
        ".tfvars",
    }

    if name in logic_exact or lower in logic_exact:
        return "LOGIC"
    if lower.startswith(logic_prefixes):
        return "LOGIC"
    if any(lower.endswith(extension) for extension in logic_extensions):
        return "LOGIC"
    if name.startswith("application.") or name.startswith("application-"):
        return "LOGIC"
    if name.startswith("bootstrap.") or name.startswith("bootstrap-"):
        return "LOGIC"
    if name.startswith("dockerfile"):
        return "LOGIC"
    if name.endswith(".csproj") or name.endswith(".fsproj"):
        return "LOGIC"

    return "REVIEW"


def parse_name_status(raw: str) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        code = status[0]
        if code in {"R", "C"} and len(parts) >= 3:
            old_path = normalize_path(parts[1])
            new_path = normalize_path(parts[2])
            hints = sorted({path_hint(old_path), path_hint(new_path)})
            files.append(
                {
                    "status": status,
                    "old_path": old_path,
                    "new_path": new_path,
                    "paths": [old_path, new_path],
                    "path_hints": hints,
                }
            )
        elif len(parts) >= 2:
            path = normalize_path(parts[1])
            files.append(
                {
                    "status": status,
                    "path": path,
                    "paths": [path],
                    "path_hints": [path_hint(path)],
                }
            )
    return files


def commit_hint(files: list[dict[str, object]]) -> str:
    hints: set[str] = set()
    for file_info in files:
        hints.update(str(value) for value in file_info.get("path_hints", []))
    if not hints:
        return "REVIEW_REQUIRED"
    if hints == {"ARCHITECTURE"}:
        return "ARCHITECTURE_CANDIDATE"
    if hints == {"LOGIC"}:
        return "LOGIC_CANDIDATE"
    if "ARCHITECTURE" in hints and "LOGIC" in hints and "REVIEW" not in hints:
        return "LOGIC_CANDIDATE"
    return "REVIEW_REQUIRED"


def first_parent_is_ancestor(repo: Path, production: str, head: str) -> bool:
    if production == head:
        return True
    chain = git(repo, "rev-list", "--first-parent", head).splitlines()
    return production in chain


def collect_commit(
    repo: Path,
    sha: str,
    index: int,
    patch_dir: Path,
) -> dict[str, object]:
    format_string = "%H%x00%h%x00%P%x00%an%x00%ae%x00%aI%x00%cI%x00%s"
    raw = git(repo, "show", "-s", f"--format={format_string}", sha)
    fields = raw.split("\x00")
    if len(fields) != 8:
        raise RuntimeError(f"Unexpected git metadata format for commit {sha}")

    full_sha, short_sha, parents_raw, author, email, authored_at, committed_at, subject = fields
    parents = [value for value in parents_raw.split() if value]
    if not parents:
        raise RuntimeError(f"Commit {sha} has no parent; cannot compare it in this workflow")
    first_parent = parents[0]

    name_status = git(
        repo,
        "diff",
        "--name-status",
        "-M",
        "-C",
        first_parent,
        full_sha,
    )
    files = parse_name_status(name_status)
    stat = git(repo, "diff", "--shortstat", first_parent, full_sha)
    patch = git_bytes(
        repo,
        "diff",
        "--binary",
        "--full-index",
        "--find-renames",
        first_parent,
        full_sha,
    )
    patch_path = patch_dir / f"{index:04d}-{full_sha}.diff"
    patch_path.write_bytes(patch)

    return {
        "index": index,
        "sha": full_sha,
        "short_sha": short_sha,
        "parents": parents,
        "first_parent": first_parent,
        "is_merge": len(parents) > 1,
        "author": author,
        "author_email": email,
        "authored_at": authored_at,
        "committed_at": committed_at,
        "subject": subject,
        "changed_file_count": len(files),
        "changed_files": files,
        "short_stat": stat,
        "heuristic_hint": commit_hint(files),
        "patch_file": str(patch_path),
        "patch_bytes": len(patch),
    }


def escape_markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_markdown(data: dict[str, object], output: Path) -> None:
    baseline = data["baseline"]
    repository = data["repository"]
    commits = data["commits"]
    lines = [
        "# Production Baseline Evidence",
        "",
        "## Baseline",
        "",
        f"- Repository: `{escape_markdown(repository['root'])}`",
        f"- Production tag: `{escape_markdown(baseline['production_tag'])}`",
        f"- Supplied production commit: `{escape_markdown(baseline['production_commit_input'])}`",
        f"- Production commit: `{escape_markdown(baseline['production_commit'])}`",
        f"- Default branch: `{escape_markdown(baseline['default_branch'])}`",
        f"- Default head: `{escape_markdown(baseline['default_head'])}`",
        f"- Post-production commits: **{len(commits)}**",
        "",
        "## Commits to review",
        "",
    ]
    if not commits:
        lines.append("No commits exist after the production commit on the default branch.")
    else:
        lines.extend(
            [
                "| # | Commit | Date | Author | Heuristic only | Files | Subject |",
                "|---:|---|---|---|---|---:|---|",
            ]
        )
        for commit in commits:
            lines.append(
                "| {index} | `{short}` | {date} | {author} | {hint} | {files} | {subject} |".format(
                    index=commit["index"],
                    short=escape_markdown(commit["short_sha"]),
                    date=escape_markdown(str(commit["committed_at"])[:10]),
                    author=escape_markdown(commit["author"]),
                    hint=escape_markdown(commit["heuristic_hint"]),
                    files=commit["changed_file_count"],
                    subject=escape_markdown(commit["subject"]),
                )
            )
    lines.extend(
        [
            "",
            "> The hints are not final classifications. Review every patch before deciding.",
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect commits after a validated production tag + commit pair."
    )
    parser.add_argument(
        "--production-tag",
        required=True,
        help="Exact Git tag used for production, for example az0.1.0-43",
    )
    parser.add_argument(
        "--production-commit",
        required=True,
        help="Full SHA or unambiguous short SHA associated with the production tag",
    )
    parser.add_argument(
        "--default-branch",
        help="Branch to analyze. Defaults to main; provide another branch only when explicitly requested.",
    )
    parser.add_argument("--remote", default="origin", help="Git remote name")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch tags and remote refs before inspection",
    )
    parser.add_argument(
        "--output-dir",
        default="baseline-analysis",
        help="Directory for JSON, Markdown, and patches",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repo = resolve_repo(Path.cwd())
        remote_present = bool(git(repo, "remote", "get-url", args.remote, check=False))
        if args.fetch:
            if not remote_present:
                raise RuntimeError(f"Remote '{args.remote}' does not exist")
            git(repo, "fetch", "--prune", "--tags", args.remote)

        default_branch = resolve_default_branch(
            repo,
            remote=args.remote,
            explicit_branch=args.default_branch,
        )
        production_from_tag = resolve_tag_commit(repo, args.production_tag)
        production_from_input = resolve_commit(repo, args.production_commit)
        if production_from_tag != production_from_input:
            raise RuntimeError(
                "Production tag/commit mismatch: "
                f"tag '{args.production_tag}' resolves to {production_from_tag}, "
                f"but supplied commit '{args.production_commit}' resolves to {production_from_input}."
            )
        production = production_from_tag

        ancestry = run_command(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", production, default_branch.head],
            check=False,
        )
        if ancestry.returncode != 0:
            raise RuntimeError(
                "The production commit is not an ancestor of the default branch head. "
                "The histories diverge; stop and resolve the baseline manually."
            )
        if not first_parent_is_ancestor(repo, production, default_branch.head):
            raise RuntimeError(
                "The production commit is an ancestor but is not on the default branch first-parent chain. "
                "This workflow cannot safely define post-production commits without manual review."
            )

        output_dir = Path(args.output_dir)
        if not output_dir.is_absolute():
            output_dir = (Path.cwd() / output_dir).resolve()
        patch_dir = output_dir / "patches"
        if patch_dir.exists():
            shutil.rmtree(patch_dir)
        patch_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        commit_shas = git(
            repo,
            "rev-list",
            "--first-parent",
            "--reverse",
            f"{production}..{default_branch.head}",
        ).splitlines()
        commits = [
            collect_commit(repo, sha, index, patch_dir)
            for index, sha in enumerate(commit_shas, start=1)
            if sha.strip()
        ]

        status_porcelain = git(repo, "status", "--porcelain", "--untracked-files=normal")
        data: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now.isoformat(),
            "status": "ALIGNED" if not commits else "READY_FOR_CLASSIFICATION",
            "repository": {
                "root": str(repo),
                "remote": args.remote if remote_present else None,
                "working_tree_dirty": bool(status_porcelain),
            },
            "baseline": {
                "production_tag": args.production_tag,
                "production_commit_input": args.production_commit,
                "production_commit": production,
                "production_short_sha": git(repo, "rev-parse", "--short=12", production),
                "default_branch": default_branch.name,
                "default_ref": default_branch.ref,
                "default_branch_source": default_branch.source,
                "default_head": default_branch.head,
                "default_head_short_sha": git(
                    repo, "rev-parse", "--short=12", default_branch.head
                ),
                "ancestry_verified": True,
                "first_parent_verified": True,
                "history_mode": "first-parent",
                "post_production_commit_count": len(commits),
            },
            "commits": commits,
            "classification_warning": (
                "Heuristic hints are evidence aids only. Review every patch and assign "
                "ARCHITECTURE, LOGIC, or UNCERTAIN."
            ),
        }

        json_path = output_dir / "production-baseline-evidence.json"
        markdown_path = output_dir / "production-baseline-evidence.md"
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        write_markdown(data, markdown_path)

        print(f"Evidence JSON: {json_path}")
        print(f"Evidence Markdown: {markdown_path}")
        print(f"Commits after production: {len(commits)}")
        return 0
    except (RuntimeError, CommandError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
