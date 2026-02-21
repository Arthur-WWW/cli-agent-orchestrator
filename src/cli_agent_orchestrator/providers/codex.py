"""Codex CLI provider implementation."""

import logging
import re
import time
from typing import Optional

from cli_agent_orchestrator.clients.tmux import tmux_client
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.utils.terminal import wait_for_shell

logger = logging.getLogger(__name__)

# Regex patterns for Codex output analysis
ANSI_CODE_PATTERN = r"\x1b\[[0-9;]*m"
IDLE_PROMPT_PATTERN = r"(?:❯|›|codex>)"
# Match the prompt only if it appears at the end of the captured output.
# Allows trailing text on the same line (e.g., "What would you like to do next?")
IDLE_PROMPT_AT_END_PATTERN = rf"(?:^\s*{IDLE_PROMPT_PATTERN}\s*)\s*\Z"
# Fallback for layouts where prompt line is followed by a status/footer line.
IDLE_PROMPT_RECENT_PATTERN = rf"^\s*{IDLE_PROMPT_PATTERN}(?:\s|$)"
IDLE_PROMPT_PATTERN_LOG = r"❯"
ASSISTANT_PREFIX_PATTERN = r"^(?:assistant|codex|agent)\s*:"
USER_PREFIX_PATTERN = r"^You\b"

PROCESSING_PATTERN = r"\b(thinking|working|running|executing|processing|analyzing)\b"
WAITING_PROMPT_PATTERN = r"^(?:Approve|Allow)\b.*\b(?:y/n|yes/no|yes|no)\b"
TRUST_PROMPT_PATTERN = r"(?:do you trust the contents of this directory|press enter to continue)"
ERROR_PATTERN = r"^(?:Error:|ERROR:|Traceback \(most recent call last\):|panic:)"
PROMPT_LINE_PATTERN = r"^\s*(?:❯|›|codex>)(?:\s.*)?$"
FOOTER_LINE_PATTERN = r"(?:for shortcuts|context left)\s*$"
SEPARATOR_LINE_PATTERN = r"^\s*[─-]{8,}\s*$"
FALLBACK_MESSAGE_MAX_LINES = 120


