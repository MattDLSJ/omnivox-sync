# Report relay

Filing a GitHub issue needs a GitHub identity. A token cannot ship in a public
repo, so the fallback was a prefilled link somebody had to click, and nobody
clicks it. This holds the token instead, so the tool can post a report with no
credentials and the reporter never has to do anything.

## Deploy it once

```bash
cd relay
npx wrangler kv namespace create RATE     # then paste the id into wrangler.toml
npx wrangler deploy
npx wrangler secret put GITHUB_TOKEN
```

For the token, use a **fine-grained** personal access token with
`Issues: read and write` on that one repository and nothing else. If it ever
leaks, the worst anybody can do is file issues, which is what the endpoint
does for them anyway.

`wrangler deploy` prints the URL. Put it in the project:

```bash
git config report.relay https://omnivox-sync-reports.<your-subdomain>.workers.dev
```

## What it does about abuse

The URL is public and unauthenticated, which is the point and also the risk:
anyone who finds it can file issues. That is bounded rather than prevented,
because requiring a credential is precisely what this removes.

- one report per IP per hour
- 64 KB of body
- the payload must look like a field report
- every issue is labelled `via relay`, so a flood is one filtered query to close

If it is abused, delete the Worker. The tool falls back to `gh`, and then to
leaving the report on disk with instructions, so nobody loses a report.
