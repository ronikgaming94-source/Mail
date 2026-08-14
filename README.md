# Temp Mail Xpress

Temp Mail Xpress is a production-oriented asynchronous Telegram bot that creates
real temporary mailboxes through the official Mail.tm API, listens for incoming
messages through Mail.tm Mercure SSE, and delivers new-message notifications to
Telegram.

## Included capabilities

- Real Mail.tm domain discovery, account creation, token authentication, message
  retrieval, message deletion, mailbox deletion, rate limiting, retries, and
  encrypted credential storage.
- Real-time Mercure SSE listeners that reconnect with backoff and recover active
  mailboxes after a restart.
- Telegram button-first user experience with disclaimer agreement, force
  subscription, maintenance mode, mailbox creation/deletion, safe email display,
  daily bonuses, referrals, credits, and support link.
- PostgreSQL persistence using SQLAlchemy 2 async ORM and asyncpg.
- Admin panel for dashboard statistics, user search, ban/unban, credit changes,
  daily/referral settings, maintenance, force-subscribe channels, broadcast
  queueing, and system health.
- FastAPI `/` and `/health` endpoints for service monitoring.

## Environment variables

Required:

- `BOT_TOKEN` — Telegram BotFather token. Store as a Replit Secret.
- `DATABASE_URL` — PostgreSQL connection string. Replit's managed PostgreSQL
  environment variable is used when available.
- `ADMIN_IDS` — comma-separated numeric Telegram IDs.
- `ENCRYPTION_KEY` — secret used to encrypt Mail.tm passwords and tokens at rest.
- `SUPPORT_BOT_URL` — support URL shown in the Help button.

Optional:

- `PORT` — FastAPI port, default `8000`.

Do not add secrets to `.env`, source files, logs, or the ZIP. Use Replit Secrets
for `BOT_TOKEN` and `ENCRYPTION_KEY`.

## Run

From the project root:

```bash
python -m bot.main
```

The process starts Telegram long polling, restores active Mail.tm listeners, and
serves FastAPI health endpoints on `PORT`.

To validate PostgreSQL, Telegram, and Mail.tm without starting polling:

```bash
python -m bot.main --check
```

Health endpoints:

```text
GET /
GET /health
```

## Setup

1. Create a bot with BotFather and set its token as `BOT_TOKEN`.
2. Add the bot to any force-subscribe channels as an administrator if channel
   membership checks are required.
3. Set `DATABASE_URL` to a PostgreSQL database and set `ADMIN_IDS` to numeric
   Telegram IDs.
4. Set a high-entropy `ENCRYPTION_KEY` as a secret.
5. Run the command above. Tables and default settings are created on first start.

## Admin configuration

Open the bot as a configured administrator and select **Admin Panel**. The panel
supports:

- Dashboard and live health checks
- User search, detail view, ban/unban, and credit adjustments
- Daily bonus reward, cooldown, and enable/disable
- Referral reward and enable/disable
- Maintenance mode
- Force-subscribe channel add/remove/list
- Background text/media broadcast with rate limiting and delivery counters

Force-subscribe channel input format:

```text
channel_id | username | invite_url | display_name
```

The bot uses the numeric ID or configured channel identifier for Telegram
membership checks and never trusts usernames for admin authorization.

## Credit and referral rules

Defaults are stored in PostgreSQL and can be changed by admins:

- New users receive 10 credits.
- Daily bonus is 10 credits once every 24 hours.
- A successful first-time referral gives 5 credits.
- Creating a real Mail.tm account costs 1 credit only after account, token, and
  local mailbox persistence all succeed.
- Every credit change creates a transaction record.
- Balances cannot become negative.

## Mail.tm behavior and limitations

Mail.tm is a temporary email provider. The bot does not promise permanent
mailbox availability, a fixed retention period, or guaranteed delivery. Provider
availability and retention are outside the bot's control.

The bot discovers active domains at runtime instead of hardcoding one. It uses
the official Mail.tm REST API and Mercure event hub, and does not require a
Mail.tm API key. Incoming content is treated as untrusted: Telegram displays
plain text by default and sanitized HTML is stored only as a safe copy. Attachment
metadata is stored, but attachments are not downloaded automatically.

## Troubleshooting

- `DATABASE_URL` errors: confirm PostgreSQL is running and that the connection
  string points to PostgreSQL, not SQLite. Internal Replit database hosts use
  asyncpg without SSL; public providers may use SSL.
- Telegram membership checks fail: add the bot to the channel with permission
  to read members, or disable force subscribe from the admin panel.
- Mail.tm is unreachable: wait for provider recovery. Creation failures do not
  deduct credits.
- Incoming mail stops after a restart: check `/health` and service logs. Active
  mailboxes are loaded from PostgreSQL and their listeners reconnect
  automatically.
