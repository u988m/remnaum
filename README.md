<div align="center">

![Remnaum — баннер](assets/remnaum-banner.svg)

# 🌊 Remnaum

### Современный VPN-магазин для [Remnawave](https://github.com/remnawave/panel)

**Telegram-бот · Mini App · Веб-магазин · Админка**

Тарифы · Покупки · Устройства · Пробный период · Промокоды · Рефералы · Оплата

[![MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

## Возможности

- **Для покупателей:** готовые и гибкие тарифы, личный кабинет, подключение VPN, устройства, история покупок, пробный доступ, промокоды и бонусы.
- **Оплата:** Telegram Stars, ЮKassa, ЮMoney, Crypto Pay — доступность методов выбирает владелец в админке.
- **Для владельца:** управление тарифами и ценами, название магазина и фото приветствия `/start`, оформление Mini App, платежные реквизиты в зашифрованной БД, клиенты, рефералы и статистика.
- **Технологии:** FastAPI · Aiogram 3 · PostgreSQL · Redis · Docker · Caddy/Nginx, отдельные **HTML/CSS/JavaScript** для страниц.
- **Автоматические обновления:** уведомление администраторам о новой версии с кнопкой [«Что нового»](https://t.me/remnaum/4).

## ⚡ Установка

На чистом VPS с **Ubuntu / Debian** запустите:

```bash
curl -fsSL https://raw.githubusercontent.com/u988m/remnaum/main/bootstrap.sh | sudo sh
```

Установщик сам скачает проект, предложит папку и последовательно спросит нужные настройки.

**Обновление установленного Remnaum:**

```bash
cd /opt/remnaum
sudo sh install.sh
```

Выберите **«Обновить»** в меню. Если при установке выбрали другую папку, перейдите в неё вместо `/opt/remnaum`.

Мастер предложит режим **рядом с Remnawave** или **на отдельном VPS**, проверит Docker, соберёт настройки и запустит контейнеры. При обновлении сохраняются данные и создаётся резервная копия.

| Назначение | Пример поддомена |
| --- | --- |
| Вебхуки Telegram, Remnawave и платежей | `webhook.example.com` |
| Mini App, веб-магазин и админка | `miniapp.example.com` |

В режиме совместной установки публичные порты Remnawave **80/443** остаются за её прокси. Для типового Caddy мастер предложит безопасно добавить маршрут с резервной копией.

**Резервные копии:** полный архив базы PostgreSQL, Redis, файлов и настроек, расписание **каждый час / день**, хранение в `backups/`, отправка зашифрованной копии в Telegram по желанию. Ключ восстановления можно задать свой или получить автоматически.

[📖 Подробная инструкция](docs/INSTALL.md)

## 💬 Сообщество

<p align="center"><strong>Следите за обновлениями, делитесь идеями и задавайте вопросы</strong></p>

<p align="center">
  <a href="https://t.me/remnaum/3"><img alt="Баги" src="https://img.shields.io/badge/%D0%91%D0%B0%D0%B3%D0%B8-D94A5B?style=for-the-badge&amp;logo=telegram&amp;logoColor=white"></a>
  <a href="https://t.me/remnaum/4"><img alt="Обновления" src="https://img.shields.io/badge/%D0%9E%D0%B1%D0%BD%D0%BE%D0%B2%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F-3978E6?style=for-the-badge&amp;logo=telegram&amp;logoColor=white"></a>
</p>
<p align="center">
  <a href="https://t.me/remnaum/5"><img alt="Предложения" src="https://img.shields.io/badge/%D0%9F%D1%80%D0%B5%D0%B4%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8F-8A56CF?style=for-the-badge&amp;logo=telegram&amp;logoColor=white"></a>
  <a href="https://t.me/remnaum/6"><img alt="Помощь" src="https://img.shields.io/badge/%D0%9F%D0%BE%D0%BC%D0%BE%D1%89%D1%8C-159B84?style=for-the-badge&amp;logo=telegram&amp;logoColor=white"></a>
</p>

**Открытый исходный код · [MIT](LICENSE)**
