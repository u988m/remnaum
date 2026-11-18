# Remnaum

**Self-hosted VPN storefront constructor for [Remnawave](https://github.com/remnawave/panel)**

Remnaum combines a Telegram bot, Telegram Mini App, admin dashboard, pricing engine and Remnawave Panel integration in one deployable service. One installation runs one independent store; the database is empty on first deployment.

> **Release status: 1.0.0 release candidate.** Suitable for installation testing. Do not treat as production-audited until real Telegram Stars payments, Remnawave provisioning and deployment on a public HTTPS domain have been validated end to end.

## Features

| Component | What it does |
| --- | --- |
| Telegram bot | Start/menu, profile, fixed plans and flexible tariff builder, Stars invoices, subscription status, device list, last 10 purchases, referrals and support |
| Mini App | Telegram-authorized customer view, active subscription, choose a fixed or custom tariff, accept terms and pay with Stars |
| Owner dashboard | Telegram-signed admin access, metrics, customers and payment history, customer detail, fixed-plan CRUD, pricing and payment settings, branding |
| Remnawave | Provision or extend VPN access after a verified payment; read subscription URL and HWID devices; verify signed outgoing panel webhooks |
| Billing | Telegram Stars in Telegram; YooKassa, YooMoney and Crypto Pay adapters for use by a **separate** future external storefront |
| Operations | Guided install, Caddy automatic HTTPS, internal Nginx, Docker Compose, persistent PostgreSQL/Redis, backup script, smoke-test CI |
| Referrals | Individual deep links and referral attribution/count; **no rewards or payouts yet** |
| Branding | Store name, short description, accent color and support contact editable in the admin dashboard |

### Telegram menu

- **📦 Тарифы** — fixed plans and custom tariff builder
- **👤 Профиль** — Telegram account details
- **🔐 Подписка** — status, expiry, limits and connection link
- **📱 Устройства** — hardware-bound devices reported by Remnawave
- **🧾 Мои покупки** — latest 10 payment statuses
- **🎁 Пригласить друга** — unique deep link and referral count
- **🆘 Поддержка** — owner-provided contact
- **🌐 Mini App** — subscription overview and checkout
- **⚙️ /admin** — owner-only settings

## First installation

Requirements: Linux Ubuntu/Debian VPS, Docker access, public A/AAAA DNS records, ports 80 and 443, Telegram bot token and Remnawave Panel API token.

```bash
git clone https://github.com/u988m/remnaum.git
cd remnaum
sudo bash install.sh
```

The installer requests your webhook domain, Mini App domain, certificate email, bot username/token, Telegram admin IDs, Remnawave URL/token, and squad IDs. It creates `.env` with restrictive permissions. Caddy obtains TLS certificates and Nginx privately forwards requests to FastAPI.

- Telegram updates: `https://WEBHOOK_DOMAIN/telegram/webhook`
- Remnawave outgoing events: `https://WEBHOOK_DOMAIN/webhooks/remnawave`
- Telegram Mini App: `https://WEBAPP_DOMAIN/app`
- Admin dashboard: `https://WEBAPP_DOMAIN/admin`
- Health: `https://WEBAPP_DOMAIN/health`

**The Remnawave panel's outbound webhook settings must be configured separately** with the generated `REMNAWAVE_WEBHOOK_SECRET`; see [installation guide](docs/INSTALL.md). The installer does not change your panel server remotely.

Configure the Mini App URL in @BotFather. On app startup the bot also registers the private-chat menu button and command list.

### Data safety

New installations contain **zero fake customers, subscriptions, orders and revenue**. Only schema and default configuration are created. Existing PostgreSQL and Redis Docker volumes persist across restarts and updates. Do not use `docker compose down -v` against a live store.

```bash
bash backup.sh
bash update.sh
docker compose ps
docker compose logs -f app
```

Backups contain PostgreSQL data. Store your `.env` (particularly `APP_SECRET_KEY`) separately and securely to recover encrypted payment settings.

## Configuration and payment requirements

Secrets must never be committed. Review [`.env.example`](.env.example) and [docs/INSTALL.md](docs/INSTALL.md). `ADMIN_IDS` lists authorized Telegram numeric IDs. Login to admin APIs uses verified Telegram Mini App initData, not a manually copied admin API token.

All in-bot and Telegram Mini App digital subscription payments use Telegram Stars (XTR) in accordance with Telegram's bot platform rules. External payment adapters do **not** authorize payment buttons for the same digital goods inside Telegram.

## Architecture

- Python 3, FastAPI, Aiogram 3
- PostgreSQL as source of truth; Redis for infrastructure support
- Async SQLAlchemy models and isolated Remnawave/payment adapters
- Caddy (public HTTPS) → Nginx (internal proxy) → FastAPI (internal)
- Telegram-signed admin and Mini App API authentication
- HMAC verification of Remnawave and provider webhook signatures

## Known release-candidate limitations

- **Alembic versioned migrations are not yet wired**; current schema bootstrap uses `create_all`. Test upgrades against a backup.
- Automatic configuration of outbound webhook settings on the remote Remnawave server is not implemented; configure it in the panel.
- Remnawave webhook payloads are authenticated and acknowledged but are **not yet reconciled** into local subscription status.
- Fixed plans and custom tariffs are supported; automatic recurring billing and referral bonuses are not implemented.
- External web shop checkout is not yet a complete standalone purchasing interface.
- HWID device deletion is not enabled from the bot.
- A clean Docker smoke test does not replace real payment/provisioning tests. No claim of production readiness is made.
- Public GitHub visibility and Awesome Remnawave submission require separate publication/review steps.

## Open source / Awesome Remnawave

Remnaum is licensed under [MIT](LICENSE). After the repository is made public and a production smoke test passes, the project can be proposed for [Awesome Remnawave](https://github.com/remnawave/panel/blob/main/docs/awesome-remnawave.mdx). See [submission notes](docs/AWESOME_REMNAWAVE.md).

Contributions, feedback, bug reports and PRs are welcome.
