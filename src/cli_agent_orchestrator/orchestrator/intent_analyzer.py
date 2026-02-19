"""Intent analysis for reviewer outputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from cli_agent_orchestrator.orchestrator.models import IntentAnalysis, IntentDecision

PASS_PATTERNS = [
    r"\bPASS\b",
    r"\bAPPROVED\b",
    r"结论\s*[:：]\s*通过",
    r"无阻断问题",
    r"可以进入下一\s*phase",
    r"可以进入下个\s*phase",
    r"no blocking issues",
]

FAIL_PATTERNS = [
    r"\bFAIL\b",
    r"结论\s*[:：]\s*不通过",
    r"必须修复",
    r"(?<!无)阻断问题",
    r"must\s*fix",
    r"blocking issue",
    r"cannot proceed",
]

MUST_FIX_HINTS = [
    "must-fix",
    "must fix",
    "必须修复",
    "阻断",
    "critical",
    "high",
    "p0",
    "p1",
]

NEGATIVE_BLOCKING_HINTS = [
    "无阻断",
    "没有阻断",
    "no blocking issues",
]


@dataclass
class IntentAnalyzerConfig:
    provider: str


class IntentAnalyzer:
    """Analyze reviewer output to pass/fail intent."""

    def __init__(self, config: IntentAnalyzerConfig):
        self.config = config

    def analyze(self, review_output: str, phase: str) -> IntentAnalysis:
        del phase

        review_output = review_output.strip()
        if not review_output:
            return IntentAnalysis(
                decision=IntentDecision.UNCLEAR,
                confidence=0.0,
                summary="Reviewer output is empty",
                source="rule",
            )

        explicit = self._explicit_decision(review_output)
        must_fix = self._extract_must_fix_items(review_output)

        if explicit == IntentDecision.FAIL:
            return IntentAnalysis(
                decision=IntentDecision.FAIL,
                confidence=0.92,
                summary="Explicit fail signal detected",
                must_fix=must_fix,
                evidence=self._find_evidence(review_output, FAIL_PATTERNS),
                source="rule",
            )

        if explicit == IntentDecision.PASS and not must_fix:
            return IntentAnalysis(
                decision=IntentDecision.PASS,
                confidence=0.92,
                summary="Explicit pass signal detected",
                must_fix=[],
                evidence=self._find_evidence(review_output, PASS_PATTERNS),
                source="rule",
            )

        # If must-fix style issues were extracted, default to fail.
        if must_fix:
            return IntentAnalysis(
                decision=IntentDecision.FAIL,
                confidence=0.76,
                summary="Must-fix style issues detected",
                must_fix=must_fix,
                source="rule",
            )

        heuristic = self._heuristic_decision(review_output)
        if heuristic is not None:
            return heuristic

        return IntentAnalysis(
            decision=IntentDecision.UNCLEAR,
            confidence=0.4,
            summary="Unable to infer clear pass/fail intent",
            source="rule",
        )

    def analyze_clarification(self, review_output: str) -> IntentAnalysis:
        """Strict parse for clarification responses (PASS/FAIL)."""
        normalized = review_output.strip().upper()
        if re.search(r"\bPASS\b", normalized) or "通过" in review_output:
            return IntentAnalysis(
                decision=IntentDecision.PASS,
                confidence=0.95,
                summary="Clarification explicitly PASS",
                source="clarification",
            )
        if re.search(r"\bFAIL\b", normalized) or "不通过" in review_output:
            return IntentAnalysis(
                decision=IntentDecision.FAIL,
                confidence=0.95,
                summary="Clarification explicitly FAIL",
                must_fix=self._extract_must_fix_items(review_output),
                source="clarification",
            )
        return IntentAnalysis(
            decision=IntentDecision.UNCLEAR,
            confidence=0.1,
            summary="Clarification did not provide explicit PASS/FAIL",
            source="clarification",
        )

    def _explicit_decision(self, text: str) -> IntentDecision:
        has_pass = any(re.search(pattern, text, re.IGNORECASE) for pattern in PASS_PATTERNS)
        has_fail = any(re.search(pattern, text, re.IGNORECASE) for pattern in FAIL_PATTERNS)

        if has_fail:
            return IntentDecision.FAIL
        if has_pass:
            return IntentDecision.PASS
        return IntentDecision.UNCLEAR

    def _heuristic_decision(self, text: str) -> Optional[IntentAnalysis]:
        lower = text.lower()

        if "looks good" in lower or "ship it" in lower:
            return IntentAnalysis(
                decision=IntentDecision.PASS,
                confidence=0.7,
                summary="Heuristic pass phrase detected",
                source="rule",
            )

        if "need" in lower and "fix" in lower:
            return IntentAnalysis(
                decision=IntentDecision.FAIL,
                confidence=0.7,
                summary="Heuristic fail phrase detected",
                must_fix=self._extract_must_fix_items(text),
                source="rule",
            )

        return None

    def _extract_must_fix_items(self, text: str) -> List[str]:
        items: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            is_bullet = bool(re.match(r"^([-*]|\d+\.)\s+", line))
            lower = line.lower()
            if any(hint in lower for hint in NEGATIVE_BLOCKING_HINTS):
                continue
            has_hint = any(hint in lower for hint in MUST_FIX_HINTS)
            if is_bullet and has_hint:
                items.append(line)

        if items:
            return items

        # Fallback: collect sentences that contain clear must-fix cues.
        sentence_matches = re.findall(r"[^。.!?\n]+(?:[。.!?]|$)", text)
        for sentence in sentence_matches:
            lower = sentence.lower()
            if any(hint in lower for hint in NEGATIVE_BLOCKING_HINTS):
                continue
            if any(hint in lower for hint in MUST_FIX_HINTS):
                items.append(sentence.strip())

        return items[:10]

    def _find_evidence(self, text: str, patterns: List[str]) -> List[str]:
        evidence: List[str] = []
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                evidence.append(match.group(0))
        return evidence
