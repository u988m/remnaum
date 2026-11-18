# Changelog

## 1.0.0-rc.3 — Storefront, encrypted settings and referrals

- Independent `/store` storefront for regular browsers using signed Telegram Login Widget and short-lived secure sessions.
- External payment methods YooKassa, YooMoney and Crypto Pay are configured only through encrypted PostgreSQL settings (not read from .env for live checkout).
- Optional one-time import of legacy provider secrets into encrypted database during upgrade; old plaintext values removed only after verification.
- Owner-controlled referral rewards in PostgreSQL: one credit per referred first fulfilled purchase, automatic bonus days on inviter's next paid VPN purchase.
- Signed Remnawave webhook receipt ledger, de-duplication and reconciliation of a limited, explicitly mapped set of subscription status events.
- Terms-of-service URL moved into admin-managed store branding.
- Fixed a previously misplaced admin JavaScript block that could break the Mini App and editor.
- Separate documented examples `webhook.example.com` (webhooks) and `miniapp.example.com` (app/admin/standalone site).
- Shortened public README and removed all payment provider secret fields from .env.example.
- Remaining blockers: live end-to-end billing/panel tests, robust migrations, full device management and more advanced automation.


## 1.0.0-rc.2 — Two installation topologies

- Add `sudo sh install.sh` wizard for standalone VPS or co-hosted Remnawave Panel.
- Co-hosted Panel mode never binds public 80/443. Optional shared `remnawave-network` alias `remnaum-api:8080` and host-only port `127.0.0.1:18080`.
- Standalone mode owns 80/443 with Caddy and Nginx and checks port conflicts.
- Generate sample site blocks for existing Docker-based or host-native Caddy/Nginx; never overwrite Panel files.
- Split Docker Compose core and topology-specific overlays, preserving existing volumes and secrets on re-install.
- Add installer validation tests and CI for standalone, local Panel and shared-network Panel topologies.


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
