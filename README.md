# Remnaum

Remnaum is a Telegram-first VPN subscription platform built around the Remnawave panel API.

## Current status

**Current milestone: 0.5.0.** Users can configure a personal tariff, pay through YooKassa, YooMoney wallet notifications or Telegram Crypto Pay, and receive a Remnawave subscription after a verified payment. Live provider credentials and the exact Remnawave 3.4.10 panel still require deployment testing.

## Planned modules

- Telegram bot: onboarding, plans, subscription status and support.
- Tariff builder: user-selected duration, traffic and device limits, bounded by operator settings and priced from configurable rates.
- Billing: YooKassa, YooMoney wallet and Crypto Pay adapters with signed/remote-verified notifications and idempotent local payment states.
- Subscription service: payment-triggered Remnawave provisioning, renewals, tariff limit updates and stored connection URL.
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
- Set `REMNAWAVE_BASE_URL` to the panel root URL (without `/api`) and `REMNAWAVE_API_TOKEN` to the panel API token. Set `REMNAWAVE_ACTIVE_INTERNAL_SQUADS` to the comma-separated internal squad UUIDs assigned to new users.
- Set `SUPPORT_CONTACT` to the text or contact link shown in the support section.
- Configure `TARIFF_MIN_*`, `TARIFF_MAX_*`, `TARIFF_PRICE_PER_*` and `TARIFF_CURRENCY` to control the custom tariff builder. Prices are stored in minor currency units (for EUR, cents).
- Set `TARIFF_CURRENCY=RUB` for the Russian payment methods. YooKassa requires `YOOKASSA_SHOP_ID` and `YOOKASSA_SECRET_KEY`; set the webhook URL to `/webhooks/payments/yookassa` and subscribe to `payment.succeeded`.
- YooMoney wallet checkout requires `YOOMONEY_RECEIVER` and `YOOMONEY_NOTIFICATION_SECRET`; configure its HTTP notification URL as `/webhooks/payments/yoomoney`. This uses the wallet collection/Quickpay flow, not the YooKassa merchant API.
- Crypto Pay requires `CRYPTOPAY_API_TOKEN`; configure its Crypto Pay app webhook URL as `/webhooks/payments/cryptopay`. Supported invoice assets are configurable with `CRYPTOPAY_ACCEPTED_ASSETS`.
- Set `PUBLIC_BASE_URL` to the public HTTPS origin so payment redirects and hosted YooMoney checkout work. Keep all provider credentials in `.env`.

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
