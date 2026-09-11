/**
 * Report relay: turns an anonymous POST into a GitHub issue.
 *
 * The problem it exists for. Filing an issue needs a GitHub identity, and a
 * token cannot ship in a public repo, so the fallback was a prefilled link the
 * reporter had to click. Nobody clicks it. A report that depends on somebody
 * going out of their way is a report that does not arrive, and the whole
 * feedback loop was resting on that click.
 *
 * So this holds the token instead, on infrastructure the maintainer owns, and
 * the tool posts to it with no credentials at all. The reporter never learns
 * it happened.
 *
 * Deploy:
 *   cd relay && npx wrangler deploy
 *   npx wrangler secret put GITHUB_TOKEN     # a fine-grained PAT, issues:write
 *                                            # on ONE repo, nothing else
 *
 * The URL is public and unauthenticated, which is the point and also the risk:
 * anyone who finds it can file issues on the repo. That is bounded rather than
 * prevented, because requiring a credential is exactly what this removes:
 *
 *   - one report per IP per hour, held in KV
 *   - 64 KB of body, refused above that
 *   - the payload has to look like a field report
 *   - every issue is labelled so a flood can be filtered and closed
 *
 * If it is ever abused, rotate the URL: the tool falls back to gh and then to
 * writing the file, so nobody loses a report while it is down.
 */

const MAX_BODY = 64 * 1024;
const WINDOW_SECONDS = 3600;

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return json({ error: "POST a report here" }, 405);
    }

    const raw = await request.text();
    if (raw.length > MAX_BODY) {
      return json({ error: "report too large" }, 413);
    }

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return json({ error: "expected JSON" }, 400);
    }

    const title = String(payload.title || "").slice(0, 200);
    const body = String(payload.body || "");
    // Shape check, not a security control: it keeps a stray crawler's POST out
    // of the issue tracker, and costs a real report nothing.
    if (!title.startsWith("[field report]") || body.length < 40) {
      return json({ error: "that is not a field report" }, 400);
    }

    const who = request.headers.get("CF-Connecting-IP") || "unknown";
    if (env.RATE) {
      const key = `ip:${who}`;
      if (await env.RATE.get(key)) {
        // Not an error the reporter should see as a failure: their report is
        // still on disk and the tool says so.
        return json({ ok: false, reason: "rate limited, try later" }, 429);
      }
      await env.RATE.put(key, "1", { expirationTtl: WINDOW_SECONDS });
    }

    const response = await fetch(
      `https://api.github.com/repos/${env.REPO}/issues`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "omnivox-sync-report-relay",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title,
          body: body.slice(0, MAX_BODY),
          labels: ["field report", "via relay"],
        }),
      }
    );

    if (!response.ok) {
      const detail = (await response.text()).slice(0, 300);
      return json({ ok: false, status: response.status, detail }, 502);
    }
    const issue = await response.json();
    return json({ ok: true, url: issue.html_url, number: issue.number });
  },
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
