// 사이트 화면(site/)은 정적 파일로 주고, 아래 세 주소만 이 코드가 처리한다.
//   POST /api/crawl      "지금 새로 모으기" 버튼 → GitHub Actions 수집 실행을 요청
//   GET  /api/status     마지막 수집 실행 상태
//   GET  /data/items.json 공개 레포에 커밋된 최신 공고 데이터
// 수집 자체는 GitHub Actions(공개 레포라 무료)가 하고, 여기서는 요청만 넘긴다.
const REPO = "meaninglens/support-board";
const WORKFLOW = "collect.yml";
const COOLDOWN_MIN = 10;

function gh(env, path, init = {}) {
  const headers = { "accept": "application/vnd.github+json", "user-agent": "meaning-support-board", "x-github-api-version": "2022-11-28", ...(init.headers || {}) };
  if (env.GITHUB_TOKEN) headers.authorization = `Bearer ${env.GITHUB_TOKEN}`;
  return fetch(`https://api.github.com/repos/${REPO}${path}`, { ...init, headers });
}
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });

async function lastRun(env) {
  const r = await gh(env, `/actions/workflows/${WORKFLOW}/runs?per_page=1`);
  if (!r.ok) return null;
  const run = (await r.json()).workflow_runs?.[0];
  return run ? { status: run.status, conclusion: run.conclusion, startedAt: run.run_started_at || run.created_at, updatedAt: run.updated_at, url: run.html_url } : null;
}

async function crawl(env) {
  if (!env.GITHUB_TOKEN) return json({ ok: false, message: "버튼 연결이 아직 안 됐어요. 창율에게 알려 주세요." }, 503);
  const run = await lastRun(env);
  if (run && run.status !== "completed") return json({ ok: true, running: true, message: "이미 모으는 중이에요.", run });
  if (run) {
    const mins = (Date.now() - Date.parse(run.startedAt)) / 60000;
    if (mins < COOLDOWN_MIN) return json({ ok: false, message: `${Math.ceil(mins)}분 전에 모았어요. ${Math.ceil(COOLDOWN_MIN - mins)}분 뒤에 다시 눌러 주세요.`, run }, 429);
  }
  const r = await gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, { method: "POST", body: JSON.stringify({ ref: "main" }), headers: { "content-type": "application/json" } });
  if (r.status !== 204) return json({ ok: false, message: "수집을 시작하지 못했어요. 잠시 뒤 다시 눌러 주세요." }, 502);
  return json({ ok: true, running: true, message: "모으기 시작했어요. 보통 3~5분 걸려요." }, 202);
}

async function items(env, ctx) {
  let ref = "main";
  if (env.GITHUB_TOKEN) {
    const r = await gh(env, "/commits/main", { headers: { accept: "application/vnd.github.sha" } });
    if (r.ok) ref = (await r.text()).trim();
  }
  const src = `https://raw.githubusercontent.com/${REPO}/${ref}/site/data/items.json`;
  const cache = caches.default;
  let res = await cache.match(src);
  if (!res) {
    const up = await fetch(src);
    if (!up.ok) return json({ message: "공고 데이터를 가져오지 못했어요." }, 502);
    res = new Response(up.body, { headers: { "content-type": "application/json; charset=utf-8", "cache-control": ref === "main" ? "max-age=60" : "max-age=86400" } });
    ctx.waitUntil(cache.put(src, res.clone()));
  }
  const out = new Response(res.body, res);
  out.headers.set("cache-control", "no-store");
  return out;
}

export default {
  async fetch(req, env, ctx) {
    const { pathname } = new URL(req.url);
    if (pathname === "/api/crawl" && req.method === "POST") return crawl(env);
    if (pathname === "/api/status") return json({ run: await lastRun(env), connected: !!env.GITHUB_TOKEN });
    if (pathname === "/data/items.json") return items(env, ctx);
    return env.ASSETS.fetch(req);
  }
};
