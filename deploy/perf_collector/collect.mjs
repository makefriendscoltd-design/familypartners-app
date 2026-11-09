// 파트너 스레드 글의 공개 반응 수를 모아 서버에 올린다. (이 맥 launchd, 하루 1회)
// 서버: GET /op/perf/targets → 글마다 브라우저로 열어 GraphQL 응답에서 숫자 추출 → POST /op/perf/report
// 조회수는 스레드가 작성자에게만 보여 수집 불가. 실패 글은 error 로 보고하고 이전 숫자는 서버가 보존.
// 사용: node collect.mjs [--dry-run] [--limit N]
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import os from 'node:os';

const args = process.argv.slice(2);
const DRY = args.includes('--dry-run');
const LIMIT = Number(args[args.indexOf('--limit') + 1]) || 0;
const BASE = process.env.FP_BASE_URL || 'https://familypartners.aimax.ai.kr';
const TOKEN = fs.readFileSync(process.env.FP_SYNC_TOKEN_FILE ||
  `${os.homedir()}/.local/share/familypartners-video-sync/token`, 'utf8').trim();
const auth = { Authorization: `Bearer ${TOKEN}` };

async function api(method, path, body) {
  const r = await fetch(BASE + path, { method, headers: { ...auth, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

export function findPost(obj, code) {
  let hit = null;
  const scan = (o) => {
    if (!o || typeof o !== 'object' || hit) return;
    if (o.code === code && 'like_count' in o) { hit = o; return; }
    for (const v of Object.values(o)) scan(v);
  };
  scan(obj);
  if (!hit) return null;
  const i = hit.text_post_app_info || {};
  return { likes: hit.like_count ?? 0, replies: i.direct_reply_count ?? 0, reposts: i.repost_count ?? 0,
           quotes: i.quote_count ?? 0, shares: i.reshare_count ?? 0 };
}

async function measure(page, url) {
  const m = url.match(/\/post\/([A-Za-z0-9_-]+)/);
  if (!m) return { error: 'not_a_post_url' };
  const code = m[1];
  let found = null;
  const onResp = async (r) => {
    try {
      if (found || !/graphql|\/api\//.test(r.url())) return;
      const t = await r.text();
      if (t.includes(code) && t.includes('like_count')) found = found || findPost(JSON.parse(t), code);
    } catch {}
  };
  page.on('response', onResp);
  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
    if (!found) {
      for (const s of await page.$$eval('script[type="application/json"]', xs => xs.map(x => x.textContent))) {
        if (s.includes(code) && s.includes('like_count')) { try { found = findPost(JSON.parse(s), code); } catch {} }
        if (found) break;
      }
    }
  } catch (e) {
    return { error: `load:${String(e.message).slice(0, 80)}` };
  } finally {
    page.off('response', onResp);
  }
  if (found) return found;
  const txt = (await page.evaluate(() => document.body.innerText.slice(0, 400)).catch(() => '')) || '';
  return { error: /로그인|log in/i.test(txt) && txt.length < 200 ? 'login_wall' : 'not_found_or_deleted' };
}

const { targets } = await api('GET', '/op/perf/targets');
const list = LIMIT ? targets.slice(0, LIMIT) : targets;
console.log(`${new Date().toISOString()} targets=${targets.length} measuring=${list.length}${DRY ? ' (dry-run)' : ''}`);
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ locale: 'ko-KR' });
const results = [];
for (const t of list) {
  const r = { id: t.id, ...(await measure(page, t.url)) };
  results.push(r);
  if (DRY) console.log(t.id, t.url, JSON.stringify(r));
  await new Promise(res => setTimeout(res, 1500 + Math.random() * 1500));  // 천천히: 차단 회피가 아니라 부하 방지
}
await browser.close();
const ok = results.filter(r => !r.error).length;
const errs = {};
for (const r of results) if (r.error) errs[r.error] = (errs[r.error] || 0) + 1;
console.log(`measured ok=${ok} failed=${results.length - ok} ${JSON.stringify(errs)}`);
if (!DRY && results.length) console.log('server', JSON.stringify(await api('POST', '/op/perf/report', { results })));
if (list.length && ok === 0) process.exit(3);   // 전부 실패 = 수집 경로가 죽음 → loopguard 가 알아채게
