"""Unit tests for review intent analyzer."""

from cli_agent_orchestrator.orchestrator.intent_analyzer import IntentAnalyzer, IntentAnalyzerConfig
from cli_agent_orchestrator.orchestrator.models import IntentDecision


def make_analyzer() -> IntentAnalyzer:
    return IntentAnalyzer(IntentAnalyzerConfig(provider="none"))


def test_intent_explicit_pass() -> None:
    analyzer = make_analyzer()
    output = "结论：PASS\n当前 phase 可进入下一阶段，无阻断问题。"

    result = analyzer.analyze(output, "phase-1")

    assert result.decision == IntentDecision.PASS
    assert result.confidence >= 0.9


def test_intent_explicit_fail_with_must_fix() -> None:
    analyzer = make_analyzer()
    output = """结论：FAIL
- must-fix: Handle None input in parser
- must-fix: Add regression tests for phase boundary
"""

    result = analyzer.analyze(output, "phase-1")

    assert result.decision == IntentDecision.FAIL
    assert len(result.must_fix) == 2


def test_intent_unclear_when_no_signal() -> None:
    analyzer = make_analyzer()
    output = "整体质量还可以，建议再看一下边界情况。"

    result = analyzer.analyze(output, "phase-1")

    assert result.decision == IntentDecision.UNCLEAR


def test_clarification_parse_pass() -> None:
    analyzer = make_analyzer()

    result = analyzer.analyze_clarification("PASS")

    assert result.decision == IntentDecision.PASS


def test_clarification_parse_fail_with_items() -> None:
    analyzer = make_analyzer()
    output = "FAIL\n- must-fix: add null check"

    result = analyzer.analyze_clarification(output)

    assert result.decision == IntentDecision.FAIL
    assert result.must_fix == ["- must-fix: add null check"]
