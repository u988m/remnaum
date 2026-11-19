<div align="center">

<p align="center">
  <img src="assets/remnaum-banner.svg" alt="Remnaum — VPN storefront for Remnawave" width="100%">
</p>

# 🌊 Remnaum

### Современный VPN-магазин для [Remnawave](https://github.com/remnawave/panel)

**Telegram-бот · Mini App · Веб-магазин · Админка**

Тарифы · Покупки · Устройства · Пробный период · Промокоды · Рефералы · Оплата

[![MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

## ✨ Возможности

- **Для покупателей:** готовые и гибкие тарифы, личный кабинет, подключение VPN, устройства, история покупок, пробный доступ, промокоды и бонусы.
- **Оплата:** Telegram Stars, ЮKassa, ЮMoney, Crypto Pay, Platega — доступность методов выбирает владелец в админке.
- **Для владельца:** управление тарифами и ценами, название магазина и фото приветствия `/start`, оформление Mini App, платежные реквизиты в зашифрованной БД, клиенты, рефералы и статистика.
- **Технологии:** FastAPI · Aiogram 3 · PostgreSQL · Redis · Docker · Caddy/Nginx, отдельные **HTML/CSS/JavaScript** для страниц.
- **Автоматические обновления:** уведомление администраторам о новой версии с кнопкой [«Что нового»](https://t.me/remnaum/4).

**Версия проекта:** номер релиза хранится в одном файле [`VERSION`](VERSION). При следующем выпуске достаточно изменить его — API прочитает новую версию автоматически.

## ⚡ Быстрая установка

На VPS с **Ubuntu или Debian** скачайте проект из GitHub и запустите мастер. Для закрытого репозитория нужен GitHub-аккаунт с доступом и настроенные учётные данные Git на VPS.

```bash
git clone https://github.com/u988m/remnaum.git
cd remnaum
sudo sh install.sh
```

Мастер предложит постоянную папку, проверит Docker и запросит необходимые настройки. Для публичного репозитория доступен также `bootstrap.sh`, который предложит скачать проект или открыть уже установленный экземпляр.

### Обновление

```bash
cd /opt/remnaum
sudo sh install.sh
```

Выберите **«2 — Обновить»**. Из любой папки то же меню открывает команда `sudo remnaum`. Чтобы задать новый пароль штатной базы, выберите **«4 — Сменить пароль PostgreSQL»**.

### Режимы установки

Мастер предложит установить Remnaum **рядом с Remnawave** или **на отдельном VPS**. Он проверит Docker, запросит необходимые настройки и запустит контейнеры. При обновлении данные сохраняются, а перед изменениями создаётся резервная копия.

| Назначение | Пример поддомена |
| --- | --- |
| Вебхуки Telegram, Remnawave и платежей | `webhook.example.com` |
| Mini App, веб-магазин и админка | `miniapp.example.com` |

В режиме совместной установки публичные порты Remnawave **80/443** остаются за её прокси. Для типового Caddy мастер предложит безопасно добавить маршрут с резервной копией.

### Резервные копии

В резервную копию входят PostgreSQL, Redis, файлы и настройки магазина. Можно настроить создание архивов **каждый час или каждый день** и отправку зашифрованных копий в Telegram. Ключ восстановления задаётся вручную или генерируется автоматически. **Храните его отдельно от сервера.**

**[📖 Подробная инструкция по установке и восстановлению](docs/INSTALL.md)**

## 💬 Сообщество

<p align="center"><strong>Обновления, предложения, вопросы и сообщения об ошибках</strong></p>

<table align="center">
  <tr>
    <td align="center"><a href="https://t.me/remnaum/3"><img src="assets/community-bugs.svg" alt="Баги" width="176" height="40"></a></td>
    <td align="center"><a href="https://t.me/remnaum/4"><img src="assets/community-updates.svg" alt="Обновления" width="176" height="40"></a></td>
  </tr>
  <tr>
    <td align="center"><a href="https://t.me/remnaum/5"><img src="assets/community-ideas.svg" alt="Предложения" width="176" height="40"></a></td>
    <td align="center"><a href="https://t.me/remnaum/6"><img src="assets/community-help.svg" alt="Помощь" width="176" height="40"></a></td>
  </tr>
</table>

**Открытый исходный код · [MIT](LICENSE)**
