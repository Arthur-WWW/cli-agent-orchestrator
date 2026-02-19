"""Orchestrator commands for phase-driven CAO HTTP workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import click

from cli_agent_orchestrator.constants import API_BASE_URL
from cli_agent_orchestrator.orchestrator.cao_client import CaoHttpClient
from cli_agent_orchestrator.orchestrator.models import OrchestratorConfig
from cli_agent_orchestrator.orchestrator.runner import OrchestratorRunner, PhaseBlockedError


@click.group()
def orchestrate() -> None:
    """Run custom CAO HTTP orchestrator workflows."""


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _get_terminal_session_name(client: CaoHttpClient, terminal_id: str) -> str:
    terminal = client.get_terminal(terminal_id)
    session_name = terminal.get("session_name")
    if not isinstance(session_name, str) or not session_name:
        raise click.ClickException(f"Failed to resolve session_name from terminal {terminal_id}")
    return session_name


@orchestrate.command("run")
@click.option("--design", "design_doc", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--plan", "plan_doc", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--phase-name", "--phase", "phase_selector", required=True, type=str)
@click.option("--developer-terminal", default=None, type=str)
@click.option("--reviewer-terminal", default=None, type=str)
@click.option("--session-name", default=None, type=str)
@click.option("--launch-developer-agent", is_flag=True, default=False)
@click.option("--developer-provider", default="claude_code", show_default=True, type=str)
@click.option("--developer-profile", default="developer", show_default=True, type=str)
@click.option("--launch-reviewer-agent", is_flag=True, default=False)
@click.option("--reviewer-provider", default="codex", show_default=True, type=str)
@click.option("--reviewer-profile", default="reviewer", show_default=True, type=str)
@click.option(
    "--run-root",
    default=".orchestrator/runs",
    show_default=True,
    type=click.Path(path_type=Path),
)
@click.option("--cao-base-url", default=API_BASE_URL, show_default=True, type=str)
@click.option("--max-review-rounds", default=8, show_default=True, type=int)
@click.option("--poll-interval", default=2.0, show_default=True, type=float)
@click.option("--response-timeout", default=1800, show_default=True, type=int)
@click.option(
    "--commit-message-template",
    default="phase({phase}): implement approved changes",
    show_default=True,
    type=str,
)
@click.option("--commit-max-retries", default=1, show_default=True, type=int)
@click.option(
    "--intent-provider",
    default="reviewer",
    show_default=True,
    type=click.Choice(["none", "developer", "reviewer", "analyzer"]),
)
@click.option("--intent-terminal", default=None, type=str)
@click.option("--launch-intent-agent", is_flag=True, default=False)
@click.option(
    "--intent-agent-provider",
    default="claude_code",
    show_default=True,
    type=str,
)
@click.option(
    "--intent-agent-profile",
    default="reviewer",
    show_default=True,
    type=str,
)
@click.option("--debug", is_flag=True, default=False)
def run_orchestrator(
    design_doc: Path,
    plan_doc: Path,
    phase_selector: str,
    developer_terminal: Optional[str],
    reviewer_terminal: Optional[str],
    session_name: Optional[str],
    launch_developer_agent: bool,
    developer_provider: str,
    developer_profile: str,
    launch_reviewer_agent: bool,
    reviewer_provider: str,
    reviewer_profile: str,
    run_root: Path,
    cao_base_url: str,
    max_review_rounds: int,
    poll_interval: float,
    response_timeout: int,
    commit_message_template: str,
    commit_max_retries: int,
    intent_provider: str,
    intent_terminal: Optional[str],
    launch_intent_agent: bool,
    intent_agent_provider: str,
    intent_agent_profile: str,
    debug: bool,
) -> None:
    """Start a new orchestrator run."""
    _setup_logging(debug)

    client = CaoHttpClient(cao_base_url)
    resolved_developer_terminal = developer_terminal
    resolved_reviewer_terminal = reviewer_terminal
    resolved_intent_terminal = intent_terminal
    resolved_session_name = session_name

    if launch_developer_agent and developer_terminal:
        raise click.ClickException(
            "--developer-terminal and --launch-developer-agent cannot be used together"
        )
    if launch_reviewer_agent and reviewer_terminal:
        raise click.ClickException(
            "--reviewer-terminal and --launch-reviewer-agent cannot be used together"
        )

    if not resolved_developer_terminal and not launch_developer_agent:
        raise click.ClickException(
            "Developer terminal is required: provide --developer-terminal or --launch-developer-agent"
        )
    if not resolved_reviewer_terminal and not launch_reviewer_agent:
        raise click.ClickException(
            "Reviewer terminal is required: provide --reviewer-terminal or --launch-reviewer-agent"
        )

    if launch_intent_agent and intent_provider != "analyzer":
        raise click.ClickException("--launch-intent-agent requires --intent-provider analyzer")

    if launch_intent_agent and intent_terminal:
        raise click.ClickException(
            "--intent-terminal and --launch-intent-agent cannot be used together"
        )

    if not resolved_session_name:
        for known_terminal_id in (
            resolved_developer_terminal,
            resolved_reviewer_terminal,
            resolved_intent_terminal,
        ):
            if known_terminal_id:
                resolved_session_name = _get_terminal_session_name(client, known_terminal_id)
                break

    if launch_developer_agent:
        if resolved_session_name:
            created = client.create_terminal_in_session(
                session_name=resolved_session_name,
                provider=developer_provider,
                agent_profile=developer_profile,
            )
        else:
            created = client.create_session(
                provider=developer_provider,
                agent_profile=developer_profile,
            )
            created_session_name = created.get("session_name")
            if isinstance(created_session_name, str) and created_session_name:
                resolved_session_name = created_session_name
        created_id = created.get("id")
        if not isinstance(created_id, str):
            raise click.ClickException(
                "Developer agent launch succeeded but terminal id is missing"
            )
        resolved_developer_terminal = created_id
        if not resolved_session_name:
            resolved_session_name = _get_terminal_session_name(client, created_id)
        click.echo(
            f"Launched developer terminal: {created_id} ({developer_provider}/{developer_profile})"
        )

    if launch_reviewer_agent:
        if not resolved_session_name:
            raise click.ClickException(
                "Cannot launch reviewer agent without session. "
                "Provide --session-name or an existing terminal id."
            )
        created = client.create_terminal_in_session(
            session_name=resolved_session_name,
            provider=reviewer_provider,
            agent_profile=reviewer_profile,
        )
        created_id = created.get("id")
        if not isinstance(created_id, str):
            raise click.ClickException("Reviewer agent launch succeeded but terminal id is missing")
        resolved_reviewer_terminal = created_id
        click.echo(
            f"Launched reviewer terminal: {created_id} ({reviewer_provider}/{reviewer_profile})"
        )

    if launch_intent_agent:
        if not resolved_session_name:
            for known_terminal_id in (
                resolved_developer_terminal,
                resolved_reviewer_terminal,
            ):
                if known_terminal_id:
                    resolved_session_name = _get_terminal_session_name(client, known_terminal_id)
                    break
        if not resolved_session_name:
            raise click.ClickException(
                "Cannot launch intent agent without session. "
                "Provide --session-name or an existing terminal id."
            )
        created = client.create_terminal_in_session(
            session_name=resolved_session_name,
            provider=intent_agent_provider,
            agent_profile=intent_agent_profile,
        )
        created_id = created.get("id")
        if not isinstance(created_id, str):
            raise click.ClickException("Intent agent launch succeeded but terminal id is missing")
        resolved_intent_terminal = created_id
        click.echo(
            "Launched intent analyzer terminal: "
            f"{created_id} ({intent_agent_provider}/{intent_agent_profile})"
        )

    if intent_provider == "analyzer" and not resolved_intent_terminal:
        raise click.ClickException(
            "intent_provider=analyzer requires --intent-terminal " "or --launch-intent-agent"
        )

    if not resolved_developer_terminal:
        raise click.ClickException("Developer terminal id is unavailable after initialization")
    if not resolved_reviewer_terminal:
        raise click.ClickException("Reviewer terminal id is unavailable after initialization")

    config = OrchestratorConfig(
        design_doc_path=design_doc,
        plan_doc_path=plan_doc,
        phase_selector=phase_selector,
        developer_terminal_id=resolved_developer_terminal,
        reviewer_terminal_id=resolved_reviewer_terminal,
        run_root=run_root,
        cao_base_url=cao_base_url,
        max_review_rounds_per_phase=max_review_rounds,
        poll_interval_sec=poll_interval,
        response_timeout_sec=response_timeout,
        commit_message_template=commit_message_template,
        commit_max_retries=commit_max_retries,
        intent_provider=intent_provider,
        intent_terminal_id=resolved_intent_terminal,
    )

    runner = OrchestratorRunner.create_new(config)
    click.echo(f"Run created: {runner.run_dir}")

    try:
        run_dir = runner.run()
        click.echo(f"Run finished. State file: {run_dir / 'run_state.json'}")
        click.echo(f"Final phase status: {runner.state.phase_status.value}")
        if runner.state.last_phase_commit:
            commit_sha = runner.state.last_phase_commit.get("commit_sha")
            if isinstance(commit_sha, str):
                click.echo(f"Phase commit: {commit_sha}")
    except PhaseBlockedError as exc:
        click.echo(f"Run blocked: {exc}")
        click.echo(f"Resume with: cao orchestrate resume --run-state {runner.state_path}")


@orchestrate.command("resume")
@click.option("--run-state", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--cao-base-url", default=None, type=str)
@click.option(
    "--intent-provider",
    default=None,
    type=click.Choice(["none", "developer", "reviewer", "analyzer"]),
)
@click.option("--intent-terminal", default=None, type=str)
@click.option("--debug", is_flag=True, default=False)
def resume_orchestrator(
    run_state: Path,
    cao_base_url: Optional[str],
    intent_provider: Optional[str],
    intent_terminal: Optional[str],
    debug: bool,
) -> None:
    """Resume a blocked or in-progress orchestrator run."""
    _setup_logging(debug)

    runner = OrchestratorRunner.from_state_path(run_state)

    if cao_base_url:
        runner.config.cao_base_url = cao_base_url
    if intent_provider:
        runner.config.intent_provider = intent_provider
    if intent_terminal:
        runner.config.intent_terminal_id = intent_terminal

    runner.state.config = runner.config.to_dict()

    try:
        run_dir = runner.run()
        click.echo(f"Run finished. State file: {run_dir / 'run_state.json'}")
        click.echo(f"Final phase status: {runner.state.phase_status.value}")
        if runner.state.last_phase_commit:
            commit_sha = runner.state.last_phase_commit.get("commit_sha")
            if isinstance(commit_sha, str):
                click.echo(f"Phase commit: {commit_sha}")
    except PhaseBlockedError as exc:
        click.echo(f"Run blocked again: {exc}")
        click.echo(f"Resume with: cao orchestrate resume --run-state {runner.state_path}")
