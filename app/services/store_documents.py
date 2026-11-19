from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import StoreDocument
from app.db.session import SessionLocal

DocumentKind = Literal["terms", "privacy"]
DOCUMENT_TITLES = {"terms": "Пользовательское соглашение", "privacy": "Политика конфиденциальности"}
DOCUMENT_MAX_LENGTH = 20_000


async def list_document_kinds() -> set[str]:
    async with SessionLocal() as session:
        return set(await session.scalars(select(StoreDocument.kind)))


async def list_documents() -> dict[str, str]:
    async with SessionLocal() as session:
        rows = await session.execute(select(StoreDocument.kind, StoreDocument.body))
        return dict(rows.tuples().all())


async def get_document(kind: str) -> str | None:
    async with SessionLocal() as session:
        return await session.scalar(select(StoreDocument.body).where(StoreDocument.kind == kind))


async def save_document(kind: DocumentKind, body: str) -> None:
    async with SessionLocal() as session:
        await session.execute(
            insert(StoreDocument).values(kind=kind, body=body)
            .on_conflict_do_update(index_elements=[StoreDocument.kind], set_={"body": body})
        )
        await session.commit()
