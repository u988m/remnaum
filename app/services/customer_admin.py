"""Owner operations on customers, their access, and their retained ledgers."""
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from sqlalchemy import delete, or_, select, update

from app.config import settings
from app.db.models import (
    Payment, PromoRedemption, Referral, ReferralReward, SelectedPlan, Subscription,
    SubscriptionDetails, TariffDraft, TariffPlan, TermsAcceptance, TrialClaim, User,
)
from app.db.session import SessionLocal
from app.services.remnawave import RemnawaveClient, panel_client
from app.services.squads import fetch_catalog, require_available
from app.services.subscriptions import _panel_identity, _unwrap, matches_panel_account, resolve_panel_user


class CustomerNotFound(ValueError):
    pass


async def _locked_customer(session, telegram_id: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
    if user is None:
        raise CustomerNotFound("Клиент не найден.")
    return user


async def _latest_subscription(session, user_id: int) -> Subscription | None:
    return await session.scalar(select(Subscription).where(Subscription.user_id == user_id)
                                .order_by(Subscription.created_at.desc(), Subscription.id.desc()).limit(1))


async def _remove_panel_user(panel: RemnawaveClient, account: dict) -> None:
    if account:
        try:
            await panel.request("DELETE", f"/api/users/{quote(str(_panel_identity(account)), safe='')}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise


async def _clear_subscriptions(session, user_id: int) -> None:
    await session.execute(delete(SubscriptionDetails).where(
        SubscriptionDetails.subscription_id.in_(select(Subscription.id).where(Subscription.user_id == user_id))
    ))
    await session.execute(delete(Subscription).where(Subscription.user_id == user_id))


async def change_display_name(telegram_id: int, display_name: str) -> None:
    async with SessionLocal.begin() as session:
        user = await _locked_customer(session, telegram_id)
        user.display_name = display_name.strip() or None


async def set_customer_block(telegram_id: int, blocked: bool) -> None:
    if blocked and telegram_id in settings.admin_id_set:
        raise ValueError("Администраторов нельзя заблокировать.")
    async with SessionLocal.begin() as session:
        user = await _locked_customer(session, telegram_id)
        if (user.blocked_at is not None) == blocked:
            return
        subscription = await _latest_subscription(session, user.id)
        panel = await panel_client(RemnawaveClient, session=session)
        if subscription or (panel.base_url and panel.token):
            account = await resolve_panel_user(panel, telegram_id, subscription)
            if blocked and account:
                await panel.disable_user(_panel_identity(account))
            elif account and subscription and subscription.status in {"active", "disabled"} and (
                subscription.expires_at and subscription.expires_at > datetime.now(timezone.utc)
            ):
                await panel.enable_user(_panel_identity(account))
        user.blocked_at = datetime.now(timezone.utc) if blocked else None


async def save_customer_subscription(
    telegram_id: int, plan_id: int, expires_at: datetime, traffic_gb: int, device_limit: int,
) -> None:
    if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
        raise ValueError("Укажите будущую дату окончания подписки.")
    async with SessionLocal.begin() as session:
        user = await _locked_customer(session, telegram_id)
        if user.blocked_at:
            raise ValueError("Разблокируйте клиента перед выдачей подписки.")
        plan = await session.get(TariffPlan, plan_id)
        if not plan:
            raise ValueError("Тариф не найден.")
        squads = plan.internal_squads or []
        require_available(squads, await fetch_catalog())
        panel = await panel_client(RemnawaveClient, session=session)
        subscription = await _latest_subscription(session, user.id)
        account = await resolve_panel_user(panel, telegram_id, subscription)
        traffic_bytes = traffic_gb * 1024 ** 3
        if account:
            panel_id = _panel_identity(account)
            updated = _unwrap(await panel.update_user(
                panel_id, expire_at=expires_at, traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=device_limit, active_internal_squads=squads,
            ))
        else:
            updated = _unwrap(await panel.create_user(
                f"tg_{telegram_id}", expires_at, telegram_id=telegram_id, traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=device_limit, active_internal_squads=squads,
            ))
            panel_id = _panel_identity(updated)
        if not matches_panel_account(panel_id, updated) or not updated.get("subscriptionUrl"):
            fetched = _unwrap(await panel.get_user(panel_id))
            if matches_panel_account(panel_id, fetched):
                updated = fetched
        if not matches_panel_account(panel_id, updated):
            raise RuntimeError("Remnawave не подтвердил обновление подписки.")
        if subscription is None:
            subscription = Subscription(user_id=user.id)
            session.add(subscription)
            await session.flush()
        subscription.status = "active"
        subscription.paid_tariff = str(plan.id)
        subscription.panel_user_id = str(panel_id)
        subscription.expires_at = expires_at
        details = await session.scalar(select(SubscriptionDetails).where(
            SubscriptionDetails.subscription_id == subscription.id
        ))
        if not details:
            details = SubscriptionDetails(subscription_id=subscription.id)
            session.add(details)
        if isinstance(updated.get("subscriptionUrl"), str):
            details.subscription_url = updated["subscriptionUrl"]
        details.traffic_gb = traffic_gb
        details.device_limit = device_limit


async def remove_customer(telegram_id: int, *, delete_account: bool) -> None:
    if delete_account and telegram_id in settings.admin_id_set:
        raise ValueError("Администраторов нельзя удалить.")
    async with SessionLocal.begin() as session:
        user = await _locked_customer(session, telegram_id)
        subscription = await _latest_subscription(session, user.id)
        panel = await panel_client(RemnawaveClient, session=session)
        if subscription or (panel.base_url and panel.token):
            account = await resolve_panel_user(panel, telegram_id, subscription)
            await _remove_panel_user(panel, account)
        await _clear_subscriptions(session, user.id)
        if not delete_account:
            return
        await session.execute(delete(Referral).where(or_(Referral.inviter_id == user.id, Referral.invited_id == user.id)))
        await session.execute(delete(ReferralReward).where(ReferralReward.inviter_id == user.id))
        await session.execute(update(ReferralReward).where(ReferralReward.invited_id == user.id).values(invited_id=None))
        for model in (SelectedPlan, TariffDraft, TermsAcceptance, TrialClaim, PromoRedemption):
            await session.execute(delete(model).where(model.user_id == user.id))
        await session.execute(update(Payment).where(Payment.user_id == user.id).values(user_id=None))
        await session.delete(user)
