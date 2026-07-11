// Cloudflare Worker: fires the "rates" workflow punctually every hour.
//
// GitHub throttles free scheduled Actions runs (observed gaps 1.5–5h), but a
// workflow_dispatch API call runs immediately. This worker's cron trigger
// (free tier) makes that call on the hour; the workflow's own schedule stays
// as a backup and its `concurrency: booth-rates` group serializes overlaps.
//
// Setup (one time, ~15 min):
//   1. GitHub fine-grained PAT: github.com/settings/personal-access-tokens/new
//      — Resource owner: mrfartman77; Only select repositories: thaicash-data;
//      Repository permissions → Actions: Read and write (nothing else);
//      expiration: 1 year (calendar-remind yourself to rotate).
//   2. dash.cloudflare.com → Workers & Pages → Create → Worker ("Hello World",
//      name it e.g. thaicash-rates-trigger) → Deploy → Edit code → replace
//      with this file → Deploy.
//   3. Worker → Settings → Variables and Secrets → Add → type Secret,
//      name GITHUB_TOKEN, value = the PAT from step 1.
//   4. Worker → Settings → Triggers → Cron Triggers → Add: 3 * * * *
//      (hourly at :03 — offset from the backup schedule's :07).
//   5. Verify: within the hour, the repo's Actions tab shows a "rates" run
//      whose trigger reads "workflow_dispatch", and commits arrive hourly.

const DISPATCH_URL =
  "https://api.github.com/repos/mrfartman77/thaicash-data/actions/workflows/booth-rates.yml/dispatches";

export default {
  async scheduled(event, env, ctx) {
    const resp = await fetch(DISPATCH_URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "thaicash-rates-trigger",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    // 204 = dispatched. Anything else lands in the worker's log stream
    // (Workers & Pages → the worker → Logs) for debugging.
    if (resp.status !== 204) {
      console.error("dispatch failed", resp.status, await resp.text());
    }
  },

  // The worker has no public function — the URL just states what it is.
  // Deliberately NOT a manual trigger: an unauthenticated URL that burns
  // Actions minutes would be a free-tier abuse vector.
  async fetch() {
    return new Response("thaicash rates trigger — cron only", { status: 200 });
  },
};
