// 쉬운 소식 글 이미지: 바깥 원문 화면 캡처(shot) 또는 프롬프트 표 카드(card).
// node deploy/easy_news_images.mjs <jobs.json>
// jobs.json = {"playwright": "<playwright-core index.mjs>", "jobs": [{kind:'shot',url,out} | {kind:'card',card:{title,sub,rows:[[label,"줄1|줄2"]]},out}]}
// 결과는 job 마다 한 줄 `IMG {"out","ok","kind","error"}`. 읽기 전용(페이지에 입력·로그인하지 않음).
import fs from 'node:fs';
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const { chromium } = await import(spec.playwright);
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const cardHtml = c => `<!doctype html><meta charset=utf-8><style>
*{margin:0;box-sizing:border-box}body{width:1080px;background:#f2f2f0;font-family:"Pretendard","Apple SD Gothic Neo",sans-serif;padding:64px 60px}
.card{background:#fff;border-radius:28px;padding:64px 56px 56px}
h1{font-size:54px;font-weight:800;letter-spacing:-1.5px;color:#111;text-align:center}
.sub{font-size:28px;color:#888;text-align:center;margin:14px 0 44px}
.row{display:flex;gap:28px;padding:30px 0;border-top:2px solid #eee}
.n{font-size:34px;font-weight:700;color:#bbb;width:56px;flex:none}
.k{font-size:34px;font-weight:700;color:#111;margin-bottom:10px}
.v{white-space:nowrap;font-size:29px;line-height:1.5;color:#555;word-break:keep-all}
</style><div class=card><h1>${esc(c.title)}</h1><div class=sub>${esc(c.sub)}</div>${c.rows.map((r, i) => `<div class=row><div class=n>${String(i + 1).padStart(2, '0')}</div><div><div class=k>${esc(r[0])}</div><div class=v>${esc(r[1]).split('|').join('<br>')}</div></div></div>`).join('')}</div>`;
const CONSENT = ['Accept all cookies', 'Accept all', 'Accept', 'Allow all', 'I agree', 'Agree', 'Got it', 'OK', '모두 허용', '동의', '확인'];
const BLOCKED = /(sign in to continue|log in to continue|access denied|verify you are human|just a moment|page not found|404 not found)/i;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  for (const job of spec.jobs) {
    const res = { out: job.out, kind: job.kind, ok: false };
    try {
      if (job.kind === 'card') {
        const p = await browser.newPage({ viewport: { width: 1080, height: 600 }, deviceScaleFactor: 1 });
        await p.setContent(cardHtml(job.card));
        await p.locator('body').screenshot({ path: job.out });
        await p.close();
        res.ok = true;
      } else {
        const ctx = await browser.newContext({ viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1, locale: 'ko-KR' });
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
            if ((s.position === 'fixed' || s.position === 'sticky') && /cookie|consent|gdpr|banner|modal|popup|overlay|dialog/i.test(el.id + ' ' + el.className + ' ' + (el.getAttribute('role') || ''))) el.style.display = 'none';
          }
        });
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
