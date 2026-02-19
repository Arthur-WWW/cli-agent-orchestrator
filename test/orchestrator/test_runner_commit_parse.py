"""Unit tests for commit result parsing."""

from cli_agent_orchestrator.orchestrator.runner import parse_commit_result


def test_parse_commit_result_structured_success() -> None:
    output = """success: true
commit_sha: abc1234
commit_message: phase(phase-1): implement approved changes
reason: none
"""

    result = parse_commit_result(output)

    assert result.success is True
    assert result.commit_sha == "abc1234"


def test_parse_commit_result_git_fallback() -> None:
    output = "[main d34db33] phase(phase-1): implement approved changes\n 3 files changed"

    result = parse_commit_result(output)

    assert result.success is True
    assert result.commit_sha == "d34db33"


def test_parse_commit_result_nothing_to_commit() -> None:
    output = "On branch main\nnothing to commit, working tree clean"

    result = parse_commit_result(output)

    assert result.success is False
    assert "nothing to commit" in (result.reason or "")
