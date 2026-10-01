"""Read-only activity counts from valid registered SNS links, not measured reach."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from urllib.parse import urlsplit

from . import core

_ID = r'[A-Za-z0-9_-]+'


def post_identity(url: str):
    """Identify a supported post/share URL without contacting the platform.

    Share aliases cannot be resolved to an underlying post locally. They remain
    separate registered-link identities; the UI deliberately reports links.
    """
    try:
        parsed = urlsplit(str(url).strip())
        host = (parsed.hostname or '').lower()
        if parsed.scheme.lower() not in ('http', 'https') or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 80, 443):
            return None
    except (TypeError, ValueError):
        return None
    path = parsed.path.rstrip('/')
    if host == 'instagram.com' or host.endswith('.instagram.com'):
        match = re.fullmatch(r'/(?:p|reel|reels|tv)/(' + _ID + ')', path)
        return ('instagram', 'post:' + match[1]) if match else None
    if host in ('threads.com', 'threads.net') or host.endswith(('.threads.com', '.threads.net')):
        match = re.fullmatch(r'/(?:@[^/]+/post|t)/(' + _ID + ')', path)
        if match:
            return ('threads', 'post:' + match[1])
        match = re.fullmatch(r'/share/(' + _ID + ')', path)
        if match:
            return ('threads', 'share:' + match[1])
    return None


def registration_time(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # Existing core.now_iso stores local KST without an offset.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=core.KST)
        return stamp.astimezone(core.KST)
    except (TypeError, ValueError):
        return None


def summary(conn, pid, as_of: date | None = None, limit: int = 5):
    as_of = as_of or core.today()
    start = as_of - timedelta(days=as_of.weekday())
    end = start + timedelta(days=6)
    partners = {r['id']: dict(r) for r in conn.execute(
        'SELECT id,name,handle,status FROM partners').fetchall()}
    rows = conn.execute(
        'SELECT id,partner_id,post_url,submitted_at FROM submissions WHERE valid=1').fetchall()
    candidates = []
    for row in rows:
        identity = post_identity(row['post_url'])
        stamp = registration_time(row['submitted_at'])
        if identity and stamp and stamp.date() <= as_of and row['partner_id'] in partners:
            candidates.append((stamp, row['id'], identity, row['partner_id']))
    candidates.sort(key=lambda x: (x[0], x[1]))
    seen = set()
    channels = {'threads': 0, 'instagram': 0}
    weekly = {}
    totals = {}
    my_total = 0
    for stamp, _, identity, owner in candidates:
        if identity in seen:
            continue
        seen.add(identity)
        channels[identity[0]] += 1
        totals[owner] = totals.get(owner, 0) + 1
        if owner == pid:
            my_total += 1
        if start <= stamp.date() <= as_of and partners[owner]['status'] == 'active':
            weekly[owner] = weekly.get(owner, 0) + 1
    ranked = []
    last_count = None
    rank = 0
    for index, (owner, count) in enumerate(sorted(weekly.items(), key=lambda x: (-x[1], x[0])), 1):
        if count != last_count:
            rank = index
        ranked.append({'partner_id': owner, 'name': partners[owner]['name'],
                       'handle': partners[owner]['handle'], 'count': count, 'rank': rank,
                       'total': totals.get(owner, 0)})
        last_count = count
    mine = next((r for r in ranked if r['partner_id'] == pid), None)
    unranked = [{'partner_id': owner, 'name': p['name'], 'handle': p['handle'],
                 'count': 0, 'rank': None, 'total': totals.get(owner, 0)}
                for owner, p in partners.items() if p['status'] == 'active' and owner not in weekly]
    return {'week_start': start.isoformat(), 'week_end': end.isoformat(),
            'total_posts': len(seen), 'channel_totals': channels,
            'weekly_posts': sum(weekly.values()), 'participants': len(weekly),
            'leaders': ranked[:max(0, limit)], 'all_active': ranked + unranked,
            'me': {'count': weekly.get(pid, 0), 'rank': mine['rank'] if mine else None,
                   'total': my_total}}
