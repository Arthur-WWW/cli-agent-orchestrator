"""Main orchestration runner for phase-driven CAO HTTP workflow."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.orchestrator.cao_client import (
    CaoHttpClient,
    CaoWaitTimeoutError,
)
from cli_agent_orchestrator.orchestrator.intent_analyzer import (
    IntentAnalyzer,
    IntentAnalyzerConfig,
)
from cli_agent_orchestrator.orchestrator.models import (
    CommitResult,
    IntentAnalysis,
    IntentDecision,
    OrchestratorConfig,
    PhaseStatus,
    RunState,
)
from cli_agent_orchestrator.orchestrator.prompts import (
    build_commit_prompt,
    build_developer_prompt,
    build_intent_analysis_prompt,
    build_reviewer_clarification_prompt,
    build_reviewer_prompt,
)

logger = logging.getLogger(__name__)
FIXED_COMMIT_MESSAGE_TEMPLATE = "phase({phase}): implement approved changes"
STAGNATION_SIMILARITY_THRESHOLD = 0.8
STAGNATION_MAX_ADDED_ITEMS = 1
STAGNATION_BLOCK_THRESHOLD = 2


class PhaseBlockedError(RuntimeError):
    """Raised when current phase is blocked and cannot continue automatically."""


def _run_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _safe_phase_slug(phase: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", phase).strip("-")
    return normalized or "phase"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _read_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"JSON at {path} is not an object")
    return data


def parse_commit_result(output: str) -> CommitResult:
    """Parse commit step output into structured result."""
    text = output.strip()
    success_match = re.search(r"success\s*[:=]\s*(true|false)", text, re.IGNORECASE)
    sha_match = re.search(r"commit[_\s-]?sha\s*[:=]\s*([0-9a-f]{7,40})", text, re.IGNORECASE)
    msg_match = re.search(r"commit[_\s-]?message\s*[:=]\s*(.+)", text, re.IGNORECASE)
    reason_match = re.search(r"reason\s*[:=]\s*(.+)", text, re.IGNORECASE)

    commit_sha = sha_match.group(1) if sha_match else None
    commit_message = msg_match.group(1).strip() if msg_match else None
    reason = reason_match.group(1).strip() if reason_match else None

    if success_match is not None:
        success = success_match.group(1).lower() == "true"
        if success and commit_sha:
            return CommitResult(
                success=True,
                commit_sha=commit_sha,
                commit_message=commit_message,
                reason=None,
            )
        if success and not commit_sha:
            return CommitResult(success=False, reason="Commit marked success but SHA missing")
        return CommitResult(success=False, reason=reason or "Developer reported commit failure")

    # Fallback parse from git commit canonical output: [branch abc1234] message
    fallback = re.search(r"\[[^\]]+\s([0-9a-f]{7,40})\]\s+(.+)", text)
    if fallback:
        return CommitResult(
            success=True, commit_sha=fallback.group(1), commit_message=fallback.group(2)
        )

    lower = text.lower()
    if "nothing to commit" in lower:
        return CommitResult(success=False, reason="No changes were committed (nothing to commit)")
    if "fatal:" in lower or "error" in lower:
        return CommitResult(success=False, reason="Commit output contains fatal/error")

    return CommitResult(success=False, reason="Unable to parse commit result")


def _normalize_issue_text(issue: str) -> str:
    """Normalize reviewer must-fix text for stable cross-round comparison."""
    text = issue.lower().strip()
    text = re.sub(r"(?<=:)\d+(?::\d+)?\b", "n", text)
    text = re.sub(r"\b(line|ln|#l)\s*\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _issue_fingerprint(issue: str) -> str:
    normalized = _normalize_issue_text(issue)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def build_issue_fingerprints(items: List[str]) -> List[str]:
    fingerprints: Set[str] = set()
    for item in items:
        if not item.strip():
            continue
        fingerprint = _issue_fingerprint(item)
        if fingerprint:
            fingerprints.add(fingerprint)
    return sorted(fingerprints)


def detect_stagnation(
    previous_fingerprints: List[str],
    current_fingerprints: List[str],
) -> Tuple[bool, float, int, int]:
    """Detect whether current must-fix set is effectively unchanged."""
    previous_set = set(previous_fingerprints)
    current_set = set(current_fingerprints)
    if not previous_set or not current_set:
        return False, 0.0, len(previous_set), len(current_set)

    intersection = previous_set.intersection(current_set)
    union = previous_set.union(current_set)
    similarity = len(intersection) / len(union)
    resolved_count = len(previous_set.difference(current_set))
    added_count = len(current_set.difference(previous_set))

    stagnant = (
        resolved_count == 0
        and similarity >= STAGNATION_SIMILARITY_THRESHOLD
        and added_count <= STAGNATION_MAX_ADDED_ITEMS
    )
    return stagnant, similarity, resolved_count, added_count


class OrchestratorRunner:
    """Coordinates developer/reviewer terminals for one phase."""

    def __init__(
        self,
        config: OrchestratorConfig,
        run_dir: Path,
        state: RunState,
        client: Optional[CaoHttpClient] = None,
        analyzer: Optional[IntentAnalyzer] = None,
    ):
        self.config = config
        self.run_dir = run_dir
        self.state = state
        self.client = client or CaoHttpClient(config.cao_base_url)
        self.analyzer = analyzer or IntentAnalyzer(
            IntentAnalyzerConfig(provider=config.intent_provider)
        )

    @property
    def state_path(self) -> Path:
        return self.run_dir / "run_state.json"

    @classmethod
    def create_new(cls, config: OrchestratorConfig) -> "OrchestratorRunner":
        run_id = _run_id_now()
        run_dir = config.run_root / run_id
        state = RunState(
            run_id=run_id,
            design_doc=str(config.design_doc_path.resolve()),
            plan_doc=str(config.plan_doc_path.resolve()),
            developer_terminal_id=config.developer_terminal_id,
            reviewer_terminal_id=config.reviewer_terminal_id,
            current_phase=config.phase_selector,
            phase_status=PhaseStatus.IN_PROGRESS,
            round=1,
            last_transition="created",
            last_phase_commit=None,
            previous_must_fix=[],
            config=config.to_dict(),
        )
        runner = cls(config=config, run_dir=run_dir, state=state)
        runner._save_state()
        return runner

    @classmethod
    def from_state_path(cls, state_path: Path) -> "OrchestratorRunner":
        data = _read_json(state_path)
        state = RunState.from_dict(data)
        if not isinstance(state.config, dict):
            raise ValueError(f"Run state at {state_path} missing config field")
        config = OrchestratorConfig.from_dict(state.config)
        run_dir = state_path.parent
        return cls(config=config, run_dir=run_dir, state=state)

    def run(self) -> Path:
        self._preflight_checks()

        if self.state.phase_status in (PhaseStatus.PASSED, PhaseStatus.FAILED):
            logger.info("Run already completed with status=%s", self.state.phase_status.value)
            return self.run_dir

        self.state.phase_status = PhaseStatus.IN_PROGRESS
        self.state.touch("preflight_ok")
        self._save_state()

        design_doc = self.config.design_doc_path.read_text()
        plan_doc = self.config.plan_doc_path.read_text()

        round_idx = max(1, self.state.round)
        while True:
            self.state.round = round_idx
            self.state.touch("phase_implementing")
            self._save_state()

            phase_dir = self._phase_dir()
            round_dir = phase_dir / f"round-{round_idx}"

            developer_prompt = build_developer_prompt(
                phase=self.state.current_phase,
                design_doc=design_doc,
                plan_doc=plan_doc,
                previous_must_fix=self.state.previous_must_fix,
            )
            _write_text(round_dir / "developer_prompt.md", developer_prompt)

            developer_output = self._send_and_capture(
                terminal_id=self.state.developer_terminal_id,
                message=developer_prompt,
                transition="wait_developer",
            )
            _write_text(round_dir / "developer_output.md", developer_output)

            reviewer_prompt = build_reviewer_prompt(self.state.current_phase, developer_output)
            _write_text(round_dir / "reviewer_prompt.md", reviewer_prompt)

            reviewer_output = self._send_and_capture(
                terminal_id=self.state.reviewer_terminal_id,
                message=reviewer_prompt,
                transition="wait_reviewer",
            )
            _write_text(round_dir / "reviewer_output.md", reviewer_output)

            analysis = self.analyzer.analyze(reviewer_output, self.state.current_phase)

            if analysis.decision == IntentDecision.UNCLEAR:
                analysis = self._analyze_unclear_with_provider(round_dir, reviewer_output)

            if analysis.decision == IntentDecision.UNCLEAR:
                clarification_prompt = build_reviewer_clarification_prompt()
                _write_text(round_dir / "reviewer_clarification_prompt.md", clarification_prompt)
                clarification_output = self._send_and_capture(
                    terminal_id=self.state.reviewer_terminal_id,
                    message=clarification_prompt,
                    transition="wait_reviewer_clarification",
                )
                _write_text(round_dir / "reviewer_clarification_output.md", clarification_output)
                clarification = self.analyzer.analyze_clarification(clarification_output)
                if clarification.decision == IntentDecision.UNCLEAR:
                    self.state.phase_status = PhaseStatus.BLOCKED
                    self.state.touch("review_unclear_blocked")
                    self._save_state()
                    raise PhaseBlockedError("Reviewer intent remained unclear after clarification")
                if clarification.decision == IntentDecision.FAIL and not clarification.must_fix:
                    clarification.must_fix = analysis.must_fix
                analysis = clarification

            _write_json(round_dir / "review_result.json", analysis.to_dict())

            if analysis.decision == IntentDecision.FAIL:
                must_fix_items = analysis.must_fix or [analysis.summary]
                current_fingerprints = build_issue_fingerprints(must_fix_items)
                previous_fingerprints = self.state.last_must_fix_fingerprints

                if previous_fingerprints:
                    (
                        stagnant,
                        similarity,
                        resolved_count,
                        added_count,
                    ) = detect_stagnation(previous_fingerprints, current_fingerprints)
                    if stagnant:
                        self.state.stagnation_count += 1
                        self.state.stagnation_reason = (
                            "must-fix set unchanged "
                            f"(similarity={similarity:.2f}, resolved={resolved_count}, "
                            f"added={added_count})"
                        )
                    else:
                        self.state.stagnation_count = 0
                        self.state.stagnation_reason = None
                else:
                    self.state.stagnation_count = 0
                    self.state.stagnation_reason = None

                self.state.last_must_fix_fingerprints = current_fingerprints
                self.state.previous_must_fix = must_fix_items

                if self.state.stagnation_count >= STAGNATION_BLOCK_THRESHOLD:
                    self.state.phase_status = PhaseStatus.BLOCKED
                    self.state.touch("review_stagnation_blocked")
                    self._save_state()
                    raise PhaseBlockedError(
                        "Review loop stagnated: "
                        + (self.state.stagnation_reason or "must-fix set unchanged")
                    )

                self.state.touch("review_failed")
                self._save_state()
                round_idx += 1
                continue

            commit_result = self._commit_phase(phase_dir)
            if not commit_result.success:
                self.state.phase_status = PhaseStatus.BLOCKED
                self.state.touch("commit_failed_blocked")
                self._save_state()
                raise PhaseBlockedError(commit_result.reason or "Commit step failed")

            self.state.last_phase_commit = commit_result.to_dict()
            self.state.phase_status = PhaseStatus.PASSED
            self.state.previous_must_fix = []
            self.state.last_must_fix_fingerprints = []
            self.state.stagnation_count = 0
            self.state.stagnation_reason = None
            self.state.touch("phase_passed")
            self._save_state()
            return self.run_dir

    def _preflight_checks(self) -> None:
        if not self.config.design_doc_path.exists():
            raise FileNotFoundError(f"Design document not found: {self.config.design_doc_path}")
        if not self.config.plan_doc_path.exists():
            raise FileNotFoundError(f"Plan document not found: {self.config.plan_doc_path}")

        for terminal_id in (
            self.state.developer_terminal_id,
            self.state.reviewer_terminal_id,
        ):
            terminal = self.client.get_terminal(terminal_id)
            status = str(terminal.get("status", ""))
            if status == TerminalStatus.ERROR.value:
                raise PhaseBlockedError(
                    f"Terminal {terminal_id} is in ERROR status; manual intervention needed"
                )

    def _phase_dir(self) -> Path:
        return self.run_dir / "phases" / _safe_phase_slug(self.state.current_phase)

    def _send_and_capture(self, terminal_id: str, message: str, transition: str) -> str:
        self.state.touch(transition)
        self._save_state()

        self.client.send_input(terminal_id, message)
        try:
            result = self.client.wait_for_completion(
                terminal_id=terminal_id,
                timeout_sec=self.config.response_timeout_sec,
                poll_interval_sec=self.config.poll_interval_sec,
            )
        except CaoWaitTimeoutError as exc:
            self.state.phase_status = PhaseStatus.BLOCKED
            self.state.touch("timeout_blocked")
            self._save_state()
            raise PhaseBlockedError(str(exc)) from exc

        if result.status == TerminalStatus.WAITING_USER_ANSWER:
            self.state.phase_status = PhaseStatus.BLOCKED
            self.state.touch("waiting_user_answer_blocked")
            self._save_state()
            raise PhaseBlockedError(
                f"Terminal {terminal_id} is waiting for user answer (approval/selection)"
            )

        if result.status == TerminalStatus.ERROR:
            self.state.phase_status = PhaseStatus.BLOCKED
            self.state.touch("terminal_error_blocked")
            self._save_state()
            raise PhaseBlockedError(f"Terminal {terminal_id} returned ERROR status")

        return self.client.get_output_last(terminal_id)

    def _commit_phase(self, phase_dir: Path) -> CommitResult:
        commit_dir = phase_dir / "commit"
        commit_message = FIXED_COMMIT_MESSAGE_TEMPLATE.format(phase=self.state.current_phase)

        attempt = 0
        while attempt <= self.config.commit_max_retries:
            attempt += 1
            self.state.touch(f"commit_attempt_{attempt}")
            self._save_state()

            prompt = build_commit_prompt(self.state.current_phase, commit_message)
            _write_text(commit_dir / f"commit_prompt_attempt_{attempt}.md", prompt)

            output = self._send_and_capture(
                terminal_id=self.state.developer_terminal_id,
                message=prompt,
                transition=f"wait_commit_attempt_{attempt}",
            )
            _write_text(commit_dir / f"commit_output_attempt_{attempt}.md", output)

            parsed = parse_commit_result(output)
            _write_json(commit_dir / f"commit_result_attempt_{attempt}.json", parsed.to_dict())

            if parsed.success:
                return parsed

        return CommitResult(success=False, reason="Commit failed after max retries")

    def _analyze_unclear_with_provider(
        self, round_dir: Path, reviewer_output: str
    ) -> IntentAnalysis:
        provider = self.config.intent_provider.lower().strip()
        terminal_id: Optional[str]

        if provider == "none":
            return IntentAnalysis(
                decision=IntentDecision.UNCLEAR,
                confidence=0.1,
                summary="Intent provider disabled",
                source="intent_provider:none",
            )

        if provider == "developer":
            terminal_id = self.state.developer_terminal_id
        elif provider == "reviewer":
            terminal_id = self.state.reviewer_terminal_id
        elif provider == "analyzer":
            terminal_id = self.config.intent_terminal_id
            if not terminal_id:
                return IntentAnalysis(
                    decision=IntentDecision.UNCLEAR,
                    confidence=0.1,
                    summary="intent_provider=analyzer but intent_terminal_id is missing",
                    source="intent_provider:analyzer",
                )
        else:
            return IntentAnalysis(
                decision=IntentDecision.UNCLEAR,
                confidence=0.1,
                summary=f"Unknown intent_provider '{self.config.intent_provider}'",
                source="intent_provider:unknown",
            )

        prompt = build_intent_analysis_prompt(self.state.current_phase, reviewer_output)
        _write_text(round_dir / f"intent_analysis_prompt_{provider}.md", prompt)
        output = self._send_and_capture(
            terminal_id=terminal_id,
            message=prompt,
            transition=f"wait_intent_analysis_{provider}",
        )
        _write_text(round_dir / f"intent_analysis_output_{provider}.md", output)
        parsed = self.analyzer.analyze_clarification(output)
        parsed.source = f"intent_provider:{provider}"
        return parsed

    def _save_state(self) -> None:
        _write_json(self.state_path, self.state.to_dict())
