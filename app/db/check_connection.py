"""Check the app's PostgreSQL connection without running migrations."""
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


async def check_connection() -> int:
    try:
        from app.db.session import engine

        try:
            async with asyncio.timeout(10):
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "28P01":
            print(
                "PostgreSQL отклонил пароль из DATABASE_URL. Проверьте пароль роли в существующей БД. "
                "POSTGRES_PASSWORD не меняет пароль в уже созданном томе. "
                "Инструкция по восстановлению доступа: docs/INSTALL.md.",
                file=sys.stderr,
            )
            return 1
        print("PostgreSQL отклонил подключение. Проверьте имя базы, роль и доступ к БД.", file=sys.stderr)
        return 1
    except Exception:
        # SQLAlchemy and settings exceptions can include connection details.
        print("Не удалось подключиться к PostgreSQL. Проверьте DATABASE_URL и доступность БД.", file=sys.stderr)
        return 1
    print("Подключение к PostgreSQL проверено.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(check_connection()))
