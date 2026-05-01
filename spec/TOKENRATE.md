# Tasklight – Token Rate Sparkline

Companion spec to [DESIGN.md](DESIGN.md). Adds an optional in-row chart that
visualises how fast a connected agent is consuming its context window.

---

## 1. Goal

Give a fast, glanceable read on how aggressively each agent is burning context.
The chart sits **behind** the existing row content (glyph, label, elapsed time)
so the row stays readable. It only appears when the agent's hook actually
reports context-window usage; agents that never send the field render exactly
as today.

Visual target:

- Simple sparkline: a single-color filled area anchored to the **bottom** of
  the row, with height proportional to the smoothed net burn rate.
- One accent color throughout — no green/red split, no centreline.
- New samples enter at the **right edge** of the row and scroll **left** over
  time. The right edge is "now"; the left edge is `now - window_s` ago.
- The chart is anchored to the row's right edge (under the elapsed-time
  column) and extends left across the row width.
- A compaction / reset shows up as the line dropping to the floor for one
  segment and resuming from the new baseline (see §3.3).

---

## 2. Inputs from Hooks

The server exposes **two** endpoints. Both feed the same per-agent token
history (§3) — they differ only in what the hook command on the wire has to
look like.

| Endpoint | Used by | Payload shape |
|---|---|---|
| `/hook` | opencode, Codex, custom sources | Tasklight's normalised event schema (existing today) |
| `/hook/claude-code` | Claude Code only | Claude Code's native hook JSON (POST stdin verbatim) |

The server **does not** read `transcript_path` or any other filesystem path
referenced by a hook. Token counts must be carried inline in the request
body. This keeps the server free of arbitrary-file-read concerns and keeps
cross-host hooks (e.g. WSL → Windows) working unchanged.

### 2.1 Generic `/hook` — opencode / Codex / custom

The existing endpoint (GET query-string or POST JSON) gains one optional
field:

| Field | Type | Notes |
|---|---|---|
| `context_tokens` | int | Cumulative tokens currently held in the agent's context window at the moment the hook fires. Absolute count, not a delta. |

Examples:

```
GET /hook?...&event=tool_use&context_tokens=42137
```

```json
{ "source": "opencode", "session_id": "...", "cwd": "...",
  "event": "thinking", "context_tokens": 42137 }
```

If absent, the event is recorded as today and the sparkline does not appear
for that agent. If present, the value MUST be a non-negative integer; on
parse failure the field is dropped with a single warn log rather than
rejecting the whole event.

The hook command is responsible for computing `context_tokens` itself
(formula varies per source; document per source as we wire each in).

### 2.2 Claude-specific `/hook/claude-code`

A new POST endpoint that accepts Claude Code's native hook JSON (the same
JSON Claude Code writes to the hook process's stdin) **plus** a raw tail of
the session transcript JSONL. Both pieces are passed through verbatim as
`multipart/form-data` fields; the server does all parsing.

This endpoint exists so that the Claude-specific shaping — event-name
mapping, hostname injection, JSONL parsing, usage extraction — moves out
of `hooks/claude.settings.json` and into the server, where it stays
versioned with Tasklight. The shell-side hook becomes one block that is
identical across every Claude Code event.

#### 2.2.1 Request body

`Content-Type: multipart/form-data` with two fields:

| Field | Required | Type | Content |
|---|---|---|---|
| `hook` | yes | `application/json` | Claude Code's hook stdin JSON, byte-for-byte |
| `transcript_tail` | no | `text/plain` | Raw bytes of the last few lines of the transcript JSONL (default 20), or empty if unreadable |

Headers:

| Header | Required | Content |
|---|---|---|
| `X-Tasklight-Hostname` | no | `hostname` output from the hook host. Falls back to the peer address if absent. |

The `hook` field carries Claude Code's native fields (`session_id`,
`cwd`, `hook_event_name`, `tool_name`, `permission_mode`,
`transcript_path`, …) untouched. The server reads what it needs and
ignores the rest. `transcript_path` is accepted but never opened — the
cross-host invariant from §2 still holds.

#### 2.2.2 Server behaviour

The server:

1. Parses the `hook` field as JSON. Required fields: `session_id`, `cwd`,
   `hook_event_name`. Missing required fields → 400.
2. Maps `hook_event_name` → internal event using the table below, then
   routes the result through the same `AgentStateModel.apply_event`
   pipeline as `/hook`. Unknown event names are silently ignored.
