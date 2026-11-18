"""Private migration verification: exit 0 only if legacy provider secrets are in DB.

Never writes secrets to stdout or logs.
"""
import asyncio
from app.services.legacy_settings import legacy_credentials
from app.services.store_settings import get_store_payment_settings


async def main() -> int:
    old = legacy_credentials()
    if not old:
        return 0
    _, stored = await get_store_payment_settings()
    for provider, values in old.items():
        record = stored.get(provider) or {}
        for field, value in values.items():
            if value and not record.get(field):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
