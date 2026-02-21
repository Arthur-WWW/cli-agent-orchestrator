"""Unit tests for Claude Code provider."""

from unittest.mock import patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider


class TestClaudeCodeProviderInitialization:
    @patch.object(ClaudeCodeProvider, "get_status")
    @patch("cli_agent_orchestrator.providers.claude_code.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_initialize_success_idle(self, mock_tmux, mock_wait_shell, mock_get_status):
        mock_wait_shell.return_value = True
        mock_get_status.return_value = TerminalStatus.IDLE

        provider = ClaudeCodeProvider("test1234", "test-session", "window-0", "reviewer")
        result = provider.initialize()

        assert result is True
        mock_wait_shell.assert_called_once()
        mock_tmux.send_keys.assert_called_once()

    @patch.object(ClaudeCodeProvider, "get_status")
    @patch("cli_agent_orchestrator.providers.claude_code.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_initialize_success_waiting_user_answer(
        self, mock_tmux, mock_wait_shell, mock_get_status
    ):
        mock_wait_shell.return_value = True
        mock_get_status.return_value = TerminalStatus.WAITING_USER_ANSWER

        provider = ClaudeCodeProvider("test1234", "test-session", "window-0", "reviewer")
        result = provider.initialize()

        assert result is True
        mock_wait_shell.assert_called_once()
        mock_tmux.send_keys.assert_called_once()

    @patch("cli_agent_orchestrator.providers.claude_code.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_initialize_shell_timeout(self, _mock_tmux, mock_wait_shell):
        mock_wait_shell.return_value = False
        provider = ClaudeCodeProvider("test1234", "test-session", "window-0", "reviewer")

        with pytest.raises(TimeoutError, match="Shell initialization timed out"):
            provider.initialize()

    @patch("cli_agent_orchestrator.providers.claude_code.time.time", side_effect=[0.0, 61.0])
    @patch("cli_agent_orchestrator.providers.claude_code.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_initialize_claude_timeout(self, mock_tmux, mock_wait_shell, _mock_time):
        mock_wait_shell.return_value = True
        provider = ClaudeCodeProvider("test1234", "test-session", "window-0", "reviewer")

        with pytest.raises(TimeoutError, match="Claude Code initialization timed out"):
            provider.initialize()


class TestClaudeCodeProviderStatus:
    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_get_status_idle_with_arrow_prompt(self, mock_tmux) -> None:
        mock_tmux.get_history.return_value = (
            "╭─── Claude Code v2.1.6 ───╮\n" "Welcome back\n" "❯ \n" "  ? for shortcuts\n"
        )

        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_get_status_idle_with_ansi_after_prompt(self, mock_tmux) -> None:
        mock_tmux.get_history.return_value = (
            "\x1b[2m──\n"
            "\x1b[0m❯\xa0\x1b[7m \x1b[0m\n"
            "\x1b[2m──\n"
            "\x1b[0m  ? for shortcuts\n"
        )

        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_get_status_processing_for_unknown_startup_output(self, mock_tmux) -> None:
        mock_tmux.get_history.return_value = (
            "tao@Mac-mini acp-orchestrator % claude --append-system-prompt '...'\n"
        )
        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        status = provider.get_status()
        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_get_status_error_command_not_found(self, mock_tmux) -> None:
        mock_tmux.get_history.return_value = "zsh: command not found: claude\n"
        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        status = provider.get_status()
        assert status == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.claude_code.tmux_client")
    def test_get_status_waiting_user_answer_for_trust_prompt(self, mock_tmux) -> None:
        mock_tmux.get_history.return_value = (
            "Do you trust the contents of this directory?\n"
            "❯ 1. Yes\n"
            "  2. No\n"
            "Press enter to continue\n"
        )
        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        status = provider.get_status()
        assert status == TerminalStatus.WAITING_USER_ANSWER


class TestClaudeCodeProviderExtraction:
    def test_extract_last_message_stops_on_arrow_prompt(self) -> None:
        script_output = "⏺ Here is the result line 1\n" "line 2\n" "❯ \n" "  ? for shortcuts\n"
        provider = ClaudeCodeProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(script_output)
        assert message == "Here is the result line 1\nline 2"
