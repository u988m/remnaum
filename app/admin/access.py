from fastapi import HTTPException
from app.config import settings

def require_admin(telegram_id: int) -> None:
    if telegram_id not in settings.admin_id_set:
        raise HTTPException(status_code=403, detail="Administrator access required")
