from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"
    paid_tariff: Mapped[str | None] = mapped_column(String(48))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    panel_user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TariffDraft(Base):
    __tablename__ = "tariff_drafts"
    __table_args__ = (UniqueConstraint("user_id", name="uq_tariff_drafts_user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    duration_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    traffic_gb: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SubscriptionDetails(Base):
    __tablename__ = "subscription_details"
    __table_args__ = (UniqueConstraint("subscription_id", name="uq_subscription_details_subscription_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    subscription_url: Mapped[str | None] = mapped_column(Text)
    traffic_gb: Mapped[int] = mapped_column(Integer)
    device_limit: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TermsAcceptance(Base):
    __tablename__ = "terms_acceptances"
    __table_args__ = (UniqueConstraint("user_id", "terms_version", name="uq_terms_acceptance_user_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    terms_version: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StorePaymentSettings(Base):
    __tablename__ = "store_payment_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    methods: Mapped[dict] = mapped_column(JSON, default=lambda: {
        "telegram_stars": True, "yookassa": False, "yoomoney": False, "cryptopay": False, "platega": False,
    }, nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StoreTariffSettings(Base):
    __tablename__ = "store_tariff_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    pricing: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Referral(Base):
    """A single immutable inviter attribution for each invited customer."""
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inviter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    invited_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TariffPlan(Base):
    """Operator-managed fixed plan; independent RUB and Telegram Stars prices."""
    __tablename__ = "tariff_plans"
    internal_squads: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    traffic_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    price_rub_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    price_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    extra_device_price_rub_minor: Mapped[int | None] = mapped_column(Integer)
    extra_device_price_stars: Mapped[int | None] = mapped_column(Integer)
    badge: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    is_featured: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SelectedPlan(Base):
    __tablename__ = "selected_plans"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("tariff_plans.id"), nullable=False)


class StoreProfile(Base):
    """Single-store branding, editable without restarting the application."""
    __tablename__ = "store_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReferralSettings(Base):
    """Owner-controlled referral rewards. Disabled until the owner opts in."""
    __tablename__ = "referral_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    reward_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReferralReward(Base):
    """One reward per referred customer, granted only after their first fulfilled purchase."""
    __tablename__ = "referral_rewards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inviter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    invited_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True, nullable=False)
    source_payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    redeemed_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RemnawaveEvent(Base):
    """Deduplicated signed event metadata; avoids storing customer data in raw webhooks."""
    __tablename__ = "remnawave_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    body_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="received")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrialSettings(Base):
    """Optional one-time free trial configuration; no demo users are seeded."""
    __tablename__ = "trial_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    traffic_gb: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TrialClaim(Base):
    """At most one trial claim for each account, including failed retries."""
    __tablename__ = "trial_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True, index=True)
    panel_user_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromoCode(Base):
    """Owner-created bonus-day promo codes; redeemed once per account."""
    __tablename__ = "promo_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    bonus_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromoRedemption(Base):
    """Available day credit until the customer's next verified subscription payment."""
    __tablename__ = "promo_redemptions"
    __table_args__ = (UniqueConstraint("user_id", "promo_id", name="uq_promo_redemption_user_code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"), nullable=False)
    bonus_days: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InternalSquadCatalog(Base):
    __tablename__ = "internal_squad_catalog"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    items: Mapped[dict] = mapped_column(JSON, default=dict)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed: Mapped[bool] = mapped_column(default=False)


class SquadPolicy(Base):
    __tablename__ = "squad_policies"
    mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    squads: Mapped[list[str]] = mapped_column(JSON)
