const TASKLIGHT_URL = process.env.TASKLIGHT_URL || "http://127.0.0.1:57017/hook";

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.length > 0) {
      return value;
    }
  }
  return null;
}

function sessionIdFrom(event, fallbackCwd) {
  return (
    firstString(
      event?.session?.id,
      event?.sessionID,
      event?.sessionId,
      event?.properties?.sessionID,
      event?.properties?.sessionId,
      event?.properties?.id,
      event?.id,
    ) || `opencode:${fallbackCwd}:${process.pid}`
  );
}

async function post(payload) {
  try {
    await fetch(TASKLIGHT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Tasklight should never break the agent.
  }
}

export const TasklightPlugin = async ({ directory, worktree }) => {
  const cwd = worktree || directory || process.cwd();
  let currentSessionId = `opencode:${cwd}:${process.pid}`;

  return {
    event: async ({ event }) => {
      if (!event?.type) {
        return;
      }

      currentSessionId = sessionIdFrom(event, cwd);

      if (event.type === "session.created") {
        await post({
          source: "opencode",
          session_id: currentSessionId,
          cwd,
          event: "start",
          data: {},
        });
      }

      if (event.type === "session.idle") {
        await post({
          source: "opencode",
          session_id: currentSessionId,
          cwd,
          event: "stop",
          data: {},
        });
      }

      if (event.type === "session.deleted") {
        await post({
          source: "opencode",
          session_id: currentSessionId,
          cwd,
          event: "exit",
          data: {},
        });
      }
    },

    "tool.execute.before": async (input) => {
      await post({
        source: "opencode",
        session_id: currentSessionId,
        cwd,
        event: "tool_use",
        data: {
          tool_name: firstString(input?.tool, input?.name) || "tool",
        },
      });
    },

    "tool.execute.after": async () => {
      await post({
        source: "opencode",
        session_id: currentSessionId,
        cwd,
        event: "thinking",
        data: {},
      });
    },
  };
};
