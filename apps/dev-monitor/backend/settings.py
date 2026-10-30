"""Notification settings shared by the monitor and the card delivery worker."""
import os
from pathlib import Path
import shlex
import tempfile

from card_delivery import slack_config, grace
from state_write import WRITE_LOCK


def slack_status():
    env = slack_config()
    return dict(configured=bool(env.get('SLACK_WEBHOOK_URL') or (env.get('SLACK_BOT_TOKEN') and env.get('SLACK_CHANNEL'))),
                channel=env.get('SLACK_CHANNEL', ''), grace_seconds=grace(env),
                token_set=bool(env.get('SLACK_BOT_TOKEN')), webhook_set=bool(env.get('SLACK_WEBHOOK_URL')))


def save_slack(data):
    with WRITE_LOCK:
        env = slack_config()
        if 'channel' in data:
            if not isinstance(data['channel'], str):
                raise ValueError('channel must be a string')
            env['SLACK_CHANNEL'] = data['channel']
        if 'grace_seconds' in data:
            seconds = int(data['grace_seconds'])
            if seconds < 0:
                raise ValueError('grace_seconds must be nonnegative')
            env['SLACK_GRACE_SECONDS'] = str(seconds)
        for field, key in [('token', 'SLACK_BOT_TOKEN'), ('webhook', 'SLACK_WEBHOOK_URL')]:
            if field in data:
                if not isinstance(data[field], str):
                    raise ValueError(field + ' must be a string')
                env[key] = data[field]
        path = Path.home()/'.config/stationdesk/slack.env'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.slack.', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(''.join(key+'='+shlex.quote(value)+'\n' for key, value in sorted(env.items())))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return slack_status()
