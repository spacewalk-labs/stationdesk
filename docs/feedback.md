# Optional feedback delivery

The hub suggestion box stays hidden until an intake or mail target is configured.
The existing monitor service handles `/feedback/api/health` and
`/feedback/api/submit`; it derives the owner from the verified identity header.
No separate service is installed. A submission succeeds only when every
configured target accepts it.

The monitor unit reads the optional user-owned
`~/.config/stationdesk/feedback.env` through systemd `EnvironmentFile`.
Restart `stationdesk-dev-monitor.service` after changing that file. Keep it
private (`chmod 600`) because it can contain delivery credentials.

Intake settings:

- `STATIONDESK_FEEDBACK_INTAKE_URL`: target base URL. The relay POSTs to `/submit`.
- `STATIONDESK_FEEDBACK_TOKEN_ENV`: name of the environment variable containing
  its token; defaults to `STATIONDESK_FEEDBACK_TOKEN`.
- The target receives `{owner,text}` JSON and the
  `X-StationDesk-Feedback-Token` header. It returns `{ok:true,issue_url:...}` or
  `{ok:false,error:...}`.

Mail settings:

- `STATIONDESK_FEEDBACK_MAIL_TO` and `STATIONDESK_FEEDBACK_MAIL_FROM`.
- `STATIONDESK_FEEDBACK_MAIL_KEY_ENV`: variable containing the mail key; defaults
  to `RESEND_API_KEY`.
- `STATIONDESK_FEEDBACK_MAIL_API`: optional endpoint override, defaulting to
  `https://api.resend.com/emails`. The JSON contract is
  `{from,to:[address],subject,text,reply_to}` with Bearer authorization.

Set both target settings to deliver to both. Leave all settings absent to keep
feedback disabled. No product-owned endpoint or recipient is supplied.
