# Remnaum

Remnaum is a Telegram-first VPN subscription platform built around the Remnawave panel API.

## Current status

**Current milestone: 0.5.0.** Users configure a personal tariff and purchase VPN access with Telegram Stars (XTR) inside the bot. Verified payments trigger Remnawave provisioning. YooKassa, YooMoney wallet and Crypto Pay adapters are implemented for a separate external storefront; they are not offered inside Telegram for this digital service.

## Planned modules

- Telegram bot: onboarding, plans, subscription status and support.
- Tariff builder: user-selected duration, traffic and device limits, bounded by operator settings and priced from configurable rates.
- Billing: Telegram Stars (XTR) checkout for in-bot digital services; separate YooKassa, YooMoney wallet and Crypto Pay adapters are reserved for a standalone external storefront.
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
- Configure `TARIFF_MIN_*`, `TARIFF_MAX_*` and `TARIFF_PRICE_PER_*` to control tariff limits and fiat price quotes. Set `TARIFF_STARS_PRICE_PER_DAY`, `TARIFF_STARS_PRICE_PER_GB` and `TARIFF_STARS_PRICE_PER_DEVICE` for the in-bot Stars price.
- Publish your terms of service and set `TELEGRAM_TERMS_URL` plus `TELEGRAM_TERMS_VERSION`. The bot requires the user to accept the current terms before showing the Telegram Stars invoice.
- YooMoney wallet checkout requires `YOOMONEY_RECEIVER` and `YOOMONEY_NOTIFICATION_SECRET`; configure its HTTP notification URL as `/webhooks/payments/yoomoney`. This uses the wallet collection/Quickpay flow, not the YooKassa merchant API.
- Crypto Pay requires `CRYPTOPAY_API_TOKEN`; configure its Crypto Pay app webhook URL as `/webhooks/payments/cryptopay`. Supported invoice assets are configurable with `CRYPTOPAY_ACCEPTED_ASSETS`.
- Set `PUBLIC_BASE_URL` to the public HTTPS origin so payment redirects and hosted YooMoney checkout work. Keep all provider credentials in `.env`.

## Admin payment settings

Open `/admin/payments` over HTTPS. Set `ADMIN_API_TOKEN` in `.env`, enter it in the admin page, and configure provider credentials and enablement. Credentials are encrypted at rest using a key derived from `APP_SECRET_KEY`; keep that key stable across restarts and backups. Public endpoints expose enabled/configured flags only, never secrets. Telegram Stars are enabled by default and can be disabled. Third-party provider credentials are stored for the standalone storefront integration; they are not offered in Telegram bot or Mini App checkout for digital VPN access.

The admin page currently manages payment-method settings. The full visual tariff constructor and standalone external checkout are subsequent milestones.

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
- Verify provider signatures and make payment callbacks idempotent before enabling external storefront payments.\n- For in-bot digital services, use Telegram Stars (XTR); do not substitute third-party checkout buttons.
- Verify Telegram Web App init data server-side before trusting user identity.
- Use role-based admin checks and audit sensitive actions.
- Restrict and rotate panel/API tokens; do not log secrets.
