# Changelog

## 1.0.0-rc.1 — Public installation testing candidate

- Interactive installation for webhook and Mini App domains, BotFather token, Telegram admin IDs and Remnawave Panel API credentials.
- Automatic HTTPS via Caddy and internal Nginx proxy with isolated PostgreSQL and Redis persistent volumes.
- Signed Remnawave webhook receiver (acknowledgment only; reconciliation pending).
- Fixed tariff plan catalog with separate RUB and Stars prices alongside custom tariff builder.
- Telegram bot menus: devices, purchase history, referral links/count, ready plan selection, configurable welcome branding and support.
- Telegram Mini App fixed-plan Stars checkout, configurable branding, subscription management.
- Unified Telegram-authenticated admin payment/pricing settings, ready-plan editor, customer details, store profile.
- Automatic PostgreSQL backup before updates and clean-install smoke tests in CI.
- MIT license, deployment instructions and Awesome Remnawave submission notes.
- Known blockers: Alembic migrations, automatic remote panel webhook configuration, referral rewards and true live-payment validation.


## 0.8.0 — Administrator dashboard

- Added Telegram-authenticated administrator dashboard at `/admin`.
- Restricted dashboard APIs to Telegram IDs configured in `ADMIN_IDS`.
- Added store overview metrics, paginated customer/subscription list and payment history.
- Added `/admin` bot command with a Telegram Mini App launch button for authorized administrators.
- Kept payment credentials and customer payment data behind separate administrator authentication checks.


## 0.6.0 — Payment settings foundation

- Added per-installation payment-method settings with Telegram Stars enabled by default.
- Added encrypted storage for external provider credentials and an administrator-token-protected settings API.
- Added a public payment-method endpoint that never returns credentials.
- Telegram tariff quotes now show independently configured RUB and Stars prices.
- Telegram checkout respects the administrator's Stars enable/disable setting.
- External payment adapters remain separate from Telegram bot and Mini App checkout.

## 0.5.0

- Added modular YooKassa, YooMoney wallet and Telegram Crypto Pay adapters.
- Added verified payment webhooks, idempotent settlement and payment tariff snapshots.
- Added Telegram Stars invoices, terms acceptance and pre-checkout validation.
- Added automatic Remnawave provisioning after successful payments.
- Added subscription details, traffic/device limits and subscription URL storage.
- Added retry of paid-but-unprovisioned Stars payments when users open subscription status.
- Added configurable tariff duration, traffic and device limits.
- Added user tariff builder and pricing calculation.
- Added Docker installer, updater, CI and deployment documentation.
