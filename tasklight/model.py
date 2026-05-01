"""Agent state model: in-memory records + QAbstractListModel."""

import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, pyqtSignal


class AgentState(Enum):
    THINKING = auto()
    TOOL = auto()
    APPROVAL = auto()
    DONE = auto()


@dataclass
class TokenSample:
    t: float                       # time.monotonic() at the hook
    input_tokens: int = 0          # cumulative non-cache input tokens
    cache_creation_tokens: int = 0 # cumulative tokens written to cache
    cache_read_tokens: int = 0     # cumulative tokens read from cache

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


@dataclass
class AgentRecord:
    session_id: str
    source: str
    cwd: str
    dirname: str
    state: AgentState
    hostname: str = ""
    tool_name: str | None = None
    state_entered_at: float = field(default_factory=time.monotonic)
    started_at: float = field(default_factory=time.monotonic)
    dismissed: bool = False
    token_history: list[TokenSample] = field(default_factory=list)
    # (t_a, t_b) reset-edge intervals: time of the discarded prev sample
    # to time of the new baseline. Pixels in this interval render at the
    # chart floor so the discontinuity stays visible.
    token_resets: list[tuple[float, float]] = field(default_factory=list)


# Maps incoming event names to the next AgentState (None = no change / special).
_EVENT_STATE: dict[str, AgentState | None] = {
    "start":            AgentState.THINKING,
    "thinking":         AgentState.THINKING,
    "tool_use":         AgentState.TOOL,
    "tool_result":      AgentState.THINKING,
    "approval_required": AgentState.APPROVAL,
    "approval_granted": AgentState.THINKING,
    "stop":             AgentState.DONE,
    "exit":             None,
}


class AgentStateModel(QAbstractListModel):
    """Flat list model; callers group by dirname themselves."""

    layout_changed_custom = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._records: list[AgentRecord] = []

    # ------------------------------------------------------------------
    # QAbstractListModel interface
    # ------------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._records)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._records)):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._records[index.row()]
        return None

    # ------------------------------------------------------------------
    # Public read access
    # ------------------------------------------------------------------

    def records(self) -> list[AgentRecord]:
        return list(self._records)

    # ------------------------------------------------------------------
    # Mutations (call only from the Qt main thread)
    # ------------------------------------------------------------------

    def apply_event(self, payload: dict) -> None:
        """Process one validated hook payload and update state."""
        source = payload.get("source", "")
        session_id = payload.get("session_id", "")
        cwd = payload.get("cwd", "")
        event = payload.get("event", "")
        data = payload.get("data") or {}

        if event not in _EVENT_STATE:
            return

        record = self._find(session_id)

        if event == "exit":
            if record is not None:
                self._remove(record)
            return

        next_state = _EVENT_STATE[event]

        if record is None:
            now = time.monotonic()
            record = AgentRecord(
                session_id=session_id,
                source=source,
                cwd=cwd,
                dirname=os.path.basename(cwd) or cwd,
                state=next_state,
                hostname=payload.get("hostname") or "",
                started_at=now,
                state_entered_at=now,
            )
            self.beginInsertRows(QModelIndex(), len(self._records), len(self._records))
            self._records.append(record)
            self.endInsertRows()
        else:
            prev_state = record.state
            record.state = next_state
            if prev_state == AgentState.DONE:
                record.state_entered_at = time.monotonic()
            record.dismissed = False

        if next_state == AgentState.TOOL:
            record.tool_name = data.get("tool_name")
        else:
            record.tool_name = None

        # Append token sample if usage breakdown is present in payload.
        # The server validates and constructs the dict before emitting,
        # so we trust the shape here.
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self._append_token_sample(
                record,
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                cache_creation_tokens=int(usage.get("cache_creation_tokens", 0) or 0),
                cache_read_tokens=int(usage.get("cache_read_tokens", 0) or 0),
            )

        row = self._records.index(record)
        idx = self.index(row)
        self.dataChanged.emit(idx, idx)

    def _append_token_sample(
        self,
        record: AgentRecord,
        input_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        window_s: int = 300,
        reset_fraction: float = 0.20,
    ) -> None:
        """Append a TokenSample and apply reset/trim rules (§3.2–§3.4)."""
        now = time.monotonic()
        new_sample = TokenSample(
            now,
            input_tokens=input_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
        )

        # Reset rule §3.3: if total tokens dropped > reset_fraction,
        # record the reset edge [prev.t, new.t] and discard old history.
        if record.token_history:
            prev = record.token_history[-1]
            if new_sample.total < prev.total * (1 - reset_fraction):
                record.token_resets.append((prev.t, new_sample.t))
                record.token_history.clear()

        record.token_history.append(new_sample)

        # Window trim §3.4: drop samples older than window_s, but keep ≥2.
        cutoff = now - window_s
        while len(record.token_history) > 2 and record.token_history[0].t < cutoff:
            record.token_history.pop(0)
        # Trim reset edges that have fully scrolled off the window.
        record.token_resets = [
            (a, b) for (a, b) in record.token_resets if b >= cutoff
        ]

    def reset(self) -> None:
        if not self._records:
            return
        self.beginRemoveRows(QModelIndex(), 0, len(self._records) - 1)
        self._records.clear()
        self.endRemoveRows()

    def dismiss(self, session_id: str) -> None:
        record = self._find(session_id)
        if record is not None:
            self._remove(record)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find(self, session_id: str) -> AgentRecord | None:
        for r in self._records:
            if r.session_id == session_id:
                return r
        return None

    def _remove(self, record: AgentRecord) -> None:
        row = self._records.index(record)
        self.beginRemoveRows(QModelIndex(), row, row)
        self._records.pop(row)
        self.endRemoveRows()
