"""Runner registry, probes, and governed one-shot dispatch (front-half spec Sec6).

A worker is file-in, file-out: worktree + brief + context in, commits +
status token out. Nothing requires it to be a subagent of the controller's
harness. The optional `config.json` `runners` map makes a `model` value
resolvable to an external agent CLI (a command-argv template with
`{worktree}`/`{prompt}`/`{logfile}` placeholders); `dispatch` execs it in the
task's worktree and captures its output.

Probing must not require the runner to already be ratified config (a
governance cycle) and must not silently execute unapproved commands either.
`--probe-command` tests an explicit candidate the user just typed --
deliberately UNGOVERNED, exec'd in a scratch temp dir with a canned trivial
prompt. `--probe NAME` re-verifies an already-ratified runner and IS governed
(it executes config-loaded commands). Either way, a passing probe records
`probes[sha256(command)] = {at, ok: true}` as an ungoverned observation --
the `monitor.py` precedent (see `record_probe`) -- and `dispatch --task`
refuses a runner whose ratified command digest has no passing probe record.
Probe-then-edit breaks the digest match and re-refuses.

No streaming, no interactive sessions, no supervision or retries: one-shot
headless exec with exit-code semantics is the whole mechanism (hard
non-goals, spec Sec6).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .engine import apply_mutation, utc_now
from .errors import IllegalTransition, ValidationError
from .git import require_repository, worktree_for
from .handover import ensure_dispatch_gitignore, materialize_attempt
from .review import REVIEW_RESULT_KIND, evidence_shas, normalize_review_results
from .schema import copied_state
from .store import StateStore, atomic_write_text


# Same character class as finalize.py's _sanitize_scope_id_for_filename /
# handover.py's _sanitize_task_id_for_filename (the archive idiom): task ids
# double as filesystem path components under .wdd/dispatch/, so anything
# outside [A-Za-z0-9._-] is replaced rather than trusted verbatim.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")

_DISPATCH_DIR_MODE = 0o700
_DISPATCH_FILE_MODE = 0o600

PROBE_PROMPT = "Reply with exactly: DONE"
PROBE_TOKEN = "DONE"

# Worker's status-token contract (skills/wdd-worker/SKILL.md's "Final
# status"): dispatch reads whichever of these appears on the trailing
# non-empty output line, if any.
STATUS_TOKENS = {"DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"}

# Bounded tail carried in the result payload; the file on disk holds the rest
# (spec Sec6 log policy).
_LOG_TAIL_BYTES = 4096


def _sanitize_task_id_for_filename(task_id: str) -> str:
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", task_id)
    if not sanitized:
        raise ValidationError(f"task id sanitizes to an empty dispatch filename: {task_id!r}")
    return sanitized


def runner_command_digest(command: list[str]) -> str:
    """sha256 of the canonical JSON of an argv template (spec Sec6).

    The digest is over the exact command BYTES (including any
    `{worktree}`/`{prompt}`/`{logfile}` placeholders, unsubstituted): a
    probe's guarantee follows the exact command a config records, so
    probing then editing the command breaks the match on purpose.
    """
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) and item for item in command
    ):
        raise ValidationError("runner command must be a non-empty list of non-empty strings")
    encoded = json.dumps(command, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _substitute_placeholders(
    command: list[str], *, worktree: Path, prompt: Path, logfile: Path
) -> list[str]:
    """Replace `{worktree}`/`{prompt}`/`{logfile}` anywhere they appear.

    Substring replacement, not `str.format`: a runner command legitimately
    containing other brace text must not raise, and the placeholders may
    appear anywhere inside an argv element (config.py's validation doc),
    not only as a whole element.
    """
    mapping = {
        "{worktree}": str(worktree),
        "{prompt}": str(prompt),
        "{logfile}": str(logfile),
    }
    substituted = []
    for arg in command:
        for placeholder, value in mapping.items():
            arg = arg.replace(placeholder, value)
        substituted.append(arg)
    return substituted


def _trailing_nonempty_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _bounded_tail(text: str, *, limit: int = _LOG_TAIL_BYTES) -> str:
    return text.encode("utf-8")[-limit:].decode("utf-8", errors="replace")


def _write_dispatch_file(path: Path, content: str) -> None:
    """Write a dispatch-scratch file with the log-policy permission (0600)."""
    atomic_write_text(path, content)
    os.chmod(path, _DISPATCH_FILE_MODE)


def probe_command(command: list[str], *, timeout: int | float | None = 120) -> dict[str, Any]:
    """Exec a candidate runner command in a scratch temp dir with a canned prompt.

    Returns ``{"ok", "exitCode", "wallMs", "tokenSeen"}``. ``ok`` requires
    both a zero exit code AND the trailing non-empty line of output equal to
    "DONE" -- a runner that exits 0 without ever answering the prompt has not
    proven anything. Pure function: never touches state or config; the
    caller decides what to do with the result (`record_probe` on success).
    """
    runner_command_digest(command)  # validates shape before spawning anything
    with tempfile.TemporaryDirectory(prefix="wddctl-probe-") as tmp:
        worktree = Path(tmp)
        prompt_path = worktree / "prompt.txt"
        logfile_path = worktree / "probe.log"
        _write_dispatch_file(prompt_path, PROBE_PROMPT + "\n")
        argv = _substitute_placeholders(
            command, worktree=worktree, prompt=prompt_path, logfile=logfile_path
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=worktree,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            exit_code: int | None = completed.returncode
            output = completed.stdout or ""
        except subprocess.TimeoutExpired as error:
            exit_code = None
            captured = error.stdout
            output = captured if isinstance(captured, str) else ""
        except OSError as error:
            exit_code = None
            output = f"wddctl: could not exec runner command: {error}"
        wall_ms = int((time.monotonic() - started) * 1000)
        token_seen = _trailing_nonempty_line(output) == PROBE_TOKEN
        return {
            "ok": exit_code == 0 and token_seen,
            "exitCode": exit_code,
            "wallMs": wall_ms,
            "tokenSeen": token_seen,
        }


def record_probe(
    store: StateStore,
    *,
    command: list[str],
    idempotency_key: str | None = None,
    expected_revision: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """Record a PASSING probe's digest as an ungoverned observation.

    Only ever called after `probe_command` reports ``ok: True`` -- callers
    must not invoke this for a failed probe. This bypasses `engine.transition`
    entirely (a raw `apply_mutation` with a handwritten mutator, like
    `monitor.monitor_once`'s own observation write): ratification is
    deliberately NOT required, because a runner must be provably usable
    before it is ever asked to be config, and gating the proof on
    ratification would be the governance cycle spec Sec6 explicitly rules
    out. Requires `store.exists()` -- the CLI is responsible for the
    "no state.json yet" fallback (report, don't record).
    """
    digest = runner_command_digest(command)

    def mutator(state: dict[str, Any]) -> dict[str, Any]:
        updated = copied_state(state)
        probes = dict(updated.get("probes") or {})
        probes[digest] = {"at": utc_now(), "ok": True}
        updated["probes"] = probes
        return updated

    return apply_mutation(
        store,
        event_type="runner.probed",
        task_id=None,
        data={"digest": digest},
        idempotency_key=idempotency_key,
        expected_revision=expected_revision,
        mutator=mutator,
    )


def _resolve_dispatch_model(
    task: dict[str, Any], config: dict[str, Any] | None, *, role: str
) -> str | None:
    """The model dispatch routes to for one task+role.

    Mirrors `engine._resolve_model`'s precedence exactly (task override ->
    risk-tiered config -> absent), resolved directly for a single task/role
    rather than through the `next` action-decoration pipeline (dispatch has
    no action dict to decorate).
    """
    override = task.get("model") if role == "worker" else task.get("reviewModel")
    if isinstance(override, str) and override:
        return override
    if not config:
        return None
    models = config.get("models") or {}
    tier = "highRisk" if task.get("risk") == "high" else "default"
    if role == "worker":
        implementation = models.get("implementation") or {}
        value = implementation.get(tier)
    else:
        review = models.get("review")
        value = review.get(tier) if isinstance(review, dict) else review
    return value if isinstance(value, str) and value else None


def _runner_command_for_model(config: dict[str, Any] | None, model: str | None) -> list[str]:
    runners = (config or {}).get("runners") or {}
    if not isinstance(model, str) or not model or model not in runners:
        raise IllegalTransition(
            f"model {model!r} is not a configured runner; harness-native dispatch is the "
            "controller's job"
        )
    return list(runners[model]["command"])


def _require_passing_probe(state: dict[str, Any], command: list[str]) -> str:
    digest = runner_command_digest(command)
    probes = state.get("probes") or {}
    entry = probes.get(digest)
    if not isinstance(entry, dict) or entry.get("ok") is not True:
        raise IllegalTransition(
            f"runner command has no passing probe record (digest {digest}); probe it first "
            "with 'wddctl dispatch --probe-command '[...]'' or 'wddctl dispatch --probe NAME'"
        )
    return digest


def _extract_section(text: str, heading: str) -> str | None:
    """The body of one `## <heading>` markdown section, or None if absent.

    Same convention as intake.py's `_headings`/`_section_lines` (a smaller,
    local copy: this only ever needs one named section, not the general
    heading-index machinery those callers build)."""
    lines = text.splitlines()
    target = heading.strip().lower()
    start: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match and match.group(1).strip().lower() == target:
            start = index + 1
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        if re.match(r"^##\s+", lines[index]):
            end = index
            break
    section = "\n".join(lines[start:end]).strip()
    return section or None


def _snapshot_files(snapshot_root: Path, brief_relative: Path) -> tuple[Path | None, list[Path]]:
    """(brief absolute path, context absolute paths) actually present in a snapshot dir.

    Enumerates the snapshot's own contents rather than re-deriving the task's
    CURRENT brief/context fields: workers and reviewers must see exactly what
    was materialized (spec Sec3 -- "never live controller files"), and this
    works identically for legacy scopes (whose `task["inputs"]` is recorded
    empty on purpose) since it never reads that field at all.
    """
    files = sorted(path for path in snapshot_root.rglob("*") if path.is_file())
    brief_path: Path | None = None
    context_paths: list[Path] = []
    for path in files:
        if path.relative_to(snapshot_root) == brief_relative:
            brief_path = path
        else:
            context_paths.append(path)
    if brief_path is None and files:
        # Defensive: specPath didn't line up with anything on disk (should
        # not happen for a well-formed task). Treat the first file as the
        # brief rather than silently omitting it from the packet.
        brief_path, context_paths = files[0], files[1:]
    return brief_path, context_paths


def _worker_prompt(task_id: str, brief_path: Path, context_paths: list[Path], deliverable: str | None) -> str:
    lines = [
        f"WDD dispatch: worker role for task {task_id}.",
        "You are an external runner executing exactly one WDD task in its worktree.",
        "Do not run wddctl yourself; the controller records your submission.",
        "",
        f"Brief: {brief_path}",
    ]
    if context_paths:
        lines.append("Context:")
        lines.extend(f"  - {path}" for path in context_paths)
    lines += [
        "",
        "Deliverable:",
        deliverable or "(see the Deliverable section of the brief above)",
        "",
        "End your output with exactly one status token on its own trailing line:",
        "DONE (objective met, verification passed), DONE_WITH_CONCERNS (delivered, flag "
        "something), NEEDS_CONTEXT (blocked on missing information), or BLOCKED (cannot "
        "proceed).",
    ]
    return "\n".join(lines) + "\n"


def _reviewer_prompt(
    task_id: str,
    brief_path: Path,
    context_paths: list[Path],
    deliverable: str | None,
    *,
    base_sha: str,
    head_sha: str,
) -> str:
    contract = {
        "schemaVersion": 1,
        "kind": REVIEW_RESULT_KIND,
        "task": task_id,
        "baseSha": base_sha,
        "headSha": head_sha,
        "reviewer": "<your name>",
        "findings": [{"severity": "P1|P2|P3", "summary": "...", "file": "...", "line": 1}],
    }
    lines = [
        f"WDD dispatch: reviewer role for task {task_id}.",
        f"Review the diff {base_sha}..{head_sha} in this worktree.",
        "Do not run wddctl yourself; the controller records your findings via 'review collect'.",
        "",
        f"Brief: {brief_path}",
    ]
    if context_paths:
        lines.append("Context:")
        lines.extend(f"  - {path}" for path in context_paths)
    lines += [
        "",
        "Deliverable:",
        deliverable or "(see the Deliverable section of the brief above)",
        "",
        "Emit EXACTLY one JSON object on stdout and nothing else, shaped like:",
        json.dumps(contract, indent=2),
        "findings may be an empty list for a clean review.",
    ]
    return "\n".join(lines) + "\n"


def _next_log_attempt(dispatch_dir: Path, sanitized: str, role: str) -> int:
    prefix = f"{sanitized}-{role}-"
    existing = [
        entry
        for entry in dispatch_dir.glob(f"{prefix}*.log")
        if entry.name[len(prefix) : -len(".log")].isdigit()
    ]
    return 1 + len(existing)


def dispatch_task(
    state: dict[str, Any],
    config: dict[str, Any] | None,
    *,
    repo: Path | str,
    wdd_dir: Path | str,
    task_id: str,
    role: str,
    timeout: int | float | None = None,
) -> dict[str, Any]:
    """Assemble the dispatch packet, exec the resolved runner, capture output.

    No state mutation happens here -- the caller records the outcome with
    the ordinary verbs (`submit` for a worker, `review collect` for a
    reviewer's written `-result.json`), exactly as it would for a
    harness-native dispatch. This function only ever raises for OUR OWN
    precondition failures (unknown task, unconfigured/unprobed runner, no
    worktree, a reviewer's output that fails its own declared contract on an
    otherwise-successful exit); a runner-side failure (nonzero exit, no
    status token, timeout) is reported in the returned dict, never an
    exception -- the exit code IS the signal (spec Sec6: one-shot,
    exit-code semantics, no retries).
    """
    if role not in {"worker", "reviewer"}:
        raise ValidationError("dispatch --role must be worker or reviewer")
    try:
        task = state["tasks"][task_id]
    except KeyError as error:
        raise ValidationError(f"unknown task: {task_id}") from error

    # Resolved up front (not just where a snapshot path is read): dispatch_dir
    # below, and the prompt/log paths built from it, are substituted into
    # argv for a subprocess run with cwd=worktree -- a DIFFERENT directory --
    # so a still-relative path there would resolve against the wrong cwd
    # inside the runner process. The CLI's own default `--state` resolves to
    # the relative `.wdd/state.json`, so this is the common case, not an
    # edge case (same fix as materialize_attempt's, handover.py).
    wdd_dir = Path(wdd_dir).resolve()
    repo = require_repository(repo)

    model = _resolve_dispatch_model(task, config, role=role)
    command = _runner_command_for_model(config, model)
    digest = _require_passing_probe(state, command)

    # worktrees.root is only ever consulted to locate this task's EXISTING
    # worktree (never to create one -- 'start' already did): the task's own
    # recorded `worktree` override, when set, wins regardless (git.worktree_for).
    worktrees_root = ((config or {}).get("worktrees") or {}).get("root")
    worktree = worktree_for(
        repo, state["scope"]["id"], task_id, task.get("worktree"),
        worktrees_root=worktrees_root,
    )
    if not worktree.is_dir():
        raise IllegalTransition(
            f"task {task_id} has no worktree at {worktree}; run 'wddctl start' first"
        )

    base_sha: str | None = None
    head_sha: str | None = None
    if role == "reviewer":
        base_sha, head_sha = evidence_shas(state, task_id, repo=repo)
        materialized = materialize_attempt(state, wdd_dir, task_id)
        snapshot = materialized["snapshot"]
    else:
        snapshot = task.get("snapshot")
        if not snapshot:
            raise IllegalTransition(
                f"task {task_id} has no recorded attempt snapshot; run 'wddctl start' first"
            )

    # Cross-reference: the snapshot's internal layout mirrors
    # `wave_delivery/paths.py`'s `resolve_artifact` (spec Sec1, Global
    # Constraints "one resolver") -- `materialize_attempt`/
    # `_task_input_sources` (handover.py) resolved every source file
    # through it before copying, so `task["specPath"]`'s relative form is
    # the same key the snapshot was written under. Task 1's `epic=None`
    # transition mode keeps that relative form flat (`tasks/<id>.md`);
    # Task 4, once refs resolve under `epics/<epic>/...`, must update this
    # comparison in lockstep or a started task's brief lookup would miss.
    snapshot_root = wdd_dir / snapshot
    brief_path, context_paths = _snapshot_files(snapshot_root, Path(task["specPath"]))
    brief_text = brief_path.read_text(encoding="utf-8") if brief_path else ""
    deliverable = _extract_section(brief_text, "Deliverable")

    if role == "worker":
        prompt_text = _worker_prompt(task_id, brief_path, context_paths, deliverable)
    else:
        prompt_text = _reviewer_prompt(
            task_id, brief_path, context_paths, deliverable,
            base_sha=base_sha, head_sha=head_sha,
        )

    dispatch_dir = wdd_dir / "dispatch"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(dispatch_dir, _DISPATCH_DIR_MODE)
    # Same rationale as handover.py's materialize_attempt: an existing
    # install that predates dispatch/ never reaches migrate_governance's
    # ensure_dispatch_gitignore call (it early-returns once config.json
    # already exists), so this is ensured at the point dispatch/ actually
    # starts existing instead. Idempotent, content-preserving.
    ensure_dispatch_gitignore(wdd_dir)
    sanitized = _sanitize_task_id_for_filename(task_id)
    attempt = _next_log_attempt(dispatch_dir, sanitized, role)
    base_name = f"{sanitized}-{role}-{attempt}"
    prompt_path = dispatch_dir / f"{base_name}.prompt"
    log_path = dispatch_dir / f"{base_name}.log"
    # A distinct sibling path, not log_path: log_path is also the target of
    # wddctl's own post-run _write_dispatch_file(log_path, output) below (an
    # atomic os.replace). A runner that also writes to {logfile} itself --
    # its own transcript, not wddctl's capture of stdout+stderr -- would
    # otherwise have that file clobbered the moment wddctl's own write lands.
    runner_log_path = dispatch_dir / f"{base_name}-runner.log"
    _write_dispatch_file(prompt_path, prompt_text)

    argv = _substitute_placeholders(
        command, worktree=worktree, prompt=prompt_path, logfile=runner_log_path
    )
    environment = os.environ.copy()
    environment["WDDCTL_DISPATCH_TASK"] = task_id
    environment["WDDCTL_DISPATCH_ROLE"] = role
    if role == "reviewer":
        # Same env var names review.py's run_review already sets: the
        # reviewer contract is the same one internal reviewers speak, so the
        # SHA-binding channel is the same too, not a parallel invention.
        environment["WDDCTL_REVIEW_TASK"] = task_id
        environment["WDDCTL_REVIEW_BASE_SHA"] = base_sha
        environment["WDDCTL_REVIEW_HEAD_SHA"] = head_sha

    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=worktree,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        exit_code: int | None = completed.returncode
        output = completed.stdout or ""
    except subprocess.TimeoutExpired as error:
        timed_out = True
        exit_code = None
        captured = error.stdout
        output = captured if isinstance(captured, str) else ""
    except OSError as error:
        exit_code = None
        output = f"wddctl: could not exec runner command: {error}"
    wall_ms = int((time.monotonic() - started) * 1000)

    _write_dispatch_file(log_path, output)
    tail = _bounded_tail(output)

    result: dict[str, Any] = {
        "task": task_id,
        "role": role,
        "model": model,
        "digest": digest,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "wallMs": wall_ms,
        "log": str(log_path),
        "tail": tail,
    }

    if role == "worker":
        token = _trailing_nonempty_line(output)
        result["statusToken"] = token if token in STATUS_TOKENS else None
        return result

    # role == "reviewer": only a genuinely successful exit is held to the
    # JSON contract -- a nonzero exit (or timeout) is reported as a plain
    # failure, the same as a worker's; there is nothing to validate.
    result["resultPath"] = None
    if exit_code != 0:
        return result
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"reviewer dispatch for {task_id} exited 0 but did not emit a JSON "
            f"{REVIEW_RESULT_KIND} object: {error}"
        ) from error
    result_path = dispatch_dir / f"{base_name}-result.json"
    _write_dispatch_file(result_path, json.dumps(parsed, indent=2, sort_keys=True) + "\n")
    # Reuses review.py's own validator, so `review collect` and this write
    # path enforce byte-for-byte the same contract (kind/schemaVersion,
    # required fields, SHA consistency across a multi-result collect --
    # trivial here with one file, but the same function either way).
    normalized = normalize_review_results([result_path], task_id=task_id)
    if normalized["baseSha"] != base_sha or normalized["headSha"] != head_sha:
        raise ValidationError(
            f"reviewer dispatch result for {task_id} does not match the frozen "
            f"{base_sha}..{head_sha} range"
        )
    result["resultPath"] = str(result_path)
    result["findings"] = len(normalized["findings"])
    return result
