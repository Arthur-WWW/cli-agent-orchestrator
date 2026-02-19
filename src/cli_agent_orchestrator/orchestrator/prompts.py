"""Prompt builders for orchestration workflow."""

from __future__ import annotations

from typing import List


def build_developer_prompt(
    phase: str,
    design_doc: str,
    plan_doc: str,
    previous_must_fix: List[str],
) -> str:
    must_fix_section = "\n".join(f"- {item}" for item in previous_must_fix)
    if not must_fix_section:
        must_fix_section = "- None"

    return (
        "You are the developer agent for phase-driven implementation.\n"
        f"Current phase: {phase}\n"
        "Hard constraints:\n"
        "1) Implement ONLY this phase.\n"
        "2) Do not move to later phases.\n"
        "3) If previous review must-fix items exist, address all of them first.\n\n"
        "Previous must-fix items:\n"
        f"{must_fix_section}\n\n"
        "Design document:\n"
        f"{design_doc}\n\n"
        "Plan document:\n"
        f"{plan_doc}\n\n"
        "Return concise completion report with:\n"
        "- Summary of changes\n"
        "- Files touched\n"
        "- Tests/validation performed\n"
    )


def build_reviewer_prompt(phase: str, developer_output: str) -> str:
    return (
        "You are the reviewer agent for phase-driven delivery.\n"
        f"Current phase under review: {phase}\n"
        "Review the developer output and repository state for this phase only.\n"
        "Please provide:\n"
        "1) A clear conclusion at the top: PASS or FAIL\n"
        "2) Blocking must-fix items (if any)\n"
        "3) Optional nice-to-have items\n"
        "A FAIL means the phase cannot proceed.\n\n"
        "Developer output:\n"
        f"{developer_output}\n"
    )


def build_reviewer_clarification_prompt() -> str:
    return (
        "Your previous review was ambiguous for automation.\n"
        "Please reply with ONLY one of: PASS or FAIL.\n"
        "If FAIL, include a short must-fix bullet list below it.\n"
    )


def build_intent_analysis_prompt(phase: str, reviewer_output: str) -> str:
    return (
        "You are the intent analyzer for review decisions.\n"
        f"Current phase: {phase}\n"
        "Given the following reviewer output, decide if the phase should PASS or FAIL.\n"
        "Reply with ONLY one of: PASS or FAIL.\n"
        "If FAIL, add a short must-fix bullet list after the first line.\n\n"
        "Reviewer output:\n"
        f"{reviewer_output}\n"
    )


def build_commit_prompt(phase: str, commit_message: str) -> str:
    return (
        "Review is passed for current phase.\n"
        f"Current phase: {phase}\n"
        "Now perform commit for all changes of this phase.\n"
        "Run:\n"
        "1) git status\n"
        f'2) git add -A && git commit -m "{commit_message}"\n\n'
        "Then reply with this exact shape (plain text is fine):\n"
        "success: true|false\n"
        "commit_sha: <sha-or-none>\n"
        "commit_message: <message-or-none>\n"
        "reason: <if failed>\n"
    )
