"""Pure view-model construction for the overlay."""

from __future__ import annotations

import time

from tasklight.model import AgentRecord, AgentState
from tasklight.overlay.presentation import STATE_LABELS, fmt_elapsed
from tasklight.overlay.types import AgentRow, GroupSummary, HeaderRow


def _summary_for_group(records: list[AgentRecord], now: float) -> GroupSummary:
    elapsed = fmt_elapsed(min(now - record.state_entered_at for record in records))

    for state, label in (
        (AgentState.APPROVAL, STATE_LABELS[AgentState.APPROVAL]),
        (AgentState.DONE, STATE_LABELS[AgentState.DONE]),
    ):
        matches = [record for record in records if record.state == state]
        if matches:
            return GroupSummary(matches[0].source, state, label, elapsed)

    active = [
        record
        for record in records
        if record.state in (AgentState.THINKING, AgentState.TOOL)
    ]
    source = active[0].source if active else ""
    return GroupSummary(source, AgentState.THINKING, "Working…", elapsed)


def build_rows(
    records: list[AgentRecord],
    collapsed_groups: set[tuple[str, str]],
    now: float | None = None,
) -> list[HeaderRow | AgentRow]:
    visible_records = [record for record in records if not record.dismissed]
    if not visible_records:
        return [AgentRow("", "", AgentState.DONE, "No agents", "")]

    has_remote = any(record.hostname for record in visible_records)

    grouped_records: dict[tuple[str, str], list[AgentRecord]] = {}
    for record in visible_records:
        grouped_records.setdefault((record.hostname, record.dirname), []).append(record)

    current_time = time.monotonic() if now is None else now
    rows: list[HeaderRow | AgentRow] = []
    for (hostname, dirname), group in sorted(grouped_records.items()):
        display_hostname = (hostname or "local") if has_remote else ""
        group_key = (hostname, dirname)
        if group_key in collapsed_groups:
            rows.append(HeaderRow(dirname, _summary_for_group(group, current_time), display_hostname, group_key))
            continue

        rows.append(HeaderRow(dirname, hostname=display_hostname, group_key=group_key))
        for record in group:
            label = (
                f"Tool: {record.tool_name or '?'}"
                if record.state == AgentState.TOOL
                else STATE_LABELS[record.state]
            )
            rows.append(
                AgentRow(
                    record.session_id,
                    record.source,
                    record.state,
                    label,
                    fmt_elapsed(current_time - record.state_entered_at),
                )
            )

    return rows
