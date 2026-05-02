"""Unit tests for tasklight.overlay.sparkline and related model logic."""

import json

import pytest

from tasklight.model import AgentRecord, AgentState, TokenSample
from tasklight.overlay.sparkline import (
    compute_mean_rate,
    is_in_reset_edge,
    iter_segments,
    smoothed_rate,
)
from tasklight.server import (
    _context_window_for_model,
    _extract_usage_from_transcript_tail,
    _parse_multipart,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_history(*pairs: tuple[float, int]) -> list[TokenSample]:
    """Build a TokenSample list from (t, tokens) pairs."""
    return [TokenSample(t, tok) for t, tok in pairs]


def make_record(session_id: str = "test") -> AgentRecord:
    import time

    now = time.monotonic()
    return AgentRecord(
        session_id=session_id,
        source="claude-code",
        cwd="/tmp/test",
        dirname="test",
        state=AgentState.THINKING,
        started_at=now,
        state_entered_at=now,
    )


# ---------------------------------------------------------------------------
# iter_segments
# ---------------------------------------------------------------------------


class TestIterSegments:
    def test_empty(self):
        assert list(iter_segments([])) == []

    def test_single_sample(self):
        h = make_history((0.0, 1000))
        assert list(iter_segments(h)) == []

    def test_two_samples(self):
        h = make_history((0.0, 1000), (10.0, 2000))
        segs = list(iter_segments(h))
        assert len(segs) == 1
        s = segs[0]
        assert s.t_a == 0.0
        assert s.t_b == 10.0
        assert s.dt == pytest.approx(10.0)
        assert s.rate == pytest.approx(100.0)  # (2000-1000)/10
        assert s.midpoint == pytest.approx(5.0)

    def test_three_samples_two_segments(self):
        h = make_history((0.0, 0), (5.0, 500), (10.0, 1500))
        segs = list(iter_segments(h))
        assert len(segs) == 2
        assert segs[0].rate == pytest.approx(100.0)
        assert segs[1].rate == pytest.approx(200.0)

    def test_negative_rate_allowed(self):
        h = make_history((0.0, 2000), (10.0, 1900))
        segs = list(iter_segments(h))
        assert segs[0].rate == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# compute_mean_rate
# ---------------------------------------------------------------------------


class TestComputeMeanRate:
    def test_empty_returns_zero(self):
        assert compute_mean_rate([], 300, 100.0) == pytest.approx(0.0)

    def test_single_sample_returns_zero(self):
        h = make_history((0.0, 1000))
        assert compute_mean_rate(h, 300, 100.0) == pytest.approx(0.0)

    def test_two_equal_duration_segments(self):
        # Two segments: [0..10] rate=100, [10..20] rate=200
        # time-weighted mean = (100*10 + 200*10) / 20 = 150
        h = make_history((0.0, 0), (10.0, 1000), (20.0, 3000))
        mean = compute_mean_rate(h, 300, 30.0)
        assert mean == pytest.approx(150.0)

    def test_two_unequal_duration_segments(self):
        # [0..5] rate=200, [5..15] rate=100
        # weighted: (200*5 + 100*10) / 15 = 2000/15 ≈ 133.33
        h = make_history((0.0, 0), (5.0, 1000), (15.0, 2000))
        mean = compute_mean_rate(h, 300, 30.0)
        assert mean == pytest.approx(2000.0 / 15.0)

    def test_samples_outside_window_excluded(self):
        # Window is 10 s; now=100; cutoff=90.
        # Sample at t=0..80 falls entirely before window; only [80..100] counts.
        h = make_history((0.0, 0), (80.0, 8000), (100.0, 12000))
        mean = compute_mean_rate(h, 10, 100.0)
        # Only seg [80,100]: rate=(12000-8000)/20=200, but t_b=100 >= cutoff=90
        assert mean == pytest.approx(200.0)

    def test_constant_rate(self):
        h = make_history((0.0, 0), (60.0, 6000), (120.0, 12000))
        mean = compute_mean_rate(h, 300, 200.0)
        assert mean == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# smoothed_rate
# ---------------------------------------------------------------------------


class TestSmoothedRate:
    def test_empty_returns_zero(self):
        assert smoothed_rate([], 0.0, 10.0) == pytest.approx(0.0)

    def test_single_sample_returns_zero(self):
        h = make_history((0.0, 1000))
        assert smoothed_rate(h, 5.0, 10.0) == pytest.approx(0.0)

    def test_at_segment_midpoint(self):
        # One segment [0..10] rate=100; midpoint=5
        # smoothed at t=5: w = dt * exp(0) = 10, result=100
        h = make_history((0.0, 0), (10.0, 1000))
        result = smoothed_rate(h, 5.0, 10.0)
        assert result == pytest.approx(100.0)

    def test_far_from_segment_is_downweighted(self):
        # One segment [0..10] rate=100; midpoint=5
        # smoothed at t=100 (far away with tau=10): exp(-95/10) ≈ 0, so ≈ 100
        # (still the only segment, so even with tiny weight, result is 100)
        h = make_history((0.0, 0), (10.0, 1000))
        result = smoothed_rate(h, 100.0, 10.0)
        # The segment is outside the 5*tau=50 cutoff, so result=0
        assert result == pytest.approx(0.0)

    def test_two_segments_interpolated(self):
        # Seg A [0..10] rate=100, midpoint=5
        # Seg B [10..20] rate=200, midpoint=15
        # At t=10 (tau=5): distance to A=5, distance to B=5, equal weights
        # result = (100*dt_A*exp(-1) + 200*dt_B*exp(-1)) / (dt_A*exp(-1)+dt_B*exp(-1))
        # = (100*10 + 200*10) / (10+10) = 150
        h = make_history((0.0, 0), (10.0, 1000), (20.0, 3000))
        result = smoothed_rate(h, 10.0, 5.0)
        assert result == pytest.approx(150.0)

    def test_negative_rate_passthrough(self):
        # smoothed_rate itself does not clamp; the painter does. Confirm
        # negative segment rates flow through unmodified.
        h = make_history((0.0, 5000), (10.0, 4000))
        result = smoothed_rate(h, 5.0, 10.0)
        assert result == pytest.approx(-100.0)


# ---------------------------------------------------------------------------
# is_in_reset_edge
# ---------------------------------------------------------------------------


class TestIsInResetEdge:
    def test_empty_resets(self):
        assert not is_in_reset_edge([], 5.0)

    def test_inside_interval(self):
        assert is_in_reset_edge([(10.0, 20.0)], 15.0)

    def test_at_boundaries(self):
        assert is_in_reset_edge([(10.0, 20.0)], 10.0)
        assert is_in_reset_edge([(10.0, 20.0)], 20.0)

    def test_outside_interval(self):
        assert not is_in_reset_edge([(10.0, 20.0)], 9.99)
        assert not is_in_reset_edge([(10.0, 20.0)], 20.01)

    def test_multiple_intervals(self):
        resets = [(10.0, 20.0), (50.0, 55.0)]
        assert is_in_reset_edge(resets, 15.0)
        assert is_in_reset_edge(resets, 52.0)
        assert not is_in_reset_edge(resets, 30.0)


# ---------------------------------------------------------------------------
# Reset rule (§3.3) — via AgentStateModel._append_token_sample
# ---------------------------------------------------------------------------


class TestResetRule:
    def _append(self, record, tokens, window_s=300, reset_fraction=0.20):
        """Call the private helper directly for testing.

        ``tokens`` is bucketed under input_tokens for back-compat with
        scalar-total tests; reset rule operates on the total which is
        equivalent.
        """
        from tasklight.model import AgentStateModel

        model = AgentStateModel.__new__(AgentStateModel)
        model._append_token_sample(
            record,
            input_tokens=tokens,
            window_s=window_s,
            reset_fraction=reset_fraction,
        )

    def test_normal_growth_accumulates(self):
        r = make_record()
        self._append(r, 1000)
        self._append(r, 2000)
        self._append(r, 3000)
        assert len(r.token_history) == 3
        assert r.token_history[-1].input_tokens == 3000

    def test_small_drop_no_reset(self):
        r = make_record()
        self._append(r, 1000)
        self._append(r, 950)  # 5% drop — below 20% threshold
        assert len(r.token_history) == 2

    def test_large_drop_triggers_reset(self):
        r = make_record()
        self._append(r, 1000)
        self._append(r, 2000)
        self._append(r, 3000)
        # Drop > 20%: 500 < 3000 * 0.80 = 2400
        self._append(r, 500)
        assert len(r.token_history) == 1
        assert r.token_history[0].input_tokens == 500
        # Reset edge recorded as (prev.t, new.t).
        assert len(r.token_resets) == 1
        prev_t, new_t = r.token_resets[0]
        assert new_t > prev_t
        assert new_t == pytest.approx(r.token_history[0].t)

    def test_exactly_at_threshold_no_reset(self):
        # 800 = 1000 * (1 - 0.20); not strictly less than => no reset
        r = make_record()
        self._append(r, 1000)
        self._append(r, 800, reset_fraction=0.20)
        assert len(r.token_history) == 2

    def test_just_below_threshold_triggers_reset(self):
        r = make_record()
        self._append(r, 1000)
        self._append(r, 799, reset_fraction=0.20)  # 799 < 800
        assert len(r.token_history) == 1
        assert r.token_history[0].input_tokens == 799


# ---------------------------------------------------------------------------
# Window trim (§3.4)
# ---------------------------------------------------------------------------


class TestWindowTrim:
    def _append(self, record, tokens, window_s=300):
        from tasklight.model import AgentStateModel

        model = AgentStateModel.__new__(AgentStateModel)
        model._append_token_sample(record, input_tokens=tokens, window_s=window_s)

    def test_old_samples_trimmed(self):
        import time

        r = make_record()
        # Manually inject old samples.
        old_t = time.monotonic() - 400  # older than window_s=300
        r.token_history = [
            TokenSample(old_t, 1000),
            TokenSample(old_t + 1, 1010),
            TokenSample(old_t + 2, 1020),
        ]
        # Append a fresh sample; trim should remove old ones (keep >=2 is satisfied).
        self._append(r, 2000, window_s=300)
        # After trim: old samples discarded, only last 2 kept if needed.
        # All 3 old samples are below cutoff, but we keep >=2, so:
        # history = [old[-1], new] at minimum.
        assert len(r.token_history) >= 2

    def test_minimum_two_kept_even_when_old(self):
        import time

        r = make_record()
        old_t = time.monotonic() - 1000
        r.token_history = [
            TokenSample(old_t, 1000),
            TokenSample(old_t + 1, 1010),
        ]
        self._append(r, 2000, window_s=5)  # very short window
        # Both old samples are beyond cutoff, but we keep >=2.
        assert len(r.token_history) >= 2


# ---------------------------------------------------------------------------
# JSONL reverse-scan parser (_extract_usage_from_transcript_tail)
# ---------------------------------------------------------------------------


class TestExtractUsage:
    def _make_line(self, obj: dict) -> bytes:
        return json.dumps(obj).encode() + b"\n"

    def test_empty_returns_none(self):
        assert _extract_usage_from_transcript_tail(b"") is None

    def test_simple_message_usage(self):
        line = self._make_line(
            {
                "message": {
                    "usage": {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 50,
                        "cache_read_input_tokens": 25,
                    }
                }
            }
        )
        result = _extract_usage_from_transcript_tail(line)
        assert result == {
            "input_tokens": 100,
            "cache_creation_tokens": 50,
            "cache_read_tokens": 25,
        }

    def test_missing_sub_fields_default_zero(self):
        line = self._make_line({"message": {"usage": {"input_tokens": 200}}})
        result = _extract_usage_from_transcript_tail(line)
        assert result == {
            "input_tokens": 200,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }

    def test_no_usage_returns_none(self):
        line = self._make_line({"type": "tool_result", "content": "ok"})
        result = _extract_usage_from_transcript_tail(line)
        assert result is None

    def test_scans_in_reverse(self):
        # Two lines with usage; the last one should be found (reverse scan).
        line1 = self._make_line({"message": {"usage": {"input_tokens": 100}}})
        line2 = self._make_line({"message": {"usage": {"input_tokens": 999}}})
        tail = line1 + line2
        result = _extract_usage_from_transcript_tail(tail)
        assert result["input_tokens"] == 999

    def test_truncated_leading_line_skipped(self):
        # Simulate a truncated first line (mid-JSON) followed by valid lines.
        truncated = b'{"message": {"usage": {"input_to'
        valid = self._make_line({"message": {"usage": {"input_tokens": 42}}})
        tail = truncated + b"\n" + valid
        result = _extract_usage_from_transcript_tail(tail)
        assert result["input_tokens"] == 42

    def test_mixed_lines_no_usage(self):
        lines = b"".join(
            [
                self._make_line({"type": "tool_use", "name": "bash"}),
                self._make_line({"type": "tool_result"}),
            ]
        )
        result = _extract_usage_from_transcript_tail(lines)
        assert result is None

    def test_all_sub_fields_present(self):
        line = self._make_line(
            {
                "message": {
                    "usage": {
                        "input_tokens": 10000,
                        "cache_creation_input_tokens": 5000,
                        "cache_read_input_tokens": 2000,
                    }
                }
            }
        )
        result = _extract_usage_from_transcript_tail(line)
        assert result == {
            "input_tokens": 10000,
            "cache_creation_tokens": 5000,
            "cache_read_tokens": 2000,
        }

    def test_includes_context_window_when_model_known(self):
        line = self._make_line(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 100, "cache_read_input_tokens": 50},
                },
            }
        )
        result = _extract_usage_from_transcript_tail(line)
        assert result["context_window_max"] == 200_000

    def test_omits_context_window_when_model_unknown(self):
        line = self._make_line(
            {
                "message": {
                    "model": "gpt-4-turbo",
                    "usage": {"input_tokens": 100},
                },
            }
        )
        result = _extract_usage_from_transcript_tail(line)
        assert "context_window_max" not in result

    def test_context_window_for_model_helper(self):
        assert _context_window_for_model("claude-opus-4-7") == 200_000
        assert _context_window_for_model("claude-sonnet-4-6-20250812") == 200_000
        assert _context_window_for_model("claude-haiku-4-5") == 200_000
        assert _context_window_for_model("gpt-4") is None
        assert _context_window_for_model(None) is None
        assert _context_window_for_model("") is None

    def test_context_window_1m_heuristic(self):
        # claude-*-1m variants ship with a 1M-token context window.
        assert _context_window_for_model("claude-sonnet-4-1m") == 1_000_000
        assert _context_window_for_model("claude-sonnet-4-1m-20250812") == 1_000_000
        # "1m" embedded in a non-suffix position should NOT trigger
        # the heuristic (false-positive guard).
        assert _context_window_for_model("claude-1monkey-4") == 200_000

    def test_curl_at_form_with_filename(self):
        """curl `-F field=@file` adds filename="..." to Content-Disposition.

        The field-name regex must not match `name="..."` as part of
        `filename="..."` — that would mis-key the field under the
        filename and silently drop the transcript tail (issue caught in
        manual sparkline testing).
        """
        body = (
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="hook"\r\n'
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"session_id":"abc"}\r\n'
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="transcript_tail"; filename="tmp.abc"\r\n'
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"transcript-data\r\n"
            b"--boundary--\r\n"
        )
        fields = _parse_multipart("multipart/form-data; boundary=boundary", body)
        assert "hook" in fields
        assert "transcript_tail" in fields
        assert "tmp.abc" not in fields
        assert fields["transcript_tail"] == b"transcript-data"

    def test_extra_fields_ignored(self):
        line = self._make_line(
            {
                "type": "assistant",
                "message": {
                    "id": "msg_xxx",
                    "usage": {
                        "input_tokens": 300,
                        "output_tokens": 150,  # not counted
                        "cache_read_input_tokens": 100,
                    },
                    "content": [],
                },
            }
        )
        result = _extract_usage_from_transcript_tail(line)
        assert result == {
            "input_tokens": 300,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 100,
        }
