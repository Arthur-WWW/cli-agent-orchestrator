"""Unit tests for Claude Code provider status parsing."""

from unittest.mock import patch

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider


class TestClaudeCodeProviderStatus:
    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_get_status_idle_with_arrow_prompt(self, mock_tmux) -> None:
        mock_tmux.get_history.return_value = (
            "╭─── Claude Code v2.1.6 ───╮\n" "Welcome back\n" "❯ \n" "  ? for shortcuts\n"
        )

        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE


class TestClaudeCodeProviderExtraction:
    def test_extract_last_message_stops_on_arrow_prompt(self) -> None:
        script_output = "⏺ Here is the result line 1\n" "line 2\n" "❯ \n" "  ? for shortcuts\n"
        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(script_output)
        assert message == "Here is the result line 1\nline 2"
