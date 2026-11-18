# Changelog

All notable project changes are documented here.

## [0.4.0] - 2026-11-18

### Added
- User-facing tariff builder for duration, traffic allowance and device count.
- Persistent per-user tariff drafts and server-side price calculation.
- Operator-configurable tariff limits, rates and currency.
- Unit tests for tariff price calculation and validation.

### Notes
- Users can customize tariff parameters within operator-defined limits.
- Payment and VPN access activation remain disabled until payment verification and subscription provisioning are implemented.
- The new tariff draft table is created by the existing metadata initialization; no existing table columns are altered.

## [0.3.0] - 2026-11-18

### Added
- Remnawave panel API client with Bearer authentication.
- User creation, lookup, enable/disable and expiry extension operations.
- Subscription lookup by numeric panel user ID.
- Unit tests for API request paths, payloads and authentication.

### Notes
- The adapter follows the published Remnawave API schema. The live compatibility check against the user's 3.4.10 panel still requires their panel URL and a scoped API token.

## [0.2.0] - 2026-11-18

### Added
- Telegram user registration with conflict-safe profile updates.
- Main bot menu with profile, plans, subscription and support sections.
- Basic /help command.
- Configurable support contact.
- Initial database table creation during application startup.

### Notes
- Plan catalogue and subscription status are informational placeholders until the billing and provisioning layers are implemented.
- Automatic table creation is suitable for the early scaffold; production schema changes should use Alembic migrations.

## [0.1.0] - 2026-11-18

### Added
- Initial modular monolith scaffold.
- Telegram bot entry point with polling and webhook modes.
- FastAPI application and webhook endpoint.
- PostgreSQL models for users, subscriptions and payments.
- Isolated Remnawave API adapter.
- Payment provider interface and safe stub.
- Docker Compose deployment.
- Interactive installer and fast-forward updater.
- Environment template and health endpoint.

### Notes
- Subscription lifecycle, payment processing, admin UI and Web App are interfaces/placeholders, not production features yet.
- Remnawave API routes must be implemented against the exact panel API version before provisioning users.
