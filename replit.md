# Temp Mail Xpress

An asynchronous Telegram temporary-mail bot backed by PostgreSQL and a configured email service.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string
- `python -m bot.main` — run Telegram polling, inbox listeners, and FastAPI health endpoints
- `python -m bot.main --check` — validate PostgreSQL, Telegram, and the email service without polling

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)
- Bot: Python 3.11+, aiogram 3, FastAPI, SQLAlchemy async ORM, asyncpg, aiohttp

## Where things live

- `bot/main.py` — application startup, Telegram polling, FastAPI server, and listener lifecycle
- `bot/config.py` — environment-backed configuration
- `bot/database/models.py` — PostgreSQL source-of-truth models
- `bot/services/mailtm/` — email service REST, parser, and event implementation
- `bot/services/` — credits, referrals, daily bonus, force subscribe, admin, and mailbox services
- `bot/bot/handlers/` — button-first Telegram user and admin handlers
- `README.md`, `.env.example`, `requirements.txt` — operations and setup

## Architecture decisions

- Mailbox creation and local credit deduction are coordinated in one transaction; failed provider operations never consume credits.
- Mailbox credentials are encrypted with the application key and are never sent to Telegram or written to logs.
- Mercure SSE listeners are centrally tracked and rebuilt from active PostgreSQL mailboxes after restart.
- Runtime business rules live in `bot_settings` so admins can change bonuses, referrals, maintenance, and notification behavior.

## Product

Users receive signup credits, create temporary mailboxes, get automatic Telegram notifications for incoming messages, manage credits and referrals, and claim daily bonuses. Admins manage users, settings, channels, credits, broadcasts, and health.

## User preferences

- Keep the bot button-driven, real-data backed, and free of fake provider responses.

## Gotchas

- `DATABASE_URL` is normalized for asyncpg; internal Replit PostgreSQL hosts disable SSL while public providers may keep it.
- The email service controls temporary mailbox retention and availability; the bot must not promise permanence.
- Use `python -m bot.main --check` before enabling long polling on a new environment.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
