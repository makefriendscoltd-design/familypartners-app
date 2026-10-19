"""Render queue-owned reply blocks beside the matching published body."""
from functools import lru_cache
from html import escape
import json
from .dropqueue import QUEUE_PATH

@lru_cache(maxsize=2)
def _entries(mtime):
    data = json.loads(QUEUE_PATH.read_text(encoding='utf-8'))
    return {(d['date'], d['title']): d for d in data.get('drops', [])}

def render(row):
    body = row['body'] or ''
    if not body.strip():
        return ''
    blocks = [('본문', body)]
    try:
        entry = _entries(QUEUE_PATH.stat().st_mtime_ns).get((str(row['drop_date']), row['title']), {})
    except (OSError, ValueError, KeyError):
        entry = {}
    # Admin-edited bodies must not silently acquire unrelated queue replies.
    if entry.get('body', '').strip() == body.strip():
        blocks += [(f'댓글 {i + 1}', text) for i, text in enumerate(entry.get('replies', [])) if isinstance(text, str) and text.strip()]
        if entry.get('link_comment'):
            blocks.append(('마지막 댓글 · 자료 링크', entry['link_comment']))
    guide = ('<p class=empty>본문을 먼저 올리고, 내 글에 댓글을 순서대로 달아주세요. 자료 링크는 마지막 댓글에 넣으면 돼요.</p>' if len(blocks) > 1 else '')
    return guide + ''.join(
        f'<section class=thread-copy-block style="margin:12px 0;padding:14px;border:1px solid #ddd;border-radius:12px">'
        f'<h3>{escape(label)}</h3><pre>{escape(text)}</pre>'
        f'<button type=button class=ghost onclick=fpCopy(this)>{escape(label)} 복사</button></section>'
        for label, text in blocks)