3. If `transcript_tail` is non-empty: splits on `\n`, iterates **in
   reverse**, attempts `json.loads` on each line, and skips lines that
   fail to parse (the leading line of the tail may be truncated mid-JSON;
   that's expected). On the first line where `message.usage` is an
   object, computes
   `context_tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens`
   (missing sub-fields default to 0) and appends a `TokenSample` to the
   agent's history per §3.2.
4. Hostname comes from `X-Tasklight-Hostname` if present, else the peer
   address.

| `hook_event_name` | Internal event |
|---|---|
| `SessionStart` | `start` |
| `UserPromptSubmit` | `thinking` |
| `PreToolUse` | `tool_use` |
| `PostToolUse` | `thinking` |
| `PermissionRequest` | `approval_required` |
| `Stop` | `stop` |
| `SessionEnd` | `exit` |

The `source` field on the internal event is hard-coded to `"claude-code"`
for everything that arrives on this endpoint.

#### 2.2.3 Hook command shape

The shell logic lives in a single script,
`hooks/tasklight-claude-hook.sh`, installed to
`~/.claude/hooks/tasklight-claude-hook.sh`. Every Claude Code event in
`claude.settings.json` calls it with no arguments:

```json
{
  "type": "command",
  "command": "bash ~/.claude/hooks/tasklight-claude-hook.sh"
}
```

The server reads `hook_event_name` from the stdin JSON, so a single
script handles all events. The script:

```sh
#!/usr/bin/env bash
set -u

INPUT=$(cat)
IF=$(mktemp); printf '%s' "$INPUT" > "$IF"

TP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
TF=$(mktemp)
if [ -n "$TP" ] && [ -r "$TP" ]; then
  tail -n "${TASKLIGHT_TAIL_LINES:-20}" "$TP" > "$TF"
fi

WSL_HOST=
if [ -n "${WSL_DISTRO_NAME:-}" ]; then
  WSL_HOST=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
fi

HOST=${TASKLIGHT_HOST:-${WSL_HOST:-127.0.0.1}}
PORT=${TASKLIGHT_PORT:-57017}

curl -sf -m 1 \
  -H "X-Tasklight-Hostname: $(hostname)" \
  -F "hook=<$IF;type=application/json" \
  -F "transcript_tail=@$TF;type=text/plain" \
  "http://${HOST}:${PORT}/hook/claude-code" \
  >/dev/null 2>&1 || true

rm -f "$IF" "$TF"
```

Notes:

- The single `jq -r '.transcript_path // empty'` extraction is the only
  parsing the script performs; everything else is byte-passthrough.
- The hook JSON is staged to a temp file and sent via `-F "hook=<$IF;…"`
  (rather than `-F "hook=$INPUT;…"`) so curl's `@`/`<` value-prefix
  interpretation and embedded-newline handling can never bite. Both
  temp files are cleaned up on exit.
- `tail -n 20` is the default; override with `TASKLIGHT_TAIL_LINES`. The
  most recent `message.usage` is typically within the last few lines,
  but parallel tool batches can push it back.
- Multipart (rather than a JSON wrapper) avoids escaping the transcript
  bytes in the shell — both fields are sent raw.
- If the transcript can't be read, `transcript_tail` is empty and the
  server skips the token-history append. The state event still
  registers.
- Cross-host (WSL → Windows host running Tasklight) keeps working: the
  hook reads the transcript locally and ships the bytes over HTTP.
- Hook reliability rules from DESIGN §4.5 still apply (`-sf -m 1`,
  `|| true`).

### 2.3 Which events carry usage

| Source | Events that should carry token data |
|---|---|
| Claude Code | `PostToolUse`, `Stop`, `UserPromptSubmit` — post-turn boundaries with fresh `usage`. `PreToolUse` MAY include the previous turn's usage; harmless but stale. |
| opencode / Codex | TBD per source; document when wired. |

Tasklight treats every event uniformly: an event with usable token data
appends a sample, an event without does not.

---

## 3. Data Model

### 3.1 Per-agent sample buffer

Extend `AgentRecord` with one new field:

```python
@dataclass
class TokenSample:
    t: float           # time.monotonic() at the hook
    tokens: int        # cumulative context_tokens reported

@dataclass
class AgentRecord:
    ...
    token_history: list[TokenSample] = field(default_factory=list)
```

The list is kept sorted by `t` (always append-only in receive order, which is
already monotonic since `apply_event` runs on the main thread).

### 3.2 Append rule

When a hook payload contains `context_tokens`:

1. Compute `now = time.monotonic()`.
2. Append `TokenSample(now, context_tokens)` to `record.token_history`.
3. Apply the **reset rule** (§3.3).
4. Apply the **window trim** (§3.4).
5. Mark the row dirty so the overlay repaints.

Hook events without `context_tokens` do not touch the buffer.

### 3.3 Reset rule (context-window drop)

A drop in cumulative tokens means the context was compacted, cleared, or a
new turn started with a smaller window. The negative delta is not a real
"burn rate" and would otherwise pollute both the running average and the
chart, so we discard pre-reset samples and keep only the new baseline.

Algorithm, applied after every append:

- Let `prev` be the last-appended sample before the new one (if any).
- Let `new` be the just-appended sample.
- If `new.tokens < prev.tokens * (1 - reset_fraction)`
  (default `reset_fraction = 0.20`, i.e. a >20% drop):
  - Mark the segment `[prev, new]` as a **reset edge**: it is excluded from
    rate calculations and rendered as a forced gap (chart drops to the
    floor at that x position).
  - Truncate the history so that only `new` remains. All older samples
    (including `prev`) are discarded; they are no longer comparable to the
    new baseline.
  - The next sample produces the first post-reset segment, measured against
    `new`.

This means a reset shows up as the chart briefly returning to zero at the
right edge, then climbing again as new samples arrive — never producing a
spurious negative rate or polluting the moving average across the boundary.

A drop smaller than `reset_fraction` is treated as ordinary noise: the
segment's signed rate (slightly negative) feeds the chart and the average
normally, no truncation.

### 3.4 Window trim

After each append, drop samples with `t < now - window_s`
(default `window_s = 300`). Keep at minimum the most recent two samples even
if both are older than the window, so a long-idle agent still renders a flat
line instead of disappearing.

---

## 4. Rate Calculation

### 4.1 Per-segment rate

Between consecutive samples `a, b` define:

```
dt    = b.t - a.t                    # always > 0
dtok  = b.tokens - a.tokens          # signed: + = upload, - = drop
rate  = dtok / dt                    # tokens per second, signed
```

`rate` is what the chart plots. Each segment occupies the time interval
`[a.t, b.t]` on the x-axis.

### 4.2 Running average (used for y-axis scale only)

A time-weighted mean of `rate` over the window gives a stable reference for
auto-scaling the chart's y-axis (so a calm period doesn't render as a flat
line at the floor):

