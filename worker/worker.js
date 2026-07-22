/**
 * Cloudflare Worker — 简知分销日报定时触发保底
 * 
 * 在每个定时点（UTC 1/3/7/11/14/16 的第2分钟）通过 GitHub API 触发 workflow_dispatch，
 * 防止 GitHub Actions 自带 cron 偶尔跳过导致的漏跑。
 * 
 * 与 GitHub 自带 cron（第0分钟）错开2分钟，互为备份。
 */

export default {
  // 定时触发入口
  async scheduled(event, env, ctx) {
    const token = env.GITHUB_TOKEN;
    if (!token) {
      console.error('GITHUB_TOKEN not set');
      return;
    }

    const url = 'https://api.github.com/repos/JNC121314/jianzhi-dashboard/actions/workflows/update-dashboard.yml/dispatches';

    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Authorization': `token ${token}`,
          'Accept': 'application/vnd.github.v3+json',
          'User-Agent': 'jianzhi-cron-worker/1.0'
        },
        body: JSON.stringify({ ref: 'main' })
      });

      console.log(`[${new Date().toISOString()}] Workflow dispatch: HTTP ${resp.status}`);

      if (!resp.ok) {
        const text = await resp.text();
        console.error(`Failed: ${resp.status} — ${text.substring(0, 200)}`);
      }
    } catch (err) {
      console.error(`Error: ${err.message}`);
    }
  },

  // HTTP 入口（用于手动测试和健康检查）
  async fetch(request, env, ctx) {
    return new Response('OK — jianzhi-cron-worker', { status: 200 });
  }
};
