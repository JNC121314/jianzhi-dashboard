export default {
  async scheduled(event, env, ctx) {
    const now = new Date();
    const timeStr = now.toISOString().replace('T', ' ').slice(0, 19);
    console.log(`[${timeStr}] Worker triggered, checking GitHub Actions...`);

    // Check if a recent run already succeeded (within last 10 minutes)
    try {
      const runsResp = await fetch(
        'https://api.github.com/repos/JNC121314/jianzhi-dashboard/actions/workflows/update-dashboard.yml/runs?per_page=2&event=schedule&status=completed',
        {
          headers: {
            'Authorization': `token ${env.GITHUB_TOKEN}`,
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'cf-worker-dashboard-trigger/1.0'
          }
        }
      );
      const runsData = await runsResp.json();
      const recentRuns = runsData.workflow_runs || [];

      // If a run completed in last 10 minutes with success, skip
      const tenMinAgo = new Date(now.getTime() - 10 * 60 * 1000);
      for (const run of recentRuns) {
        const runDate = new Date(run.created_at);
        if (runDate > tenMinAgo && run.conclusion === 'success') {
          console.log(`[${timeStr}] Recent run #${run.run_number} already succeeded at ${run.created_at}, skipping.`);
          return;
        }
      }
    } catch (e) {
      console.log(`[${timeStr}] Failed to check recent runs: ${e.message}, proceeding anyway.`);
    }

    // Trigger workflow_dispatch
    try {
      const dispatchResp = await fetch(
        'https://api.github.com/repos/JNC121314/jianzhi-dashboard/actions/workflows/update-dashboard.yml/dispatches',
        {
          method: 'POST',
          headers: {
            'Authorization': `token ${env.GITHUB_TOKEN}`,
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'cf-worker-dashboard-trigger/1.0',
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ ref: 'main' })
        }
      );
      console.log(`[${timeStr}] Workflow dispatch status: ${dispatchResp.status}`);
    } catch (e) {
      console.log(`[${timeStr}] Failed to trigger workflow: ${e.message}`);
    }
  },

  async fetch(request, env, ctx) {
    // Manual trigger endpoint: GET /trigger
    const url = new URL(request.url);
    if (url.pathname === '/trigger') {
      const dispatchResp = await fetch(
        'https://api.github.com/repos/JNC121314/jianzhi-dashboard/actions/workflows/update-dashboard.yml/dispatches',
        {
          method: 'POST',
          headers: {
            'Authorization': `token ${env.GITHUB_TOKEN}`,
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'cf-worker-dashboard-trigger/1.0',
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ ref: 'main' })
        }
      );
      return new Response(JSON.stringify({ ok: true, status: dispatchResp.status }), {
        headers: { 'Content-Type': 'application/json' }
      });
    }
    return new Response('jianzhi-dashboard cron trigger worker');
  }
};
