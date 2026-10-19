"""Render queue-owned reply blocks beside the matching published body."""
from functools import lru_cache
from html import escape
import json
import re
from .dropqueue import QUEUE_PATH

@lru_cache(maxsize=2)
def _entries(mtime):
    data = json.loads(QUEUE_PATH.read_text(encoding='utf-8'))
    return {(d['date'], d['title']): d for d in data.get('drops', [])}

def entry_for(row):
    try:
        entry = _entries(QUEUE_PATH.stat().st_mtime_ns).get((str(row['drop_date']), row['title']), {})
    except (OSError, ValueError, KeyError):
        return {}
    return entry if entry.get('body', '').strip() == (row['body'] or '').strip() else {}


def images_for(entry):
    return [image for image in entry.get('main_images', [])
            if isinstance(image, dict) and re.fullmatch(r'/thread-img/[A-Za-z0-9_-]+\.jpg', image.get('src', ''))]


def thumbnail(row):
    images = images_for(entry_for(row))
    return images[0]['src'] if images else None


def render(row):
    body = row['body'] or ''
    if not body.strip():
        return ''
    entry = entry_for(row)
    blocks = [('본문', body)]
    blocks += [(f'댓글 {i + 1}', text) for i, text in enumerate(entry.get('replies', [])) if isinstance(text, str) and text.strip()]
    if entry.get('link_comment'):
        blocks.append(('마지막 댓글 · 자료 링크', entry['link_comment']))
    guide = ('<p class=empty>본문과 이미지를 먼저 올리고, 내 글에 댓글을 순서대로 달아주세요. 자료 링크는 마지막 댓글에 넣으면 돼요.</p>' if images_for(entry) else '<p class=empty>본문을 먼저 올리고, 내 글에 댓글을 순서대로 달아주세요. 자료 링크는 마지막 댓글에 넣으면 돼요.</p>') if len(blocks) > 1 else ''
    rendered = []
    for index, (label, text) in enumerate(blocks):
        media = ''
        if index == 0:
            for number, image in enumerate(images_for(entry), 1):
                src = escape(image['src'])
                media += (f'<figure style="margin:16px 0"><img src="{src}" alt="{escape(image.get("alt", "사례 이미지"))}" '
                          f'style="width:100%;height:auto;border-radius:8px" loading=lazy>'
                          f'<figcaption class=empty>원본 시연 · {escape(image.get("credit", ""))}</figcaption>'
                          f'<a class=lk href="{src}" download="{src.rsplit("/", 1)[-1]}">이미지 {number} 저장</a></figure>')
        rendered.append(
            f'<section class=thread-copy-block style="margin:12px 0;padding:8px;border:1px solid #ddd;border-radius:12px">'
            f'<h3>{escape(label)}</h3><pre style="padding:8px;font-family:inherit;font-size:15px;line-height:1.7;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere;word-break:keep-all">{escape(text)}</pre>'
            f'<button type=button class=ghost onclick=fpCopy(this)>{escape(label)} 복사</button>{media}</section>')
    return guide + ''.join(rendered)
