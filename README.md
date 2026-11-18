# Remnaum

Remnaum is a Telegram-first VPN subscription platform built around the Remnawave panel API.

## Current status

**Current milestone: 0.2.0.** The bot registers Telegram users and provides a starter menu for plans, profile, subscription and support. Billing, subscription provisioning and the customer Web App are not production-ready yet.

## Planned modules

- Telegram bot: onboarding, plans, subscription status and support.
- Billing: provider adapters, payment verification, idempotent webhook processing and refunds.
- Subscription service: lifecycle, renewals, expiry jobs and panel synchronization.
- Remnawave adapter: isolated client for creating users, reading user/subscription data, enabling/disabling accounts and extending expiry. API calls use Bearer-token authentication.
- Admin: role-based access, users, plans, payments, announcements and audit log.
- Web App: authenticated customer dashboard and subscription management.
- Operations: installer, updater, backups, migrations, health checks and release notes.

## Architecture

A modular monolith keeps deployment simple while separating domain boundaries. FastAPI hosts HTTP endpoints and the Telegram webhook. Aiogram supports webhook and polling operation. PostgreSQL is the source of truth; Redis is reserved for cache, FSM and background-job coordination. External panel and payment systems are accessed through adapters.

## Quick start

Requirements: Linux server, Docker Engine and Docker Compose plugin.

```bash
git clone https://github.com/u988m/remnaum.git
cd remnaum
chmod +x install.sh update.sh
./install.sh
```

The installer creates `.env`, generates application and database secrets, and optionally asks for the Telegram token, admin Telegram IDs and Remnawave connection. You can edit `.env` later and restart with `docker compose up -d`.

## Configuration

See [`.env.example`](.env.example). Keep secrets in `.env`; never commit it.

- Set `TELEGRAM_USE_WEBHOOK=false` to use polling.
- For webhook mode, set `TELEGRAM_USE_WEBHOOK=true`, `TELEGRAM_WEBHOOK_URL` and a long random `TELEGRAM_WEBHOOK_SECRET`. Put the service behind HTTPS.
- Set `REMNAWAVE_BASE_URL` to the panel root URL (without `/api`) and `REMNAWAVE_API_TOKEN` to the panel API token. The adapter currently implements the documented `/api/users` and `/api/subscriptions` operations.
- Set `SUPPORT_CONTACT` to the text or contact link shown in the support section.
- Payment integration is intentionally disabled until a provider is selected and verified.

## Operations

```bash
docker compose logs -f app
docker compose ps
./update.sh
```

The updater fetches and fast-forward pulls the currently checked-out branch, then rebuilds containers. Back up PostgreSQL before production updates. Database migrations and automated rollback are not wired yet.

## Releases

Every release must update `CHANGELOG.md`, the application version, and a Git tag. Use semantic versioning:

- **MAJOR**: incompatible API or data changes.
- **MINOR**: backward-compatible features.
- **PATCH**: fixes and security updates.

## Security baseline

- Never expose PostgreSQL or Redis ports publicly.
- Use HTTPS for Telegram and payment webhooks.
- Verify provider signatures and make payment callbacks idempotent before enabling payments.
- Verify Telegram Web App init data server-side before trusting user identity.
- Use role-based admin checks and audit sensitive actions.
- Restrict and rotate panel/API tokens; do not log secrets.