```
mean_rate = sum(rate_i * dt_i) / sum(dt_i)   over segments in window
```

When the buffer has fewer than two samples, `mean_rate = 0` and nothing is
drawn.

### 4.3 Display smoothing

Per-segment `rate` is jaggy at the granularity of individual hook events.
Smooth it for display only (the underlying samples are kept raw):

- For each pixel column `x` of the chart, find the time `t_x` it represents
  (§5.1) and compute a smoothed rate via an exponential weighting of nearby
  segments, with time constant `tau_s = window_s / 30` (default 10 s for a
  300 s window):

  ```
  smoothed(t) = sum(rate_i * w_i) / sum(w_i)
      where w_i = dt_i * exp(-|t - midpoint_i| / tau_s)
  ```

- Reset edges (§3.3) are excluded from the kernel entirely, and pixel
  columns whose `t_x` falls inside a reset edge render at zero (chart drops
  to the floor) so the discontinuity stays visible.

`smoothed(t)` clamped to `max(0, ·)` is what the chart fill follows.

---

## 5. Rendering

### 5.1 Geometry

Defined per row, in the row's local coordinate space:

| Quantity | Value |
|---|---|
| `chart_right` | row right edge, minus `metrics.pad`, minus `metrics.elapsed_width`, minus `metrics.text_gap` |
| `chart_left`  | `chart_right - chart_width` |
| `chart_width` | `theme.token_rate.width_em * metrics.em` (default `width_em = 24`); clamped to fit between `metrics.label_x` and `chart_right` |
| `chart_top` | `layout_row.top + 1` |
| `chart_bottom` | `layout_row.top + layout_row.height - 1` |

The chart sits in a band that overlaps the label and (optionally) the
elapsed column; text is drawn **after** the chart so it remains legible.
The fill is anchored to `chart_bottom` and grows upward.

The x-axis maps time to pixels:

```
t_x = now - ((chart_right - x) / chart_width) * window_s
```

so `x = chart_right` is `now`, `x = chart_left` is `now - window_s`. The
chart visibly scrolls right-to-left because every repaint advances `now`.

### 5.2 Y-axis scale

```
y_scale = (chart_bottom - chart_top - 1) / max(mean_rate * scale_headroom, 1.0)
```

`scale_headroom` defaults to `2.5`, so a rate of `2.5 × mean_rate`
saturates the top of the band. Clamp drawn `y` values to the band — large
spikes get clipped rather than blowing out the row.

### 5.3 Painting order

Inside `OverlayWidget.paintEvent`, **before** the existing per-row text
painting:

1. Skip if `record.token_history` has fewer than 2 samples or
   `theme.token_rate.enabled` is false.