class CodexProvider(BaseProvider):
    """Provider for Codex CLI tool integration."""

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str] = None,
    ):
        super().__init__(terminal_id, session_name, window_name)
        self._initialized = False
        self._agent_profile = agent_profile

    def initialize(self) -> bool:
        """Initialize Codex provider by starting codex command."""
        if not wait_for_shell(tmux_client, self.session_name, self.window_name, timeout=10.0):
            raise TimeoutError("Shell initialization timed out after 10 seconds")

        tmux_client.send_keys(self.session_name, self.window_name, "codex")

        # Codex may require one-time directory trust confirmation before reaching IDLE.
        # Treat WAITING_USER_ANSWER as initialized so users can interact in tmux.
        start = time.time()
        while time.time() - start < 60.0:
            status = self.get_status()
            if status in (TerminalStatus.IDLE, TerminalStatus.WAITING_USER_ANSWER):
                self._initialized = True
                return True
            time.sleep(1.0)

        raise TimeoutError("Codex initialization timed out after 60 seconds")

    def get_status(self, tail_lines: Optional[int] = None) -> TerminalStatus:
        """Get Codex status by analyzing terminal output."""
        output = tmux_client.get_history(self.session_name, self.window_name, tail_lines=tail_lines)

        if not output:
            return TerminalStatus.ERROR

        clean_output = re.sub(ANSI_CODE_PATTERN, "", output)
        tail_output = "\n".join(clean_output.splitlines()[-25:])
        has_processing_indicator = bool(
            re.search(PROCESSING_PATTERN, tail_output, re.IGNORECASE | re.MULTILINE)
        )

        last_user = None
        for match in re.finditer(USER_PREFIX_PATTERN, clean_output, re.IGNORECASE | re.MULTILINE):
            last_user = match

        output_after_last_user = clean_output[last_user.start() :] if last_user else clean_output
        assistant_after_last_user = bool(
            last_user
            and re.search(
                ASSISTANT_PREFIX_PATTERN,
                output_after_last_user,
                re.IGNORECASE | re.MULTILINE,
            )
        )

        has_idle_prompt_at_end = bool(
            re.search(IDLE_PROMPT_AT_END_PATTERN, clean_output, re.IGNORECASE | re.MULTILINE)
        )
        has_idle_prompt_recent = bool(
            re.search(IDLE_PROMPT_RECENT_PATTERN, tail_output, re.IGNORECASE | re.MULTILINE)
        )

        # Only treat ERROR/WAITING prompts as actionable if they appear after the last user message
        # and are not part of an assistant response.
        if last_user is not None:
            if not assistant_after_last_user:
                if re.search(
                    WAITING_PROMPT_PATTERN,
                    output_after_last_user,
                    re.IGNORECASE | re.MULTILINE,
                ):
                    return TerminalStatus.WAITING_USER_ANSWER
                if re.search(
                    ERROR_PATTERN,
                    output_after_last_user,
                    re.IGNORECASE | re.MULTILINE,
                ):
                    return TerminalStatus.ERROR
        else:
            if re.search(WAITING_PROMPT_PATTERN, tail_output, re.IGNORECASE | re.MULTILINE):
                return TerminalStatus.WAITING_USER_ANSWER
            if re.search(TRUST_PROMPT_PATTERN, tail_output, re.IGNORECASE | re.MULTILINE):
                return TerminalStatus.WAITING_USER_ANSWER
            if re.search(ERROR_PATTERN, tail_output, re.IGNORECASE | re.MULTILINE):
                return TerminalStatus.ERROR
        if has_processing_indicator:
            return TerminalStatus.PROCESSING

        if has_idle_prompt_at_end or has_idle_prompt_recent:
            # Consider COMPLETED only if we see an assistant marker after the last user message.
            if last_user is not None:
                if re.search(
                    ASSISTANT_PREFIX_PATTERN,
                    clean_output[last_user.start() :],
                    re.IGNORECASE | re.MULTILINE,
                ):
                    return TerminalStatus.COMPLETED

                return TerminalStatus.IDLE

            return TerminalStatus.IDLE

        # If we're not at an idle prompt and we don't see explicit errors/permission prompts,
        # assume the CLI is still producing output.
        return TerminalStatus.PROCESSING

    def get_idle_pattern_for_log(self) -> str:
        """Return Codex IDLE prompt pattern for log files."""
        return IDLE_PROMPT_PATTERN_LOG

    def extract_last_message_from_script(self, script_output: str) -> str:
        """Extract Codex's final response message using assistant label markers."""
        clean_output = re.sub(ANSI_CODE_PATTERN, "", script_output)

        matches = list(
            re.finditer(ASSISTANT_PREFIX_PATTERN, clean_output, re.IGNORECASE | re.MULTILINE)
        )

        if not matches:
            return self._extract_last_message_fallback(clean_output)

        last_match = matches[-1]
        start_pos = last_match.end()

        idle_after = re.search(
            IDLE_PROMPT_AT_END_PATTERN,
            clean_output[start_pos:],
            re.IGNORECASE | re.MULTILINE,
        )
        end_pos = start_pos + idle_after.start() if idle_after else len(clean_output)

        final_answer = clean_output[start_pos:end_pos].strip()

        if not final_answer:
            raise ValueError("Empty Codex response - no content found")

        return final_answer

    def _extract_last_message_fallback(self, clean_output: str) -> str:
        """Best-effort extraction for Codex UIs that omit explicit assistant labels."""
        if not re.search(PROMPT_LINE_PATTERN, clean_output, re.IGNORECASE | re.MULTILINE):
            raise ValueError("No Codex response found - no assistant marker detected")

        lines = clean_output.splitlines()
        if not lines:
            raise ValueError("No Codex response found - empty output")

        # Remove trailing prompt/footer/noise lines.
        while lines:
            last = lines[-1]
            if not last.strip():
                lines.pop()
                continue
            if re.search(FOOTER_LINE_PATTERN, last, re.IGNORECASE):
                lines.pop()
                continue
            if re.match(PROMPT_LINE_PATTERN, last, re.IGNORECASE):
                lines.pop()
                continue
            if re.match(SEPARATOR_LINE_PATTERN, last, re.IGNORECASE):
                lines.pop()
                continue
            break

        if not lines:
            raise ValueError("No Codex response found - no content before prompt")

        start = max(0, len(lines) - FALLBACK_MESSAGE_MAX_LINES)

        # If bullet-style output exists in the tail, anchor there to avoid returning old history.
        for i in range(start, len(lines)):
            if re.match(r"^\s*•\s+\S", lines[i]):
                start = i
                break

        final_answer = "\n".join(lines[start:]).strip()
        if not final_answer:
            raise ValueError("Empty Codex response - fallback extraction produced no content")

        return final_answer

    def exit_cli(self) -> str:
        """Get the command to exit Codex CLI."""
        return "/exit"

    def cleanup(self) -> None:
        """Clean up Codex CLI provider."""
        self._initialized = False
