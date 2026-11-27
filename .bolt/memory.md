# Project memory

## Product intent & audience

- Telegram shop bot with two planned frontends: a Telegram bot and a Telegram Mini App (web app running inside Telegram), both sharing one database.
- Feature set: products, wallet, deposits, resellers, gifts, giveaways, lucky draw, CDK, AM subscriptions, admin panel.
- Audience: customers buying through Telegram; resellers and admins managing the shop.

## Brand personality & voice

- Not yet defined; current bot interactions appear functional and transactional.

## Visual design & UX direction

- Web app will be a Telegram Mini App — must work inside Telegram's WebView on mobile and desktop.
- UX scope under discussion: full customer experience vs. subset MVP.
- No visual/typographic decisions made yet.

## Decisions, constraints & rationale

- Current stack: Python bot, local SQLite database, `db.py` with ~60+ database functions.
- Proposed migration: SQLite → Supabase (Postgres) so both bot and web app share a single database.
- Rationale: no clean way for SQLite and Postgres to act as one shared database; both frontends must point at the same Postgres instance.
- Constraint: migrating `db.py` is significant — every function must be rewritten for Postgres and the schema recreated in Supabase with access policies.
- Bot may run on SQLite in parallel during migration or switch fully to Supabase; pending user decision.

## Open questions & assumptions

- Web app scope: full customer experience or subset MVP?
- Migration strategy: keep bot on SQLite in parallel or fully switch to Supabase?
- Supabase project/account not yet provisioned.
- Authentication model for web app (Telegram Web App initData vs. separate auth) not decided.
- Whether bot and web app share identical business logic or web app has its own API layer.