CREATE TABLE IF NOT EXISTS promo_codes (
    id SERIAL NOT NULL,
    code VARCHAR(32) NOT NULL,
    bonus_days INTEGER NOT NULL,
    max_uses INTEGER NOT NULL,
    used_count INTEGER NOT NULL,
    active BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (code)
);

CREATE TABLE IF NOT EXISTS referral_settings (
    id INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL,
    reward_days INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS remnawave_events (
    id SERIAL NOT NULL,
    body_digest VARCHAR(64) NOT NULL,
    event VARCHAR(128) NOT NULL,
    scope VARCHAR(48) NOT NULL,
    status VARCHAR(24) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (body_digest)
);

CREATE TABLE IF NOT EXISTS store_payment_settings (
    id INTEGER NOT NULL,
    methods JSON NOT NULL,
    encrypted_credentials TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS store_profile (
    id INTEGER NOT NULL,
    data JSON NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS store_tariff_settings (
    id INTEGER NOT NULL,
    pricing JSON NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS tariff_plans (
    id SERIAL NOT NULL,
    title VARCHAR(80) NOT NULL,
    description TEXT NOT NULL,
    duration_days INTEGER NOT NULL,
    traffic_gb INTEGER NOT NULL,
    device_limit INTEGER NOT NULL,
    price_rub_minor INTEGER NOT NULL,
    price_stars INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL,
    sort_order INTEGER NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS trial_settings (
    id INTEGER NOT NULL,
    enabled BOOLEAN NOT NULL,
    days INTEGER NOT NULL,
    traffic_gb INTEGER NOT NULL,
    device_limit INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL NOT NULL,
    telegram_id BIGINT NOT NULL,
    username VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_telegram_id ON users (telegram_id);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    provider VARCHAR(32) NOT NULL,
    provider_payment_id VARCHAR(128),
    amount INTEGER NOT NULL,
    currency VARCHAR(8) NOT NULL,
    status VARCHAR(24) NOT NULL,
    payload TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    UNIQUE (provider_payment_id)
);

CREATE INDEX IF NOT EXISTS ix_payments_status ON payments (status);

CREATE INDEX IF NOT EXISTS ix_payments_user_id ON payments (user_id);

CREATE TABLE IF NOT EXISTS referrals (
    id SERIAL NOT NULL,
    inviter_id INTEGER NOT NULL,
    invited_id INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(inviter_id) REFERENCES users (id),
    FOREIGN KEY(invited_id) REFERENCES users (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_referrals_invited_id ON referrals (invited_id);

CREATE INDEX IF NOT EXISTS ix_referrals_inviter_id ON referrals (inviter_id);

CREATE TABLE IF NOT EXISTS selected_plans (
    user_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    PRIMARY KEY (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(plan_id) REFERENCES tariff_plans (id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    panel_user_id VARCHAR(128),
    status VARCHAR(24) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_subscriptions_panel_user_id ON subscriptions (panel_user_id);

CREATE INDEX IF NOT EXISTS ix_subscriptions_status ON subscriptions (status);

CREATE INDEX IF NOT EXISTS ix_subscriptions_user_id ON subscriptions (user_id);

CREATE TABLE IF NOT EXISTS tariff_drafts (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    duration_days INTEGER NOT NULL,
    traffic_gb INTEGER NOT NULL,
    device_limit INTEGER NOT NULL,
    amount_minor INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_tariff_drafts_user_id UNIQUE (user_id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_tariff_drafts_user_id ON tariff_drafts (user_id);

CREATE TABLE IF NOT EXISTS terms_acceptances (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    terms_version VARCHAR(32) NOT NULL,
    accepted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_terms_acceptance_user_version UNIQUE (user_id, terms_version),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS ix_terms_acceptances_user_id ON terms_acceptances (user_id);

CREATE TABLE IF NOT EXISTS trial_claims (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    panel_user_id VARCHAR(128),
    status VARCHAR(24) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_trial_claims_user_id ON trial_claims (user_id);

CREATE TABLE IF NOT EXISTS promo_redemptions (
    id SERIAL NOT NULL,
    user_id INTEGER NOT NULL,
    promo_id INTEGER NOT NULL,
    bonus_days INTEGER NOT NULL,
    applied_payment_id INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_promo_redemption_user_code UNIQUE (user_id, promo_id),
    FOREIGN KEY(user_id) REFERENCES users (id),
    FOREIGN KEY(promo_id) REFERENCES promo_codes (id),
    FOREIGN KEY(applied_payment_id) REFERENCES payments (id)
);

CREATE INDEX IF NOT EXISTS ix_promo_redemptions_user_id ON promo_redemptions (user_id);

CREATE TABLE IF NOT EXISTS referral_rewards (
    id SERIAL NOT NULL,
    inviter_id INTEGER NOT NULL,
    invited_id INTEGER NOT NULL,
    source_payment_id INTEGER NOT NULL,
    days INTEGER NOT NULL,
    redeemed_payment_id INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(inviter_id) REFERENCES users (id),
    FOREIGN KEY(invited_id) REFERENCES users (id),
    UNIQUE (source_payment_id),
    FOREIGN KEY(source_payment_id) REFERENCES payments (id),
    FOREIGN KEY(redeemed_payment_id) REFERENCES payments (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_referral_rewards_invited_id ON referral_rewards (invited_id);

CREATE INDEX IF NOT EXISTS ix_referral_rewards_inviter_id ON referral_rewards (inviter_id);

CREATE INDEX IF NOT EXISTS ix_referral_rewards_redeemed_payment_id ON referral_rewards (redeemed_payment_id);

CREATE TABLE IF NOT EXISTS subscription_details (
    id SERIAL NOT NULL,
    subscription_id INTEGER NOT NULL,
    subscription_url TEXT,
    traffic_gb INTEGER NOT NULL,
    device_limit INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_subscription_details_subscription_id UNIQUE (subscription_id),
    FOREIGN KEY(subscription_id) REFERENCES subscriptions (id)
);

CREATE INDEX IF NOT EXISTS ix_subscription_details_subscription_id ON subscription_details (subscription_id);
