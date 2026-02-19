"""HTTP client wrapper for CAO server."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict

import requests  # type: ignore[import-untyped]

from cli_agent_orchestrator.models.terminal import TerminalStatus


class CaoApiError(RuntimeError):
    """Raised when CAO API returns an error."""


class CaoWaitTimeoutError(TimeoutError):
    """Raised when waiting terminal status timed out."""


@dataclass
class WaitResult:
    """Wait result for terminal completion."""

    status: TerminalStatus


class CaoHttpClient:
    """Thin client for CAO HTTP APIs used by orchestrator."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_terminal(self, terminal_id: str) -> Dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/terminals/{terminal_id}",
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise CaoApiError(
                f"Failed to get terminal {terminal_id}: {response.status_code} {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise CaoApiError(f"Invalid terminal response for {terminal_id}: {data}")
        return data

    def create_session(
        self,
        provider: str,
        agent_profile: str,
        session_name: str | None = None,
        working_directory: str | None = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "provider": provider,
            "agent_profile": agent_profile,
        }
        if session_name:
            params["session_name"] = session_name
        if working_directory:
            params["working_directory"] = working_directory

        response = requests.post(
            f"{self.base_url}/sessions",
            params=params,
            timeout=self.timeout,
        )
        if response.status_code != 201:
            raise CaoApiError(
                "Failed to create session: " f"{response.status_code} {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise CaoApiError(f"Invalid session creation response: {data}")
        return data

    def create_terminal_in_session(
        self,
        session_name: str,
        provider: str,
        agent_profile: str,
        working_directory: str | None = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "provider": provider,
            "agent_profile": agent_profile,
        }
        if working_directory:
            params["working_directory"] = working_directory

        response = requests.post(
            f"{self.base_url}/sessions/{session_name}/terminals",
            params=params,
            timeout=self.timeout,
        )
        if response.status_code != 201:
            raise CaoApiError(
                "Failed to create terminal in session "
                f"{session_name}: {response.status_code} {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise CaoApiError(f"Invalid terminal creation response: {data}")
        return data

    def get_terminal_status(self, terminal_id: str) -> TerminalStatus:
        terminal = self.get_terminal(terminal_id)
        status = terminal.get("status")
        if not isinstance(status, str):
            raise CaoApiError(f"Terminal {terminal_id} status missing or invalid")
        return TerminalStatus(status)

    def send_input(self, terminal_id: str, message: str) -> None:
        response = requests.post(
            f"{self.base_url}/terminals/{terminal_id}/input",
            params={"message": message},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise CaoApiError(
                f"Failed to send input to {terminal_id}: {response.status_code} {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict) or not data.get("success", False):
            raise CaoApiError(f"Unexpected input response for {terminal_id}: {data}")

    def get_output_last(self, terminal_id: str) -> str:
        response = requests.get(
            f"{self.base_url}/terminals/{terminal_id}/output",
            params={"mode": "last"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise CaoApiError(
                f"Failed to get output from {terminal_id}: {response.status_code} {response.text}"
            )
        data = response.json()
        output = data.get("output")
        if not isinstance(output, str):
            raise CaoApiError(f"Invalid output payload from {terminal_id}: {data}")
        return output

    def wait_for_completion(
        self,
        terminal_id: str,
        timeout_sec: int,
        poll_interval_sec: float,
    ) -> WaitResult:
        """Wait until terminal reaches completed/error/waiting state.

        Returns completed also when terminal returns to IDLE after processing.
        """
        start = time.time()
        saw_processing = False

        while time.time() - start < timeout_sec:
            status = self.get_terminal_status(terminal_id)

            if status == TerminalStatus.PROCESSING:
                saw_processing = True
            elif status in (TerminalStatus.WAITING_USER_ANSWER, TerminalStatus.ERROR):
                return WaitResult(status=status)
            elif status == TerminalStatus.COMPLETED:
                return WaitResult(status=status)
            elif status == TerminalStatus.IDLE and saw_processing:
                return WaitResult(status=TerminalStatus.COMPLETED)

            time.sleep(poll_interval_sec)

        raise CaoWaitTimeoutError(
            f"Timed out waiting for terminal {terminal_id} after {timeout_sec} seconds"
        )
