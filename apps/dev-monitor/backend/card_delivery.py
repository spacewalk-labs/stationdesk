"""Two-second local spool collector and optional Slack sender."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shlex
import sqlite3
import stat
import sys
import threading
import urllib.error
import urllib.request

from cards import Cards, now


def slack_config():
    path = Path.home()/'.config/stationdesk/slack.env'
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix('export ')
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=',1)
            parts = shlex.split(value, comments=True)
            env[key.strip()] = ' '.join(parts)
    return env


def grace(env):
    return max(0,int(env.get('SLACK_GRACE_SECONDS','300')))


def ensure_spool(root):
    spool = Path(root)/'state/cards-spool'
    for name in ('tmp','new','bad'):
        (spool/name).mkdir(parents=True,exist_ok=True,mode=0o700)
    return spool


def ingest_token(root):
    path = Path(root)/'state/card-ingest.token'
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as handle:
            handle.write(secrets.token_urlsafe(32)+'\n')
    except FileExistsError:
        pass
    return path.read_text().strip()


def scan(cards, spool, delay=300):
    results = {}
    # UUID names carry no chronology; recovery must follow the fault published first.
    for path in sorted((spool/'new').iterdir(), key=lambda p: (p.lstat().st_mtime_ns, p.name)):
        try:
            # Refuse non-files without opening a producer FIFO and stalling the loop.
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError('Expected regular JSON file')
            payload = json.loads(path.read_bytes())
            if payload.get('id')+'.json' != path.name:
                raise ValueError('Filename must match card id')
            status = cards.ingest(payload,delay)
            path.unlink()
        except sqlite3.Error:
            status = 'deferred'
        except (OSError,ValueError,TypeError,AttributeError) as error:
            target = spool/'bad'/(path.name+'.'+secrets.token_hex(4))
            try:
                path.rename(target)
                target.with_suffix(target.suffix+'.reason').write_text(type(error).__name__+'\n')
            except OSError:
                pass
            status = 'bad'
        results[status] = results.get(status,0)+1
    return results


def send_slack(cards, env):
    webhook = env.get('SLACK_WEBHOOK_URL','')
    token,channel = env.get('SLACK_BOT_TOKEN',''),env.get('SLACK_CHANNEL','')
    bot = bool(token and channel)
    if not webhook and not bot:
        return 0
    with cards.db() as db:
        row = db.execute('SELECT * FROM cards WHERE send_next_at<=? AND sent_at IS NULL ORDER BY first_at LIMIT 1',(now(),)).fetchone()
    if row is None:
        return 0
    card = cards.card(row)
    resolved = bool(card['resolved_at'])
    def escape(value):
        return str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    text = ('✅ Resolved: ' if resolved else '🔴 ') + escape(card['title'])
    if card['body']:
        text += '\n'+escape(card['body'])
    text += '\n'+escape(card['source'])+(' ×'+str(card['count']) if card['count']>1 else '')
    payload = {'text':text}
    headers = {'Content-Type':'application/json'}
    if bot:
        method = 'chat.update' if resolved and card['slack_ts'] else 'chat.postMessage'
        url = 'https://slack.com/api/'+method
        payload['channel'] = channel
        if method == 'chat.update':
            payload['ts'] = card['slack_ts']
        headers['Authorization'] = 'Bearer '+token
    else:
        # Incoming webhooks cannot edit a message: emit its resolved follow-up.
        url = webhook
    ok,ts = False,None
    retry = 30*2**min(card['send_attempts'],6)
    request = urllib.request.Request(url,data=json.dumps(payload).encode(),headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=2) as response:
            ok = 200 <= response.status < 300
            if bot:
                result = json.load(response)
                ok = isinstance(result, dict) and result.get('ok') is True
                ts = result.get('ts') if isinstance(result, dict) else None
    except urllib.error.HTTPError as error:
        if error.code == 429:
            try:
                retry = max(retry,int(error.headers.get('Retry-After','30')))
            except ValueError:
                pass
    except (OSError,ValueError):
        pass
    with cards.db() as db:
        current = db.execute('SELECT archived_at,resolved_at FROM cards WHERE card_id=?',(card['card_id'],)).fetchone()
        # A fault reopened the card during its recovery send: keep the new alert scheduled.
        if current is None or resolved and not current['resolved_at']:
            return int(ok)
        # A recovery can arrive while the HTTP request is in flight. Queue its update
        # after a successful initial send, and let a manual archive cancel retries.
        recovered = ok and current['resolved_at'] and not resolved
        next_at = now() if recovered else None if ok or current['archived_at'] else (datetime.now(timezone.utc)+timedelta(seconds=retry)).isoformat()
        db.execute('''UPDATE cards SET sent_at=?,send_next_at=?,send_attempts=send_attempts+1,
           slack_ts=COALESCE(?,slack_ts) WHERE card_id=?''',
           (now() if ok and not recovered else None,next_at,ts,card['card_id']))
    return int(ok)


def start(root):
    cards = Cards(root)
    spool = ensure_spool(root)
    ingest_token(root)
    stop = threading.Event()
    def loop():
        ticks = 0
        while not stop.is_set():
            try:
                try:
                    env = slack_config()
                    delay = grace(env)
                except (OSError, ValueError) as error:
                    print('Slack config: '+type(error).__name__,file=sys.stderr)
                    env, delay = {}, 300
                scan(cards,spool,delay)
                send_slack(cards,env)
                if ticks%1800 == 0:
                    cards.sweep()
                ticks += 1
            except (OSError,ValueError,sqlite3.Error) as error:
                # Never print payloads, prompts, webhook URLs or credentials.
                print('card collector: '+type(error).__name__,file=sys.stderr)
            stop.wait(2)
    threading.Thread(target=loop,name='card-collector',daemon=True).start()
    return stop
