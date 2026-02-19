"""Data models for CAO HTTP orchestrator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class PhaseStatus(str, Enum):
    """Execution status for a phase run."""

    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class IntentDecision(str, Enum):
    """Decision parsed from reviewer output."""

    PASS = "pass"
    FAIL = "fail"
    UNCLEAR = "unclear"


@dataclass
class IntentAnalysis:
    """Normalized review analysis result."""

    decision: IntentDecision
    confidence: float
    summary: str
    must_fix: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    source: str = "rule"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "confidence": self.confidence,
            "summary": self.summary,
            "must_fix": self.must_fix,
            "evidence": self.evidence,
            "source": self.source,
        }


@dataclass
class CommitResult:
    """Commit step result."""

    success: bool
    commit_sha: Optional[str] = None
    commit_message: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "commit_sha": self.commit_sha,
            "commit_message": self.commit_message,
            "reason": self.reason,
        }


@dataclass
class OrchestratorConfig:
    """Runtime configuration for orchestrator."""

    design_doc_path: Path
    plan_doc_path: Path
    phase_selector: str
    developer_terminal_id: str
    reviewer_terminal_id: str
    run_root: Path
    cao_base_url: str
    max_review_rounds_per_phase: int = 8
    poll_interval_sec: float = 2.0
    response_timeout_sec: int = 1800
    commit_max_retries: int = 1
    intent_provider: str = "reviewer"
    intent_terminal_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["design_doc_path"] = str(self.design_doc_path)
        data["plan_doc_path"] = str(self.plan_doc_path)
        data["run_root"] = str(self.run_root)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrchestratorConfig":
        return cls(
            design_doc_path=Path(data["design_doc_path"]),
            plan_doc_path=Path(data["plan_doc_path"]),
            phase_selector=str(data["phase_selector"]),
            developer_terminal_id=str(data["developer_terminal_id"]),
            reviewer_terminal_id=str(data["reviewer_terminal_id"]),
            run_root=Path(data["run_root"]),
            cao_base_url=str(data["cao_base_url"]),
            max_review_rounds_per_phase=int(data.get("max_review_rounds_per_phase", 8)),
            poll_interval_sec=float(data.get("poll_interval_sec", 2.0)),
            response_timeout_sec=int(data.get("response_timeout_sec", 1800)),
            commit_max_retries=int(data.get("commit_max_retries", 1)),
            intent_provider=str(data.get("intent_provider", "reviewer")),
            intent_terminal_id=(
                str(data["intent_terminal_id"])
                if isinstance(data.get("intent_terminal_id"), str)
                else None
            ),
        )


@dataclass
class RunState:
    """Persistent orchestration run state."""

    run_id: str
    design_doc: str
    plan_doc: str
    developer_terminal_id: str
    reviewer_terminal_id: str
    current_phase: str
    phase_status: PhaseStatus
    round: int
    last_transition: str
    last_phase_commit: Optional[Dict[str, Any]]
    previous_must_fix: List[str] = field(default_factory=list)
    config: Optional[Dict[str, Any]] = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "design_doc": self.design_doc,
            "plan_doc": self.plan_doc,
            "developer_terminal_id": self.developer_terminal_id,
            "reviewer_terminal_id": self.reviewer_terminal_id,
            "current_phase": self.current_phase,
            "phase_status": self.phase_status.value,
            "round": self.round,
            "last_transition": self.last_transition,
            "last_phase_commit": self.last_phase_commit,
            "previous_must_fix": self.previous_must_fix,
            "config": self.config,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunState":
        return cls(
            run_id=str(data["run_id"]),
            design_doc=str(data["design_doc"]),
            plan_doc=str(data["plan_doc"]),
            developer_terminal_id=str(data["developer_terminal_id"]),
            reviewer_terminal_id=str(data["reviewer_terminal_id"]),
            current_phase=str(data["current_phase"]),
            phase_status=PhaseStatus(str(data["phase_status"])),
            round=int(data["round"]),
            last_transition=str(data["last_transition"]),
            last_phase_commit=data.get("last_phase_commit"),
            previous_must_fix=list(data.get("previous_must_fix", [])),
            config=data.get("config"),
            updated_at=str(data.get("updated_at", datetime.now(timezone.utc).isoformat())),
        )

    def touch(self, transition: str) -> None:
        self.last_transition = transition
        self.updated_at = datetime.now(timezone.utc).isoformat()
