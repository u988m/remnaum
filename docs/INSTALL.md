# Installation and first-run configuration

Supported host: a fresh Ubuntu/Debian Linux server with a public IPv4 address, root/sudo privileges, Docker Engine and Compose (the installer will try to install them using apt).

## Before installation
1. Create DNS A/AAAA records for the webhook and Mini App domains pointing to the server. The two domains may be identical.
2. Make sure TCP ports 80 and 443 are open and unused by another web server.
3. Get a Telegram bot token from @BotFather, your numeric Telegram ID, and a Remnawave Panel API token.
4. Prepare the Remnawave internal squad UUIDs and a publicly available terms-of-service URL.
5. Optional: set a contact for support.

## Install
```bash
git clone https://github.com/u988m/remnaum.git
cd remnaum
sudo bash install.sh
```
The guided installer creates a private .env, a Caddy configuration (automatic HTTPS), and an internal Nginx reverse proxy. Database and Redis are private Docker volumes; no demo customer or payment is seeded. Credentials are never printed after the input step.

Caddy terminates HTTPS, Nginx routes traffic inside Docker. Telegram webhook lives at https://YOUR_WEBHOOK_DOMAIN/telegram/webhook, Mini App at https://YOUR_WEBAPP_DOMAIN/webapp, admin at https://YOUR_WEBAPP_DOMAIN/admin, and Remnawave events at https://YOUR_WEBHOOK_DOMAIN/webhooks/remnawave. Caddy requires working DNS and public HTTP/HTTPS connectivity to obtain certificates.

The installer **does not modify Remnawave Panel settings remotely**. On the Panel server set:
```dotenv
WEBHOOK_ENABLED=true
WEBHOOK_URL=https://YOUR_WEBHOOK_DOMAIN/webhooks/remnawave
WEBHOOK_SECRET_HEADER=THE_SAME_REMNAWAVE_WEBHOOK_SECRET_FROM_REMNAUM_ENV
```
Restart the panel using its documented deployment process. The Remnaum receiver verifies the official HMAC-SHA256 signature; unsigned payloads are rejected.

Use `docker compose ps`, `docker compose logs --tail=100 app`, and `curl https://YOUR_WEBAPP_DOMAIN/health` to check health. Configure BotFather Menu Button / Main Mini App to `https://YOUR_WEBAPP_DOMAIN/webapp`; the app also sets the chat menu button on boot.

## Security / upgrades
- Never commit .env, API tokens, webhook secrets, or customer databases.
- First installation should start with zero users, payments and subscriptions; existing volumes are preserved by install and update.
- **Do not use** `docker compose down -v` on production; it deletes database volumes.
- Back up database and the matching `APP_SECRET_KEY` before upgrading.
- Configure `TELEGRAM_TERMS_URL` before enabling Stars payments.
- PostgreSQL schema currently uses SQLAlchemy create_all; a full versioned migration system remains a release blocker.
