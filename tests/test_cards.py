"""Card lifecycle and transport contracts without starting local services."""
from datetime import datetime, timedelta, timezone
from email.message import Message
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).parents[1]/'apps/dev-monitor/backend'
sys.path.insert(0,str(BACKEND))
from cards import Cards
import card_delivery

spec = importlib.util.spec_from_file_location('card_handler',BACKEND/'handler.py')
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


class CardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cards = Cards(self.root)
        self.index = 0

    def event(self, **kwargs):
        self.index += 1
        return dict(dict(id='e'+str(self.index),group='test',source='agent',level='normal',title='Hello'),**kwargs)

    def test_merge_raise_only_reread_resolve_and_receipt_dedup(self):
        first = self.event(peek=True,body='a')
        self.assertEqual('inserted',self.cards.ingest(first))
        self.assertEqual('duplicate',self.cards.ingest(first))
        self.cards.transition(first['id'],'read')
        self.cards.ingest(self.event(peek=False,body='a'))
        self.assertIsNotNone(self.cards.feed()['messages'][0]['read_at'])
        promoted = self.event(body='b'); promoted['level']='urgent'
        self.cards.ingest(promoted)
        self.cards.ingest(self.event(body='b'))
        card = self.cards.feed()['messages'][0]
        # The newest message sets the level: an urgent card falls back to normal.
        self.assertEqual((4,'normal',True,None),(card['count'],card['level'],card['peek'],card['read_at']))
        self.assertEqual({'active':1,'unread':1,'urgent':0,'archived':0},self.cards.feed()['counts'])
        self.cards.ingest(self.event(resolves=True))
        self.assertEqual([],self.cards.feed()['messages'])
        self.assertEqual(1,self.cards.feed()['counts']['archived'])
        self.cards.ingest(self.event())
        self.assertEqual(1,self.cards.feed()['counts']['active'])

    def test_identical_fault_counts_without_renewing_peek_and_recovery_realerts(self):
        first = self.event(peek=True, body='exit-code')
        first['level'] = 'urgent'
        with patch('cards.now', return_value='2026-11-02T00:00:00+00:00'):
            self.cards.ingest(first)
        with patch('cards.now', return_value='2026-11-02T00:03:00+00:00'):
            self.cards.ingest(dict(first, id='repeat'))
        card = self.cards.feed()['messages'][0]
        self.assertEqual(2, card['count'])
        self.assertEqual('2026-11-02T00:03:00+00:00', card['last_at'])
        self.assertEqual('2026-11-02T00:00:00+00:00', card['peek_at'])
        self.assertIsNone(card['read_at'])
        with patch('cards.now', return_value='2026-11-02T00:06:00+00:00'):
            self.cards.ingest(dict(first, id='different', body='timeout'))
        self.assertEqual('2026-11-02T00:06:00+00:00', self.cards.feed()['messages'][0]['peek_at'])
        self.cards.ingest(dict(first, id='recovery', resolves=True))
        with patch('cards.now', return_value='2026-11-02T00:09:00+00:00'):
            self.assertEqual('reopened', self.cards.ingest(dict(first, id='recurrence')))
        card = self.cards.feed()['messages'][0]
        self.assertEqual('2026-11-02T00:09:00+00:00', card['peek_at'])
        self.assertEqual(4, card['count'])

    def test_unit_stop_and_onfailure_pair_keep_description_and_one_peek(self):
        fault = dict(self.event(peek=True, body='systemd result: exit-code'), level='urgent')
        with patch('cards.now', return_value='2026-11-02T00:00:00+00:00'):
            self.cards.ingest(fault)
        for index in range(3):
            with patch('cards.now', return_value=f'2026-11-02T00:0{index+1}:00+00:00'):
                self.cards.ingest(dict(fault, id=f'stop-{index}'))
                self.cards.ingest(dict(self.event(peek=True), level='urgent'))
        card = self.cards.feed()['messages'][0]
        self.assertEqual(7, card['count'])
        self.assertEqual('systemd result: exit-code', card['body'])
        self.assertEqual('2026-11-02T00:00:00+00:00', card['peek_at'])
        self.cards.ingest(dict(self.event(peek=True, body='systemd result: timeout'), level='urgent'))
        changed = self.cards.feed()['messages'][0]
        self.assertNotEqual(card['peek_at'], changed['peek_at'])
        self.cards.ingest(self.event(body=''))
        self.assertEqual('', self.cards.feed()['messages'][0]['body'])

    def test_old_database_migrates_and_retains_peek_baseline(self):
        self.cards.ingest(self.event(peek=True))
        before = self.cards.feed()['messages'][0]
        with self.cards.db() as db:
            db.execute('ALTER TABLE cards DROP COLUMN peek_at')
        migrated = Cards(self.root)
        self.assertEqual(before['last_at'], migrated.feed()['messages'][0]['peek_at'])
        migrated.ingest(self.event(peek=True))
        self.assertEqual(before['last_at'], Cards(self.root).feed()['messages'][0]['peek_at'])

    def test_urgency_and_peek_promotion_renew_peek(self):
        self.cards.ingest(self.event())
        with patch('cards.now', return_value='2099-01-01T00:00:00+00:00'):
            self.cards.ingest(self.event(peek=True))
        self.assertEqual('2099-01-01T00:00:00+00:00', self.cards.feed()['messages'][0]['peek_at'])
        with patch('cards.now', return_value='2099-01-01T00:03:00+00:00'):
            self.cards.ingest(dict(self.event(peek=True), level='urgent'))
        self.assertEqual('2099-01-01T00:03:00+00:00', self.cards.feed()['messages'][0]['peek_at'])

    def test_sweep_archives_idle_and_expires_old_receipts(self):
        first = self.event()
        self.cards.ingest(first)
        self.cards.transition(first['id'],'read')
        idle = (datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        with self.cards.db() as db:
            db.execute('UPDATE cards SET read_at=?,last_at=?',(idle,idle))
        self.cards.sweep()
        self.assertEqual(1,self.cards.feed()['counts']['archived'])
        old = (datetime.now(timezone.utc)-timedelta(days=181)).isoformat()
        with self.cards.db() as db:
            db.execute('UPDATE cards SET last_at=?',(old,))
            db.execute('UPDATE receipts SET received_at=?',(old,))
        self.cards.sweep()
        self.assertEqual([],self.cards.feed('all')['messages'])
        self.assertEqual('inserted',self.cards.ingest(first))

    def test_spool_keeps_invalid_and_continues_to_valid(self):
        spool = card_delivery.ensure_spool(self.root)
        (spool/'new/bad.json').write_text('{')
        p = self.event()
        tmp = spool/'tmp'/p['id']
        tmp.write_text(json.dumps(p))
        os.link(tmp,spool/'new'/(p['id']+'.json'))
        tmp.unlink()
        self.assertEqual({'bad':1,'inserted':1},card_delivery.scan(self.cards,spool))
        self.assertEqual(1,len(list((spool/'bad').glob('*.reason'))))
        self.assertFalse(list((spool/'new').iterdir()))

    def test_spool_does_not_follow_a_symlink_from_a_shared_writer(self):
        spool = card_delivery.ensure_spool(self.root)
        p = self.event()
        target = self.root/'elsewhere.json'
        target.write_text(json.dumps(p))
        os.symlink(target,spool/'new'/(p['id']+'.json'))
        self.assertEqual({'bad':1},card_delivery.scan(self.cards,spool))
        self.assertEqual(0,self.cards.feed()['counts']['active'])

    def test_spool_preserves_fault_then_recovery_publication_order(self):
        spool = card_delivery.ensure_spool(self.root)
        fault = self.event(); fault.update(id='z-fault',level='urgent')
        resolved = self.event(resolves='test'); resolved['id']='a-recovery'
        for index,payload in enumerate((fault,resolved)):
            path = spool/'new'/(payload['id']+'.json')
            path.write_text(json.dumps(payload))
            os.utime(path,ns=(1000000+index,1000000+index))
        self.assertEqual({'inserted':1,'resolved':1},card_delivery.scan(self.cards,spool))
        self.assertEqual(0,self.cards.feed()['counts']['active'])

    def test_flapping_unit_stays_one_card(self):
        # Each auto restart sends ExecStopPost and OnFailure faults, then ExecStartPost recovery.
        # Memory-backed storage keeps 3,000 committed transactions from waiting on disk syncs.
        if os.path.isdir('/dev/shm'):
            memory = tempfile.TemporaryDirectory(dir='/dev/shm')
            self.addCleanup(memory.cleanup)
            self.cards = Cards(memory.name)
        unit = 'unit:stationdesk-fileview.service'
        def fault(source):
            return dict(self.event(), group=unit, source=source, level='urgent',
                        title='App unit failed: stationdesk-fileview.service', peek=True)
        for _ in range(1000):
            self.cards.ingest(fault('systemd'))
            self.cards.ingest(fault('stationdesk-card'))
            self.cards.ingest(dict(self.event(), group=unit, resolves=unit))
        self.assertEqual(0,self.cards.feed()['counts']['active'])
        self.assertEqual('reopened',self.cards.ingest(fault('systemd')))
        cards = self.cards.feed('all')['messages']
        self.assertEqual(1,len(cards))
        self.assertEqual((2001,False,None,None),(cards[0]['count'],cards[0]['archived'],cards[0]['resolved_at'],cards[0]['read_at']))
        self.assertGreater(cards[0]['last_at'],cards[0]['first_at'])

    def test_owner_archive_closes_card_for_good(self):
        first = self.event()
        self.cards.ingest(first)
        self.cards.transition(first['id'],'archive')
        self.assertEqual('inserted',self.cards.ingest(self.event()))
        # Archiving a card shown before the page saw its recovery also keeps it closed.
        self.cards.ingest(self.event(resolves=True))
        self.cards.transition(self.cards.feed('all')['messages'][0]['id'],'archive')
        self.assertEqual('inserted',self.cards.ingest(self.event()))
        self.assertEqual(3,len(self.cards.feed('all')['messages']))

    def test_reopened_card_offers_its_run_again(self):
        p = self.event(run=dict(cwd='/tmp',prompt='Fix'))
        self.cards.ingest(p)
        with self.cards.db() as db:
            db.execute("UPDATE cards SET ran_at='x',ran_input='{}',ran_window='card-1'")
        self.cards.ingest(self.event(resolves=True))
        self.cards.ingest(self.event(run=dict(cwd='/tmp',prompt='Fix')))
        card = self.cards.feed()['messages'][0]
        self.assertEqual((None,None,None),(card['ran_at'],card['ran_input'],card['ran_window']))

    BOT = {'SLACK_BOT_TOKEN':'xoxb-test','SLACK_CHANNEL':'alerts'}

    def slack_reply(self, body=b'{"ok":true,"ts":"1.0","channel":"C1"}'):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        response.__enter__.return_value.status = 200
        return response

    def test_reopen_during_recovery_send_keeps_new_alert(self):
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        with patch('urllib.request.urlopen',return_value=self.slack_reply()):
            card_delivery.send_slack(self.cards,self.BOT)
        self.cards.ingest(self.event(resolves=True))
        def reopen_in_flight(*args,**kwargs):
            self.cards.ingest(dict(self.event(),level='urgent'),grace=0)
            return self.slack_reply(b'{"ok":true}')
        with patch('urllib.request.urlopen',side_effect=reopen_in_flight):
            card_delivery.send_slack(self.cards,self.BOT)
        with patch('urllib.request.urlopen',return_value=self.slack_reply(b'{"ok":true}')) as request:
            self.assertEqual(1,card_delivery.send_slack(self.cards,self.BOT))
            self.assertTrue(json.loads(request.call_args.args[0].data)['text'].startswith('🔴'))

    def test_reopened_card_alerts_again_after_grace(self):
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()) as request:
            card_delivery.send_slack(self.cards,self.BOT)
            self.cards.ingest(self.event(resolves=True))
            card_delivery.send_slack(self.cards,self.BOT)
            # A reopen within the grace edits the same message back to red; after it, one new post.
            self.cards.ingest(dict(self.event(),level='urgent'))
            self.assertEqual(1,card_delivery.send_slack(self.cards,self.BOT))
            self.cards.ingest(self.event(resolves=True))
            self.cards.ingest(dict(self.event(),level='urgent'),grace=0)
            self.assertEqual(1,card_delivery.send_slack(self.cards,self.BOT))
            self.assertEqual(0,card_delivery.send_slack(self.cards,self.BOT))
            self.assertEqual(['chat.postMessage','chat.update','chat.update','chat.postMessage'],[c.args[0].full_url.rsplit('/',1)[1] for c in request.call_args_list])

    def test_recovery_edits_by_channel_id_and_never_posts(self):
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()) as request:
            card_delivery.send_slack(self.cards,self.BOT)
            self.assertEqual('alerts',json.loads(request.call_args.args[0].data)['channel'])
            self.cards.ingest(self.event(resolves=True))
            self.assertEqual(1,card_delivery.send_slack(self.cards,self.BOT))
            call = request.call_args.args[0]
            self.assertEqual('chat.update',call.full_url.rsplit('/',1)[1])
            sent = json.loads(call.data)
            self.assertEqual(('C1','1.0'),(sent['channel'],sent['ts']))
            self.assertTrue(sent['text'].startswith('✅'))
        # Recovered before it was sent: nothing goes out. Nor does a webhook get a follow-up.
        p = self.event(group='quiet'); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        self.cards.ingest(self.event(group='quiet',resolves=True))
        hook = {'SLACK_WEBHOOK_URL':'http://example.test'}
        with patch('urllib.request.urlopen',return_value=self.slack_reply(b'')) as request:
            self.assertEqual(0,card_delivery.send_slack(self.cards,hook))
            self.cards.ingest(dict(self.event(group='hook'),level='urgent'),grace=0)
            self.assertEqual(1,card_delivery.send_slack(self.cards,hook))
            self.cards.ingest(self.event(group='hook',resolves=True))
            self.assertEqual(0,card_delivery.send_slack(self.cards,hook))
            self.assertEqual(1,request.call_count)

    def unit_fault(self, **kwargs):
        return dict(self.event(), group='unit:x.service', source='systemd', level='urgent', title='App unit failed', **kwargs)

    def age(self, **fields):
        with self.cards.db() as db:
            for column, delta in fields.items():
                db.execute('UPDATE cards SET %s=? WHERE "group"=?' % column,
                           ((datetime.now(timezone.utc)+delta).isoformat(), 'unit:x.service'))

    def unit_sends(self):
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()) as request:
            card_delivery.send_slack(self.cards,self.BOT)
        return request.call_count

    def test_unit_fault_pages_only_after_a_fifteen_minute_episode(self):
        self.cards.ingest(self.unit_fault())
        self.assertEqual(0,self.unit_sends())
        # Self-healed within the window: never paged.
        self.cards.ingest(self.event(group='unit:x.service',resolves=True))
        self.assertEqual(0,self.unit_sends())
        # Fails again 10 minutes into the same episode: still not due.
        self.age(episode_at=-timedelta(minutes=10), resolved_at=-timedelta(minutes=1))
        self.cards.ingest(self.unit_fault())
        self.assertEqual(0,self.unit_sends())
        # ...and a flapping unit whose episode passes 15 minutes pages once.
        self.cards.ingest(self.event(group='unit:x.service',resolves=True))
        self.age(episode_at=-timedelta(minutes=16), resolved_at=-timedelta(minutes=1))
        self.cards.ingest(self.unit_fault())
        self.assertEqual(1,self.unit_sends())
        # Recovery edits the page; a re-failure in the episode edits it back instead of posting.
        self.cards.ingest(self.event(group='unit:x.service',resolves=True))
        self.assertEqual(1,self.unit_sends())
        self.cards.ingest(self.unit_fault())
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()) as request:
            self.assertEqual(1,card_delivery.send_slack(self.cards,self.BOT))
            self.assertEqual('chat.update',request.call_args.args[0].full_url.rsplit('/',1)[1])
        # Recovered for more than 15 minutes: the next failure is a new episode.
        self.cards.ingest(self.event(group='unit:x.service',resolves=True))
        self.age(resolved_at=-timedelta(minutes=20))
        self.cards.ingest(self.unit_fault())
        self.assertEqual(0,self.unit_sends())
        card = self.cards.feed()['messages'][0]
        self.assertIsNone(card['slack_ts'])
        self.assertGreater(card['send_next_at'],datetime.now(timezone.utc).isoformat())

    def test_webhook_episode_pages_once_across_flaps(self):
        hook = {'SLACK_WEBHOOK_URL':'http://example.test'}
        self.cards.ingest(self.unit_fault())
        self.age(episode_at=-timedelta(minutes=16), send_next_at=-timedelta(minutes=1))
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply(b'')) as request:
            self.assertEqual(1,card_delivery.send_slack(self.cards,hook))
            self.cards.ingest(self.event(group='unit:x.service',resolves=True))
            self.cards.ingest(self.unit_fault())
            self.assertEqual(0,card_delivery.send_slack(self.cards,hook))
            self.assertEqual(1,request.call_count)

    def test_digest_counts_an_episode_once_and_drops_it_when_it_pages(self):
        self.cards.ingest(self.unit_fault())
        self.age(episode_at=-timedelta(minutes=16))
        self.cards.ingest(self.event(group='unit:x.service',resolves=True))
        self.cards.ingest(self.unit_fault())
        self.cards.ingest(self.event(group='unit:x.service',resolves=True))
        with self.cards.db() as db:
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM quiet').fetchone()[0])
        self.cards.ingest(self.unit_fault())
        self.unit_sends()
        self.assertIsNone(self.cards.digest())

    def test_new_episode_posts_to_the_current_channel(self):
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()):
            card_delivery.send_slack(self.cards,self.BOT)
            self.cards.ingest(self.event(resolves=True))
            card_delivery.send_slack(self.cards,self.BOT)
            self.cards.ingest(dict(self.event(),level='urgent'),grace=0)
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()) as request:
            card_delivery.send_slack(self.cards,dict(self.BOT,SLACK_CHANNEL='CNEW'))
            self.assertEqual('CNEW',json.loads(request.call_args.args[0].data)['channel'])

    def test_normal_reopen_does_not_turn_a_recovered_message_red(self):
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()) as request:
            card_delivery.send_slack(self.cards,self.BOT)
            self.cards.ingest(self.event(resolves=True))
            card_delivery.send_slack(self.cards,self.BOT)
            self.cards.ingest(self.event())
            self.assertEqual(0,card_delivery.send_slack(self.cards,self.BOT))
            self.assertEqual(2,request.call_count)

    def test_weekly_digest_counts_routine_events_once_a_week(self):
        paged = self.event(group='paged'); paged['level']='urgent'
        self.cards.ingest(paged,grace=0)
        with patch('urllib.request.urlopen',side_effect=lambda *a,**k: self.slack_reply()):
            card_delivery.send_slack(self.cards,self.BOT)
        for _ in range(2):
            self.cards.ingest(self.event(group='noise'))
        flap = self.event(group='flap'); flap['level']='urgent'
        self.cards.ingest(flap)
        self.cards.ingest(self.event(group='flap',resolves=True))
        pending = self.event(group='pending'); pending['level']='urgent'
        self.cards.ingest(pending)
        counts = self.cards.digest()
        self.assertEqual({'noise':2,'flap':1},counts)
        card = next(m for m in self.cards.feed()['messages'] if m['group'].startswith('digest:'))
        self.assertEqual('normal',card['level'])
        self.assertIn('noise ×2',card['body'])
        self.assertNotIn('paged',card['body'])
        self.assertIsNone(self.cards.digest())  # not again within the week
        with patch('urllib.request.urlopen') as request:
            card_delivery.send_slack(self.cards,self.BOT)
            request.assert_not_called()

    def test_blip_during_first_alert_send_needs_no_second_alert(self):
        # The alert in flight already says this unit is failing, and no recovery was posted:
        # Slack stays one red message whose ts the eventual recovery edits.
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        bot = {'SLACK_BOT_TOKEN':'xoxb-test','SLACK_CHANNEL':'C1'}
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true,"ts":"1.0"}')
        response.__enter__.return_value.status = 200
        def blip_in_flight(*args,**kwargs):
            self.cards.ingest(self.event(resolves=True))
            self.cards.ingest(dict(self.event(),level='urgent'),grace=0)
            return response
        with patch('urllib.request.urlopen',side_effect=blip_in_flight):
            self.assertEqual(1,card_delivery.send_slack(self.cards,bot))
        with patch('urllib.request.urlopen') as request:
            self.assertEqual(0,card_delivery.send_slack(self.cards,bot))
            request.assert_not_called()
        self.cards.ingest(self.event(resolves=True))
        response.__enter__.return_value = io.BytesIO(b'{"ok":true}')
        response.__enter__.return_value.status = 200
        with patch('urllib.request.urlopen',return_value=response) as request:
            self.assertEqual(1,card_delivery.send_slack(self.cards,bot))
            sent = request.call_args.args[0]
            self.assertEqual(('https://slack.com/api/chat.update','1.0'),(sent.full_url,json.loads(sent.data)['ts']))

    def test_distinct_actions_in_same_group_keep_their_cards(self):
        p = self.event(link='https://example.test/first')
        self.cards.ingest(p)
        self.cards.ingest(self.event(link='https://example.test/second'))
        self.cards.ingest(self.event(link='https://example.test/first'))
        cards = self.cards.feed()['messages']
        self.assertEqual(2,len(cards))
        self.assertEqual([1,2],sorted(card['count'] for card in cards))
        self.cards.ingest(self.event(resolves='test'))
        self.assertEqual(0,self.cards.feed()['counts']['active'])

    def test_manual_archive_cancels_pending_slack(self):
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        self.cards.transition(p['id'],'archive')
        with patch('urllib.request.urlopen') as request:
            self.assertEqual(0,card_delivery.send_slack(self.cards,{'SLACK_WEBHOOK_URL':'http://example.test'}))
            request.assert_not_called()

    def test_run_reads_manifest_port_when_service_env_missing(self):
        directory = self.root/'apps/devterm'
        directory.mkdir(parents=True)
        (directory/'app.toml').write_text('port = 19937\n')
        p = self.event(run=dict(cwd='/tmp',prompt='Fix'))
        self.cards.ingest(p)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true}')
        with patch.dict(os.environ,{'STATIONDESK_OWNER':'owner'}),patch('urllib.request.urlopen',return_value=response) as request:
            os.environ.pop('STATIONDESK_PORT_DEVTERM',None)
            self.cards.run(p['id'],{})
            self.assertEqual('http://127.0.0.1:19937/sessions',request.call_args.args[0].full_url)

    def test_run_passes_declared_values_and_records_only_success(self):
        p = self.event(run=dict(cwd='/tmp',prompt='Fix selected kind',params=[dict(key='kind',label='Kind',choices=['bug','typo'],default='bug')]))
        self.cards.ingest(p)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true}')
        with patch.dict(os.environ,{'STATIONDESK_PORT_DEVTERM':'19930','STATIONDESK_OWNER':'owner'}),patch('urllib.request.urlopen',return_value=response) as request:
            result = self.cards.run(p['id'],{'kind':'typo'})
            sent = json.loads(request.call_args.args[0].data)
            self.assertEqual(('/tmp','Fix selected kind\n\nParameters: {"kind":"typo"}'),(sent['cwd'],sent['prompt']))
            self.assertTrue(result['url'].startswith('/devterm/?session=card-'))
            self.assertEqual({'id': result['ran_window'], 'name': result['ran_window']}, result['session'])
            with self.assertRaises(ValueError):
                self.cards.run(p['id'],{'kind':'not-a-choice'})
            request.assert_called_once()
        self.assertIsNotNone(self.cards.feed()['messages'][0]['ran_at'])
        self.assertEqual({'kind':'typo'},json.loads(self.cards.feed()['messages'][0]['ran_input']))
        response.__enter__.return_value = io.BytesIO(b'{"ok":true}')
        with patch.dict(os.environ,{'STATIONDESK_PORT_DEVTERM':'19930','STATIONDESK_OWNER':'owner'}),patch('urllib.request.urlopen',return_value=response) as request:
            self.cards.run(p['id'],{})
            self.assertIn('Parameters: {"kind":"bug"}',json.loads(request.call_args.args[0].data)['prompt'])
        self.assertEqual({'kind':'bug'},json.loads(self.cards.feed()['messages'][0]['ran_input']))

    def test_slack_absent_deferred_and_recovery_before_or_after_delivery(self):
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p)
        with patch('urllib.request.urlopen') as request:
            self.assertEqual(0,card_delivery.send_slack(self.cards,{}))
            self.assertEqual(0,card_delivery.send_slack(self.cards,{'SLACK_WEBHOOK_URL':'http://example.test'}))
            request.assert_not_called()
        self.cards.ingest(self.event(resolves=True))
        with patch('urllib.request.urlopen') as request:
            self.assertEqual(0,card_delivery.send_slack(self.cards,{'SLACK_WEBHOOK_URL':'http://example.test'}))
            request.assert_not_called()
        p = self.event(); p['level']='urgent'
        self.cards.ingest(p,grace=0)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 200
        with patch('urllib.request.urlopen',return_value=response) as request:
            self.assertEqual(1,card_delivery.send_slack(self.cards,{'SLACK_WEBHOOK_URL':'http://example.test'}))
            self.cards.ingest(self.event(resolves=True))
            self.assertEqual(0,card_delivery.send_slack(self.cards,{'SLACK_WEBHOOK_URL':'http://example.test'}))
            self.assertEqual(1,request.call_count)

    def request(self,path,method='GET',data=None,owner='owner',token=None):
        h = object.__new__(handler.Handler)
        h.path = path
        h.headers = Message()
        for key,value in [('Tailscale-User-Login',owner),('Host','desk.test'),('Origin','http://desk.test'),('Content-Type','application/json'),('X-StationDesk-Token',token)]:
            if value is not None:
                h.headers[key] = value
        body = json.dumps(data or {}).encode()
        h.headers['Content-Length'] = str(len(body))
        h.rfile = io.BytesIO(body)
        result = []
        h.reply = lambda *args: result.append(args)
        with patch.dict(os.environ,{'STATIONDESK_ROOT':str(self.root),'STATIONDESK_OWNER':'owner','STATIONDESK_FQDN':''}):  # empty: same-origin falls back to Host
            getattr(h,'do_'+method)()
        return result[0]

    def test_owner_and_ingest_token_boundaries(self):
        for path in ['/api/owner/messages','/api/owner/messages/preview']:
            self.assertEqual(403,self.request(path,owner='other')[0])
        self.assertEqual(401,self.request('/api/ingest','POST',self.event())[0])
        token = card_delivery.ingest_token(self.root)
        p = self.event()
        self.assertEqual(202,self.request('/api/ingest','POST',p,owner=None,token=token)[0])
        self.assertEqual(1,self.request('/api/owner/messages')[1]['counts']['unread'])
        self.assertEqual(403,self.request('/api/owner/messages/'+p['id']+'/read','POST',owner='other')[0])
        self.assertEqual(200,self.request('/api/owner/messages/'+p['id']+'/read','POST')[0])


if __name__ == '__main__':
    unittest.main()
