import { readFileSync } from "fs";
import { hostname } from "os";

const PORT = 57017;

function resolveUrls() {
  if (process.env.TASKLIGHT_URL) return [process.env.TASKLIGHT_URL];
  if (process.env.WSL_DISTRO_NAME) {
    try {
      const match = readFileSync("/etc/resolv.conf", "utf8").match(/^nameserver\s+(\S+)/m);
      if (match) return [`http://${match[1]}:${PORT}/hook`, `http://127.0.0.1:${PORT}/hook`];
    } catch {}
  }
  return [`http://127.0.0.1:${PORT}/hook`];
}

const TASKLIGHT_URLS = resolveUrls();
const HOSTNAME = hostname();

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
  for (const url of TASKLIGHT_URLS) {
    try {
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      return;
    } catch {}
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
          hostname: HOSTNAME,
          event: "start",
          data: {},
        });
      }

      if (event.type === "session.idle") {
        await post({
          source: "opencode",
          session_id: currentSessionId,
          cwd,
          hostname: HOSTNAME,
          event: "stop",
          data: {},
        });
      }

      if (event.type === "session.deleted") {
        await post({
          source: "opencode",
          session_id: currentSessionId,
          cwd,
          hostname: HOSTNAME,
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
        hostname: HOSTNAME,
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
        hostname: HOSTNAME,
        event: "thinking",
        data: {},
      });
    },
  };
};
