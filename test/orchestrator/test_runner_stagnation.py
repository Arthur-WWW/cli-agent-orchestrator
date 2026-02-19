"""Unit tests for review stagnation detection."""

from cli_agent_orchestrator.orchestrator.runner import build_issue_fingerprints, detect_stagnation


def test_build_issue_fingerprints_deduplicates_line_noise() -> None:
    items = [
        "must-fix: null check missing in src/app.py:42",
        "must-fix: null check missing in src/app.py:108",
    ]

    fingerprints = build_issue_fingerprints(items)

    assert len(fingerprints) == 1


def test_detect_stagnation_true_for_high_overlap_without_resolution() -> None:
    previous = ["a", "b", "c", "d"]
    current = ["a", "b", "c", "d", "e"]

    stagnant, similarity, resolved_count, added_count = detect_stagnation(previous, current)

    assert stagnant is True
    assert similarity == 0.8
    assert resolved_count == 0
    assert added_count == 1


def test_detect_stagnation_false_when_issues_are_resolved() -> None:
    previous = ["a", "b", "c", "d"]
    current = ["a", "b", "c", "e"]

    stagnant, similarity, resolved_count, added_count = detect_stagnation(previous, current)

    assert stagnant is False
    assert similarity < 0.8
    assert resolved_count == 1
    assert added_count == 1
