// 쉬운 소식 글 이미지: 바깥 원문 화면 캡처(shot) 또는 프롬프트 표 카드(card).
// node deploy/easy_news_images.mjs <jobs.json>
// jobs.json = {"playwright": "<playwright-core index.mjs>", "jobs": [{kind:'shot',url,out} | {kind:'card',card:{title,sub,rows:[[label,"줄1|줄2"]]},out}]}
// 결과는 job 마다 한 줄 `IMG {"out","ok","kind","error"}`. 읽기 전용(페이지에 입력·로그인하지 않음).
import fs from 'node:fs';
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const { chromium } = await import(spec.playwright);
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
// 2026-11-12 사용자 요청: 세로 4:5 대신 가로 16:9. 카드는 1920x1080 고정 2단, 넘치면 글자를 줄여 맞춤.
const W = 1920, H = 1080;
const cardHtml = c => `<!doctype html><meta charset=utf-8><style>
*{margin:0;box-sizing:border-box}html{font-size:var(--s,1px)}
body{width:${W}px;height:${H}px;background:#f2f2f0;font-family:"Pretendard","Apple SD Gothic Neo",sans-serif;padding:56px 72px;overflow:hidden}
.card{background:#fff;border-radius:32px;padding:56rem 64rem;height:100%;display:flex;flex-direction:column}
h1{font-size:64rem;font-weight:800;letter-spacing:-1.5px;color:#111;text-align:center}
.sub{font-size:32rem;color:#888;text-align:center;margin:14rem 0 36rem}
.grid{display:grid;grid-template-columns:1fr 1fr;column-gap:64rem;grid-auto-flow:column;flex:1;align-content:center}
.row{display:flex;gap:24rem;padding:26rem 0;border-top:2px solid #eee}
.n{font-size:36rem;font-weight:700;color:#bbb;width:56rem;flex:none}
.k{font-size:38rem;font-weight:700;color:#111;margin-bottom:8rem}
.v{white-space:nowrap;font-size:31rem;line-height:1.5;color:#555;word-break:keep-all}
</style><div class=card><h1>${esc(c.title)}</h1><div class=sub>${esc(c.sub)}</div><div class=grid style="grid-template-rows:repeat(${Math.ceil(c.rows.length / 2)},auto)">${c.rows.map((r, i) => `<div class=row><div class=n>${String(i + 1).padStart(2, '0')}</div><div><div class=k>${esc(r[0])}</div><div class=v>${esc(r[1]).split('|').join('<br>')}</div></div></div>`).join('')}</div></div>`;
// 글자를 최대 1.8배로 키운 뒤 카드 밖으로 나가지 않을 때까지 0.04씩 줄인다(최소 0.6).
const fitCard = () => {
  const card = document.querySelector('.card');
  const over = () => card.scrollHeight > card.clientHeight + 1 || [...document.querySelectorAll('.row')].some(r => r.scrollWidth > r.clientWidth + 1);
  let s = 1.8;
  document.documentElement.style.setProperty('--s', s + 'px');
  while (over() && s > 0.6) { s -= 0.04; document.documentElement.style.setProperty('--s', s + 'px'); }
  return s;
};
// 원문 캡처: 960x540 화면을 2배로 찍어 1920x1080. 폰에서 글씨가 읽히도록 좁은 화면으로 확대.
const SHOT_HIDE = ['header.AppHeader', '.AppHeader', 'header[role=banner]', '.js-header-wrapper', '.notion-topbar-mobile', '[class*="signup"]', '[class*="SignUp"]'];
const CONSENT = ['Accept all cookies', 'Accept all', 'Accept', 'Allow all', 'I agree', 'Agree', 'Got it', 'OK', '모두 허용', '모두 승인', '모두 수락', '전체 허용', '동의', '확인'];
const BLOCKED = /(sign in to continue|log in to continue|access denied|verify you are human|just a moment|page not found|404 not found)/i;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  for (const job of spec.jobs) {
    const res = { out: job.out, kind: job.kind, ok: false };
    try {
      if (job.kind === 'card') {
        const p = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
        await p.setContent(cardHtml(job.card));
        res.scale = await p.evaluate(fitCard);
        await p.screenshot({ path: job.out });
        await p.close();
        res.ok = true;
      } else {
        const ctx = await browser.newContext({ viewport: { width: W / 2, height: H / 2 }, deviceScaleFactor: 2, locale: 'ko-KR' });
        const p = await ctx.newPage();
        const resp = await p.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
        try { await p.waitForLoadState('networkidle', { timeout: 15000 }); } catch {}
        await p.waitForTimeout(2000);
        for (const name of CONSENT) {
          try {
            const b = p.getByRole('button', { name, exact: true }).first();
            if (await b.isVisible({ timeout: 300 })) { await b.click({ timeout: 2000 }); await p.waitForTimeout(600); break; }
          } catch {}
        }
        // Remaining fixed overlays (cookie bars, sign-up nags) are hidden, not clicked.
        await p.evaluate(() => {
          for (const el of document.querySelectorAll('body *')) {
            const s = getComputedStyle(el);
            if ((s.position === 'fixed' || s.position === 'sticky') && /cookie|consent|gdpr|banner|modal|popup|overlay|dialog|signup|sign-up/i.test(el.id + ' ' + el.className + ' ' + (el.getAttribute('role') || ''))) el.style.display = 'none';
            // 상단 가입 유도 띠(노션 "sign up to start building")도 숨긴다.
            if (/sign up|log ?in|가입|로그인/i.test(el.innerText || '') && el.getBoundingClientRect().top < 80 && el.getBoundingClientRect().height < 80 && el.children.length < 6 && (el.innerText || '').length < 120) el.style.visibility = 'hidden';
          }
        });
        await p.evaluate(sel => { for (const q of sel) for (const el of document.querySelectorAll(q)) el.style.display = 'none'; window.scrollTo(0, 0); }, SHOT_HIDE);
        await p.waitForTimeout(400);
        const text = await p.evaluate(() => document.body ? document.body.innerText.slice(0, 3000) : '');
        if (resp && resp.status() >= 400) throw new Error('http_' + resp.status());
        if (BLOCKED.test(text) || text.trim().length < 80) throw new Error('blocked_or_empty_page');
        await p.screenshot({ path: job.out });
        await ctx.close();
        res.ok = true;
      }
    } catch (e) { res.error = String(e.message || e).slice(0, 200); }
    console.log('IMG ' + JSON.stringify(res));
  }
} finally { await browser.close(); }
