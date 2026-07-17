"""Repository evidence and decision fingerprints for constitution ratification."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .store import atomic_write_text


def _git_branch(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    branch = result.stdout.strip()
    return branch or None


def _git_target_branch(root: Path) -> str:
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    remote_head = result.stdout.strip()
    if remote_head.startswith("origin/"):
        return remote_head.removeprefix("origin/")
    main = subprocess.run(
        ["git", "branch", "--list", "main"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if main.stdout.strip():
        return "main"
    return _git_branch(root) or "main"


def decision_fingerprint(proposal: dict[str, Any]) -> str:
    decisions = proposal.get("decisions")
    if not isinstance(decisions, dict):
        raise ValidationError("constitution proposal must contain a decisions object")
    encoded = json.dumps(decisions, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def probe_repository(root: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValidationError(f"repository root does not exist: {root}")
    instruction_files = [
        str(path.relative_to(root))
        for path in sorted(root.glob("AGENTS.md")) + sorted(root.glob(".agents/AGENTS.md"))
    ]
    verification_commands: list[str] = []
    if (root / "tests").is_dir():
        verification_commands.append("python3 -m unittest discover -s tests")
    if (root / "package.json").is_file():
        verification_commands.append("npm test")
    if (root / "scripts" / "test_wdd_worktree_cleanup.py").is_file():
        verification_commands.append("python3 scripts/test_wdd_worktree_cleanup.py")
    languages: list[str] = []
    if list(root.rglob("*.py")):
        languages.append("python")
    if list(root.rglob("*.js")) or list(root.rglob("*.ts")):
        languages.append("javascript-typescript")
    target_branch = _git_target_branch(root)
    proposal = {
        "schemaVersion": 1,
        "kind": "wddctl_constitution_proposal",
        "repository": str(root),
        "evidence": {
            "instructionFiles": instruction_files,
            "languages": languages,
            "verificationCommands": verification_commands,
            "gitBranch": _git_branch(root),
            "hasWddDirectory": (root / ".wdd").is_dir(),
        },
        "decisions": {
            "storageMode": "local-markdown",
            "targetBranch": target_branch,
            "epicBranchConvention": "epic/[epic-slug]",
            "taskBranchConvention": "task/[task-id]-[task-slug]",
            "profileDefault": "standard",
            "reviewModeDefault": "risk_based",
            "monitoringModeDefault": "adaptive",
            "controllerMode": "schema_v2_controller_governed",
        },
    }
    proposal["decisionFingerprint"] = decision_fingerprint(proposal)
    return proposal


def write_proposal(path: Path | str, proposal: dict[str, Any]) -> None:
    atomic_write_text(Path(path), json.dumps(proposal, indent=2, sort_keys=True) + "\n")


def read_proposal(path: Path | str) -> dict[str, Any]:
    try:
        proposal = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(f"constitution proposal does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"constitution proposal is not valid JSON: {error}") from error
    computed = decision_fingerprint(proposal)
    stored = proposal.get("decisionFingerprint")
    if stored is not None and stored != computed:
        raise ValidationError("constitution proposal fingerprint does not match its decisions")
    proposal["decisionFingerprint"] = computed
    return proposal


def ratification_status(state: dict[str, Any], proposal: dict[str, Any] | None) -> dict[str, Any]:
    ratification = state["constitution"].get("ratification")
    result: dict[str, Any] = {
        "status": state["constitution"]["status"],
        "ratification": ratification,
        "stale": None,
    }
    if proposal is not None:
        fingerprint = proposal["decisionFingerprint"]
        result["proposalFingerprint"] = fingerprint
        result["stale"] = not isinstance(ratification, dict) or ratification.get(
            "decisionFingerprint"
        ) != fingerprint
    return result
