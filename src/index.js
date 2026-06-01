// Relay for the "Fitness" Garmin watchface.
//
// A GitHub Actions bot logs into your Garmin Connect every hour, then pushes
// your real numbers here. The watch reads them back. Two keys keep it private:
//   PUSH_KEY  - only the bot knows it (writes session + metrics)
//   WATCH_KEY - only the watch knows it (reads metrics)
//
// Routes:
//   GET  /session?key=PUSH_KEY    -> stored Garmin session token (or empty)
//   PUT  /session?key=PUSH_KEY    -> save Garmin session token (body = text)
//   PUT  /metrics?key=PUSH_KEY    -> save metrics (body = JSON)
//   GET  /data?key=WATCH_KEY      -> read metrics (the watch calls this)
//   GET  /kick?key=WATCH_KEY      -> watch fires this on activity-complete / wake;
//                                    triggers the GitHub bot to fetch fresh data now
//                                    (needs GH_TOKEN + GH_REPO env). Debounced 60s.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;
    const key = url.searchParams.get("key");
    try {
      if (path === "/data" && method === "GET") {
        if (key !== env.WATCH_KEY) return json({ error: "unauthorized" }, 401);
        const m = await env.TOKENS.get("metrics");
        return new Response(m || "{}", {
          headers: { "content-type": "application/json" },
        });
      }

      if (path === "/kick" && method === "GET") {
        if (key !== env.WATCH_KEY) return json({ error: "unauthorized" }, 401);
        // Debounce: ignore repeat kicks within 60s so a chatty event can't
        // hammer the GitHub bot.
        const now = Date.now();
        const last = parseInt((await env.TOKENS.get("lastKick")) || "0", 10);
        if (now - last < 60000) return json({ ok: true, skipped: "debounced" });
        await env.TOKENS.put("lastKick", String(now));
        if (!env.GH_TOKEN || !env.GH_REPO) {
          return json({ error: "GH_TOKEN/GH_REPO not configured" }, 500);
        }
        const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
          method: "POST",
          headers: {
            authorization: `Bearer ${env.GH_TOKEN}`,
            accept: "application/vnd.github+json",
            "user-agent": "fitness-relay-worker",
            "content-type": "application/json",
          },
          body: JSON.stringify({
            event_type: "garmin-refresh",
            client_payload: { why: url.searchParams.get("why") || "event" },
          }),
        });
        return json({ ok: r.ok, status: r.status });
      }

      if (path === "/session") {
        if (key !== env.PUSH_KEY) return json({ error: "unauthorized" }, 401);
        if (method === "GET") {
          const s = await env.TOKENS.get("session");
          return new Response(s || "", { headers: { "content-type": "text/plain" } });
        }
        if (method === "PUT" || method === "POST") {
          await env.TOKENS.put("session", await request.text());
          return json({ ok: true });
        }
      }

      if (path === "/metrics" && (method === "PUT" || method === "POST")) {
        if (key !== env.PUSH_KEY) return json({ error: "unauthorized" }, 401);
        const body = await request.text();
        try {
          JSON.parse(body);
        } catch (e) {
          return json({ error: "body is not valid JSON" }, 400);
        }
        await env.TOKENS.put("metrics", body);
        return json({ ok: true });
      }

      return new Response("Not found", { status: 404 });
    } catch (e) {
      return json({ error: String(e) }, 500);
    }
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}