2. Set clip to the chart rect.
3. Build a polyline of
   `(x, chart_bottom - max(0, smoothed(t_x)) * y_scale)`
   for every integer `x` in `[chart_left, chart_right]`. Pixel columns
   inside a reset edge (§4.3) emit `y = chart_bottom` (floor).
4. Fill the polygon formed by that polyline closed down to `chart_bottom`
   using `theme.token_rate.color` at `theme.token_rate.fill_alpha`
   (default `#5599cc` @ alpha `0.35`).
5. Optionally stroke the top edge of the polyline at full alpha for a
   crisper outline (controlled by `theme.token_rate.stroke_alpha`,
   default `0.0` = off).
6. Release clip; existing label/glyph/elapsed text paints over the chart.

The fill alpha is intentionally low so text on top stays readable.

### 5.4 When to draw on which row

The chart is **per agent row**, not per group header. Collapsed group
headers do not show a chart; their summary already aggregates state, and
mixing per-agent rates would be misleading.

`APPROVAL` rows also skip the chart — the yellow approval tint already owns
the row background and the chart would clash with it.

`DONE` rows draw the final chart but it freezes (no new samples arrive).
After `done_auto_remove_s` it is removed with the row.

### 5.5 Repaint cadence

Add the chart's needs to the existing animation timer (`_anim`, 125 ms /
8 fps): if any visible row has at least two samples, the timer must run so
the chart scrolls smoothly. The timer's existing condition (any
THINKING/TOOL row present) becomes `OR (any row has token_history with
>= 2 samples and the chart is enabled)`.

Repainting the full widget at 8 fps is fine — it already happens for
spinners. No separate timer.

---

## 6. Configuration

Extend `ThemeConfig` (in `tasklight/config.py`):

```python
@dataclass
class TokenRateConfig:
    enabled: bool = True
    window_s: int = 300            # sliding window length
    width_em: float = 24.0         # chart width in em units
    reset_fraction: float = 0.20   # tokens drop ratio that triggers reset
    scale_headroom: float = 2.5    # y-axis headroom multiplier
    smoothing_tau_s: float = 0.0   # 0 = auto = window_s / 30
    color: str = "#5599cc" # TODO: Split into stroke_color and fill_color
    fill_alpha: float = 0.35
    stroke_alpha: float = 0.0      # 0 = no stroke, just the fill
```

```python
@dataclass
class ThemeConfig:
    ...
    token_rate: TokenRateConfig = field(default_factory=TokenRateConfig)
```

Update `_write_defaults` and the YAML loader to round-trip `theme.token_rate`.
All keys hot-reload like the rest of the theme.

---

## 7. Project Layout

New module:

- `tasklight/overlay/sparkline.py` — pure functions:
  - `compute_mean_rate(history, window_s, now) -> float`
  - `smoothed_rate(history, t, tau_s) -> float`
  - `iter_segments(history) -> Iterable[Segment]` (yields normal segments
    plus reset-edge markers)
  - `paint_sparkline(painter, rect, history, cfg, now)` — the only function
    that touches `QPainter`.

`OverlayWidget._paint_agent_row` calls `paint_sparkline` before its existing
text drawing, gated on `cfg.theme.token_rate.enabled` and history length.

No changes to the `view_model` / `layout` modules: the chart is a pure
overlay on existing rows and does not affect row geometry.

---

## 8. Performance

- `O(N)` per repaint, where `N` is samples in window. With ~one sample per
  hook event and 300 s window, `N` rarely exceeds a few hundred even under
  heavy use.
- The smoothing kernel is evaluated once per pixel column (~`width_em * em`
  pixels, e.g. 24 × 8 = 192 columns at default font). For each column it
  scans only segments whose midpoint is within `~5 * tau_s` of `t_x`; that
  bound is enforced by early-exit in `smoothed_rate`.
- Resets truncate the buffer (§3.3), so history can never grow unboundedly
  across compaction events.

---

## 9. Open Questions

1. **Per-source token semantics for `/hook`.** opencode and Codex may
   report tokens differently (or not at all). Document the recommended
   hook formula per source as we wire each in; the server treats every
   `context_tokens` value uniformly.
2. **Transcript-tail size.** `tail -n 6` is the starting heuristic. If we
   see sessions where the most recent `message.usage` line falls outside
   that window (e.g. interleaved tool-result lines push it out), bump
   the count or switch to a byte-bounded tail (`tail -c 64K`).
3. **Should `mean_rate` use a longer window than the chart itself?** A
   quieter scale baseline (e.g. 30 min mean) would make short bursts more
   visually striking. Defer until we've used the simple version for a
   while.
