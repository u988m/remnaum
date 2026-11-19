# Submit Remnaum to Awesome Remnawave

Official listing: https://docs.rw/awesome-remnawave/
Contribution file: https://github.com/remnawave/panel/blob/main/docs/awesome-remnawave.mdx

## Requirements

- [x] **Open-source:** `u988m/remnaum` is a **public** repository with an MIT license.
- [x] **Remnawave integration:** Remnaum integrates with the Remnawave API to provision and manage VPN subscriptions.
- [ ] **Preview:** add `static/awesome/remnaum.webp` to a fork of `remnawave/panel`.
- [ ] **Pull request:** add a `ProjectCard` to **Commerce** in `docs/awesome-remnawave.mdx` and open a PR against `remnawave/panel:main`.

## Project card for `Commerce`

Insert this component inside the existing `<CategorySection id="commerce" ...>` section, before the existing project cards:

```mdx
    <ProjectCard
        id="remnaum"
        title="Remnaum"
        description="Open-source, self-hosted VPN storefront for Remnawave with a Telegram bot, Telegram Mini App, web store and owner dashboard. Supports configurable plans and Internal Squads, API-based subscription provisioning, device management, trial access, promo codes and referrals. Payment integrations include Telegram Stars, YooKassa, YooMoney, Crypto Pay and Platega. Docker setup supports deployment alongside Remnawave or on a separate VPS."
        author="u988m"
        authorLink="https://github.com/u988m"
        image="/awesome/remnaum.webp"
        githubRepo="u988m/remnaum"
        links={{
            github: 'https://github.com/u988m/remnaum'
        }}
    />
```

## Submission

1. Fork [`remnawave/panel`](https://github.com/remnawave/panel/fork) under the account `u988m`.
2. Create a branch like `docs/add-remnaum` in the fork.
3. Take a screenshot of the running dashboard or Mini App (avoid displaying actual customers, payment identifiers, or API tokens). Frame it at [shots.so](https://shots.so) and compress as WebP with [Squoosh](https://squoosh.app/).
4. Commit that image as `static/awesome/remnaum.webp` and the ProjectCard above in `docs/awesome-remnawave.mdx`.
5. Open the PR targeting **`remnawave/panel:main`**, not this repository's `main`.

Existing payment integrations and installer code do not, by themselves, prove production readiness: test the full Remnawave provisioning and real provider payment flow before making performance or reliability claims.

## Example PR title and summary

**Title:** `docs(awesome): add Remnaum to Commerce`

**Summary:** Add Remnaum, an MIT-licensed self-hosted Remnawave storefront integrating a Telegram bot, Mini App, web store, admin dashboard, subscription provisioning and payment methods. Include a compressed WebP preview in `static/awesome`.
