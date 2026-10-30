"""Local cards, receipts and delivery state; no company integrations."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import tomllib
import urllib.request
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected card object')
    p = dict(payload)
    for key in ('id', 'group', 'source'):
        if not isinstance(p.get(key), str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', p[key]):
            raise ValueError('Invalid ' + key)
    if p.get('level') not in ('normal', 'urgent'):
        raise ValueError('Invalid level')
    if not isinstance(p.get('title'), str) or not p['title'].strip():
        raise ValueError('Title required')
    for key in ('body', 'detail'):
        if p.get(key) is not None and not isinstance(p[key], str):
            raise ValueError('Invalid ' + key)
    if not isinstance(p.get('peek', False), bool):
        raise ValueError('Invalid peek')
    if p.get('resolves') is True:
        p['resolves'] = p['group']
    elif p.get('resolves') is False:
        p['resolves'] = None
    if p.get('resolves') is not None and not isinstance(p['resolves'], str):
        raise ValueError('Invalid resolves')
    if p.get('link') is not None:
        if isinstance(p['link'], dict):
            p['link'] = p['link'].get('url')
        from urllib.parse import urlsplit
        link = urlsplit(p['link'])
        if link.scheme not in ('http', 'https') or not link.hostname:
            raise ValueError('Link requires http(s) URL')
    if p.get('created_at'):
        date = datetime.fromisoformat(p['created_at'].replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError('created_at needs timezone')
    run = p.get('run')
    if run is not None:
        if not isinstance(run, dict) or any(not isinstance(run.get(k), str) or not run[k].strip() or '\0' in run[k] for k in ('cwd', 'prompt')):
            raise ValueError('run requires cwd and prompt')
        params = run.get('params', [])
        if not isinstance(params, list):
            raise ValueError('run params must be declarations')
        seen = set()
        for entry in params:
            if not isinstance(entry, dict) or not isinstance(entry.get('key'), str) or not re.fullmatch(r'[A-Za-z0-9._:-]+', entry['key']) or entry['key'] in seen or not isinstance(entry.get('label'), str):
                raise ValueError('Invalid run param')
            seen.add(entry['key'])
            choices = entry.get('choices')
            if choices is not None and (not isinstance(choices, list) or not choices or any(not isinstance(v, str) for v in choices)):
                raise ValueError('Invalid run choices')
            if 'default' in entry and (not isinstance(entry['default'], str) or choices is not None and entry['default'] not in choices):
                raise ValueError('Invalid run default')
    return p


class Cards:
    def __init__(self, root):
        self.root = Path(root)
        self.state = self.root / 'state'
        self.state.mkdir(parents=True, exist_ok=True)
        self.path = self.state / 'cards.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, received_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cards(card_id TEXT PRIMARY KEY, "group" TEXT NOT NULL,
              source TEXT,level TEXT,title TEXT,body TEXT,link TEXT,run TEXT,peek INTEGER,detail TEXT,
              count INTEGER,created_at TEXT,first_at TEXT,last_at TEXT,read_at TEXT,archived_at TEXT,
              ran_at TEXT,ran_input TEXT,ran_window TEXT,resolved_at TEXT,sent_at TEXT,send_next_at TEXT,
              send_attempts INTEGER DEFAULT 0,slack_ts TEXT);
            CREATE INDEX IF NOT EXISTS cards_group ON cards("group",archived_at);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, payload, grace=300):
        p = validate(payload)
        at = now()
        due = (datetime.now(timezone.utc) + timedelta(seconds=grace)).isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM receipts WHERE id=?', (p['id'],)).fetchone():
                return 'duplicate'
            if p.get('resolves'):
                # Recovery always closes the fault, even without outbound Slack configured.
                db.execute('''UPDATE cards SET archived_at=?, resolved_at=?, last_at=?,
                  send_next_at=CASE WHEN sent_at IS NOT NULL THEN ? ELSE NULL END,
                  sent_at=NULL,send_attempts=0 WHERE ("group"=? OR card_id=?) AND archived_at IS NULL''',
                  (at,at,at,at,p['resolves'],p['resolves']))
                status = 'resolved'
            else:
                # A fault after its recovery reopens the resolved card: a flapping unit
                # stays one card with a growing count. A card the owner archived stays closed.
                candidates = db.execute('''SELECT * FROM cards WHERE "group"=? AND (archived_at IS NULL OR resolved_at IS NOT NULL)
                  ORDER BY archived_at IS NOT NULL,last_at DESC''', (p['group'],))
                old = next((card for card in candidates if (json.loads(card['run']) if card['run'] else None) == p.get('run') and card['link'] == p.get('link')), None)
                run = json.dumps(p.get('run')) if p.get('run') is not None else None
                if old:
                    reopened = old['archived_at'] is not None
                    changed = reopened or old['title'] != p['title'] or old['body'] != p.get('body','')
                    level = 'urgent' if 'urgent' in (old['level'],p['level']) else 'normal'
                    if reopened:
                        # A new occurrence: alert again and offer the run action again.
                        sent_at, attempts, next_at, ran = None, 0, due if level == 'urgent' else None, (None,)*3
                    else:
                        sent_at, attempts, ran = old['sent_at'], old['send_attempts'], (old['ran_at'],old['ran_input'],old['ran_window'])
                        next_at = due if old['level'] == 'normal' and level == 'urgent' and not old['sent_at'] else old['send_next_at']
                    db.execute('''UPDATE cards SET count=count+1,last_at=?,level=?,title=?,body=?,link=?,run=?,
                       peek=MAX(peek,?),detail=COALESCE(?,detail),read_at=?,send_next_at=?,
                       archived_at=NULL,resolved_at=NULL,sent_at=?,send_attempts=?,
                       ran_at=?,ran_input=?,ran_window=? WHERE card_id=?''',
                       (at,level,p['title'],p.get('body',''),p.get('link'),run,p.get('peek',False),p.get('detail'),None if changed else old['read_at'],next_at,sent_at,attempts,*ran,old['card_id']))
                    status = 'reopened' if reopened else 'coalesced'
                else:
                    db.execute('''INSERT INTO cards(card_id,"group",source,level,title,body,link,run,peek,detail,count,created_at,first_at,last_at,send_next_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)''',
                      (p['id'],p['group'],p['source'],p['level'],p['title'],p.get('body',''),p.get('link'),run,p.get('peek',False),p.get('detail'),p.get('created_at',at),at,at,due if p['level']=='urgent' else None))
                    status = 'inserted'
            db.execute('INSERT INTO receipts VALUES(?,?,?)',(p['id'],at,json.dumps(p)))
        return status

    def card(self, row):
        result = dict(row)
        result.update(id=result['card_id'],peek=bool(result['peek']),archived=bool(result['archived_at']))
        result['run'] = json.loads(result['run']) if result['run'] else None
        return result

    def feed(self, scope='active'):
        with self.db() as db:
            rows = db.execute('SELECT * FROM cards ORDER BY last_at DESC,card_id').fetchall()
        messages = [self.card(r) for r in rows]
        active = [m for m in messages if not m['archived']]
        counts = dict(active=len(active),unread=sum(m['read_at'] is None for m in active),urgent=sum(m['level']=='urgent' for m in active),archived=len(messages)-len(active))
        return {'messages': [m for m in messages if scope=='all' or m['archived']==(scope=='archived')], 'counts':counts}

    def preview(self):
        feed = self.feed()
        unread = [m for m in feed['messages'] if m['read_at'] is None]
        return dict(messages=feed['messages'][:5],top=unread[:3],unread_count=feed['counts']['unread'],counts=feed['counts'])

    def delivery_health(self):
        with self.db() as db:
            row = db.execute("""SELECT
              SUM(send_next_at IS NOT NULL AND sent_at IS NULL), MAX(sent_at),
              SUM(send_next_at IS NOT NULL AND sent_at IS NULL AND send_attempts > 0)
              FROM cards""").fetchone()
        return dict(pending_count=row[0] or 0, last_sent_at=row[1], failed_count=row[2] or 0)

    def transition(self, card_id, action):
        column = {'read':'read_at','archive':'archived_at'}[action]
        with self.db() as db:
            found = db.execute('SELECT 1 FROM cards WHERE card_id=?',(card_id,)).fetchone()
            if not found:
                raise KeyError('Unknown card')
            # An owner archive also clears resolution, so a later fault cannot reopen the card.
            db.execute('UPDATE cards SET '+column+'=COALESCE('+column+',?)'+(',send_next_at=NULL,resolved_at=NULL' if action=='archive' else '')+' WHERE card_id=?',(now(),card_id))
        return {'ok':True}

    def window(self, card_id):
        with self.db() as db:
            row = db.execute('SELECT ran_window FROM cards WHERE card_id=?', (card_id,)).fetchone()
        if row is None or not row['ran_window']:
            raise ValueError('Card has no previous session')
        name = row['ran_window']
        port = os.environ.get('STATIONDESK_PORT_DEVTERM')
        if not port:
            port = tomllib.loads((self.root/'apps/devterm/app.toml').read_text())['port']
        request = urllib.request.Request(f'http://127.0.0.1:{port}/sessions', headers={'Tailscale-User-Login':os.environ.get('STATIONDESK_OWNER','')})
        with urllib.request.urlopen(request, timeout=10) as response:
            sessions = json.load(response)['sessions']
        active = any(session.get('name') == name for session in sessions)
        return dict(ok=True, state='active' if active else 'ended', url='/devterm/?session='+name, session=dict(id=name,name=name))

    def run(self, card_id, values, note=''):
        with self.db() as db:
            row = db.execute('SELECT * FROM cards WHERE card_id=?',(card_id,)).fetchone()
        if row is None or not row['run']:
            raise ValueError('Card has no run action')
        run = json.loads(row['run'])
        if not isinstance(values, dict):
            raise ValueError('params must be object')
        declared = {p['key']:p for p in run.get('params',[])}
        if set(values)-set(declared):
            raise ValueError('Unknown run param')
        if not isinstance(note, str):
            raise ValueError('note must be a string')
        prompt = run['prompt']
        if note.strip():
            prompt += '\n\nOwner note: ' + note.strip()
        selected = {}
        for key, param in declared.items():
            value = values.get(key,param.get('default',''))
            if not isinstance(value,str) or 'choices' in param and value not in param['choices']:
                raise ValueError('Invalid run param value')
            selected[key] = value
        if row['count'] > 1:
            prompt += '\n\nMessage context: last_at=%s count=%s' % (row['last_at'],row['count'])
        if declared:
            prompt += '\n\nParameters: ' + json.dumps(selected,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        port = os.environ.get('STATIONDESK_PORT_DEVTERM')
        if not port:
            port = tomllib.loads((self.root/'apps/devterm/app.toml').read_text())['port']
        name = 'card-'+uuid.uuid4().hex[:12]
        request = urllib.request.Request(f'http://127.0.0.1:{port}/sessions',data=json.dumps(dict(name=name,cwd=run['cwd'],prompt=prompt)).encode(),headers={'Content-Type':'application/json','Tailscale-User-Login':os.environ.get('STATIONDESK_OWNER','')})
        with urllib.request.urlopen(request,timeout=10) as response:
            result = json.load(response)
            if not result.get('ok'):
                raise ValueError('devterm session failed')
        with self.db() as db:
            db.execute('UPDATE cards SET ran_at=?,ran_input=?,ran_window=? WHERE card_id=?',(now(),json.dumps(selected),name,card_id))
        return dict(ok=True,url='/devterm/?session='+name,session=dict(id=name,name=name),ran_at=now(),ran_window=name)

    def sweep(self):
        at = datetime.now(timezone.utc)
        idle = (at-timedelta(hours=48)).isoformat()
        cutoff = (at-timedelta(days=180)).isoformat()
        with self.db() as db:
            db.execute('UPDATE cards SET archived_at=?,send_next_at=NULL WHERE archived_at IS NULL AND read_at<=? AND last_at<=?',(at.isoformat(),idle,idle))
            db.execute('DELETE FROM cards WHERE last_at<=?',(cutoff,))
            db.execute('DELETE FROM receipts WHERE received_at<=?',(cutoff,))
