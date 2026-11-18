# Remnaum — two deployment modes

The **same bot, Mini App, database and Remnawave API integration** are deployed in either mode. Pick the mode based on who already owns public TCP ports 80 and 443.

## Quick start

```bash
git clone https://github.com/u988m/remnaum.git
cd remnaum
sudo sh install.sh
```

This POSIX shell entry point starts the Python 3 standard-library installer. It will ask which mode to use and guide you through domains, BotFather token, Telegram admin IDs and Remnawave API details. You may also use `sudo sh install.sh --mode panel` or `sudo sh install.sh --mode standalone`.

**Requirements:** Ubuntu/Debian with Python 3 and root/sudo access. On a standalone VPS the installer can install official Docker Engine + Compose packages if missing. On an existing Remnawave Panel VPS, it **never installs/replaces Docker automatically**; working Docker Compose is required.

Create A/AAAA DNS records for the webhook domain and the Mini App domain. Use subdomains distinct from the existing Remnawave Panel domain. They can be identical to each other. You also need a BotFather token, your numeric Telegram admin ID, Remnawave Panel HTTPS API URL/token, and active internal squad UUIDs. Provide a public HTTPS terms URL before accepting real Stars payments.

### Mode 1 — shared Panel server (`panel`)

Choose **1** at the installer prompt, or run:

```bash
sudo sh install.sh --mode panel
```

**No Remnaum service binds public 80/443 in this mode.** The app runs on internal container port 8080 and publishes only `127.0.0.1:18080` for a native host proxy. PostgreSQL and Redis are internal Docker services, with separate Remnaum volumes.

The installer detects the *existing* `remnawave-network` when present. In that case it attaches only the Remnaum **app** to that Docker network with alias `remnaum-api`. An existing Panel proxy attached to that same network can forward new domains to `http://remnaum-api:8080`. Remnawave containers, certificates, configuration, service names and ports are not modified. The official Remnawave Caddy and Nginx guides both use `remnawave-network`: [Caddy](https://docs.rw/install/reverse-proxies/caddy/) and [Nginx](https://docs.rw/install/reverse-proxies/nginx/).

To select a different existing proxy network explicitly:

```bash
sudo sh install.sh --mode panel --proxy-network YOUR_EXISTING_DOCKER_NETWORK
```

To use a **host-native** Caddy/Nginx (not running in Docker), choose `--proxy-network none`, then route new domains to `http://127.0.0.1:18080`. You can change the loopback port with `--local-port 18081`. If you use a containerized reverse proxy, it **must share the chosen Docker network** with the Remnaum app; a Docker container generally cannot reach the host's `127.0.0.1`.

After installation the `generated/` directory contains four sample site blocks:

- `remnaum.docker.caddy` — existing Caddy container on the shared Docker network
- `remnaum.docker.nginx.conf` — existing Nginx container on the shared Docker network
- `remnaum.host.caddy` — native host Caddy pointing at loopback
- `remnaum.host.nginx.conf` — native host Nginx pointing at loopback

**Manually add the correct site block to your existing proxy**; do not replace the Panel's Caddyfile or Nginx file. Configure TLS for your new domains using that proxy's normal certificate method. Validate the combined config before reloading the existing proxy. The snippets are examples; installer deliberately does not edit existing Remnawave files, restart the Panel, or assume a particular existing proxy container name.

Example for an existing Dockerized Caddy reverse proxy on the official network:

```caddyfile
https://bot.example.net, https://app.example.net {
    encode zstd gzip
    reverse_proxy http://remnaum-api:8080
}
```

Example for native host Caddy, with the default loopback port:

```caddyfile
https://bot.example.net, https://app.example.net {
    reverse_proxy http://127.0.0.1:18080
}
```

Keep existing Panel site entries and their original Panel domain intact. **Do not forward the Remnawave Panel's domain to Remnaum**; use separate hostnames. Before enabling Telegram webhooks, confirm `https://YOUR_WEBHOOK_DOMAIN/health` returns `status: ok`.

### Mode 2 — separate VPS (`standalone`)

Choose **2** at the installer prompt, or run:

```bash
sudo sh install.sh --mode standalone
```

The installer checks that TCP 80/443 (and UDP 443) are free before deploying:

```text
Internet 80/443 → Caddy (automatic TLS) → internal Nginx → FastAPI:8080
                                                          ↘ PostgreSQL / Redis
```

Only Caddy binds the host's public 80/443; Nginx, FastAPI, PostgreSQL and Redis stay inside the Compose network. DNS must resolve to this VPS, and ports 80/443 must be reachable to obtain HTTPS certificates. Connect to the **remote** Remnawave panel using its existing HTTPS API URL/token. The Panel's own server and ports are untouched.

If port 80 or 443 is already in use, the installer refuses standalone mode and suggests panel/shared-proxy mode; it does **not** stop the existing service.

## Endpoint URLs (both modes)

| Purpose | URL |
| --- | --- |
| Telegram Bot API updates | `https://WEBHOOK_DOMAIN/telegram/webhook` |
| Remnawave outgoing events | `https://WEBHOOK_DOMAIN/webhooks/remnawave` |
| Telegram Mini App | `https://WEBAPP_DOMAIN/app` |
| Owner dashboard via Telegram | `https://WEBAPP_DOMAIN/admin` |
| Public health check | `https://WEBAPP_DOMAIN/health` |

Telegram Bot API webhook registration is retried while public HTTPS is being prepared; register the Mini App domain via BotFather. Incoming Telegram requests require the configured Telegram secret. `/admin` API requests require signed Telegram Mini App initData from an allowed `ADMIN_IDS` entry.

## Remnawave outgoing webhook

The installer generates `REMNAWAVE_WEBHOOK_SECRET` and enables an authenticated **receiving** endpoint. You still need to configure the Panel's outgoing webhook using the matching secret and:

```dotenv
WEBHOOK_ENABLED=true
WEBHOOK_URL=https://WEBHOOK_DOMAIN/webhooks/remnawave
WEBHOOK_SECRET_HEADER=THE_VALUE_OF_REMNAWAVE_WEBHOOK_SECRET
```

Follow the environment variable names and deployment instructions supported by **your installed Remnawave version**. The receiver currently verifies the signature and acknowledges events; automatic event reconciliation into subscriptions is not yet implemented.

## First-run database, updates and diagnostics

The install creates tables and default configuration but **does not seed test users, orders or payments**. PostgreSQL and Redis volumes are independent of the Panel and persist across app restarts and updates. A repeated install preserves existing credentials and uses the previous deployment mode by default. `sudo sh install.sh --mode panel` allows migrating an older Remnaum deployment without deleting volumes.

Commands work in both modes because the selected Compose overlays are stored in `.env` via `COMPOSE_FILE`:

```bash
docker compose config --services
docker compose ps
docker compose logs --tail=100 app
bash backup.sh
bash update.sh
```

Panel mode diagnostic: `curl http://127.0.0.1:18080/health`, then `curl https://YOUR_WEBAPP_DOMAIN/health` after integrating the existing proxy.

Standalone diagnostic: `curl https://YOUR_WEBAPP_DOMAIN/health` after DNS and Caddy TLS are ready.

Do not run `docker compose down -v` on production. Preserve a secure copy of `.env` (especially `APP_SECRET_KEY`) together with database backups. Remnaum still needs versioned Alembic migrations and real end-to-end payment validation before a stable production 1.0.0 release.
