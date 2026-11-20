import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendInvoice, SendMessage
from aiogram.types import CallbackQuery, Message

from app.bot import handlers, menu
from app.db.models import TariffPlan
from app.services.payment_providers import PaymentLink
from app.services.plans import plan_revision
from app.services.store_settings import payment_presentation


class BotMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_checkout_and_back_keep_one_menu_in_the_chat(self):
        chat = {"id": 42, "type": "private"}
        user = {"id": 42, "first_name": "Покупатель", "is_bot": False}
        messages = {}
        deleted = []
        storage = {}

        @asynccontextmanager
        async def lock(*args, **kwargs):
            yield

        bot = Bot("123:test")
        self.addAsyncCleanup(bot.session.close)

        def telegram_message(message_id, **fields):
            return Message.model_validate({
                "message_id": message_id, "date": 1_700_000_000, "chat": chat, **fields,
            }, context={"bot": bot})

        async def send(bot, method, timeout=None):
            if isinstance(method, (SendMessage, SendInvoice)):
                message_id = 100 + send.last_message
                send.last_message += 1
                content = {"text": method.text} if isinstance(method, SendMessage) else {"invoice": {
                    "title": method.title, "description": method.description, "start_parameter": "",
                    "currency": method.currency, "total_amount": method.prices[0].amount,
                }}
                message = telegram_message(message_id, reply_markup=method.reply_markup, **content)
                messages[message_id] = message
                return message
            if isinstance(method, EditMessageText):
                message = telegram_message(method.message_id, text=method.text, reply_markup=method.reply_markup)
                messages[method.message_id] = message
                return message
            if isinstance(method, DeleteMessage):
                deleted.append(method.message_id)
                messages.pop(method.message_id, None)
                return True
            if isinstance(method, AnswerCallbackQuery):
                return True
            raise AssertionError(type(method))

        send.last_message = 0
        middleware = menu.MenuMiddleware()

        async def start(message_id):
            message = telegram_message(message_id, from_user=user, text="/start")
            await middleware(lambda event, _: handlers.start_handler(event), message, {"bot": bot})

        async def press(handler, message, data):
            callback = CallbackQuery.model_validate({
                "id": data, "from_user": user, "chat_instance": "chat",
                "message": message.model_dump(), "data": data,
            }, context={"bot": bot})
            await middleware(lambda event, _: handler(event), callback, {"bot": bot})

        plan = TariffPlan(id=4, title="Тариф", duration_days=30, traffic_gb=0, device_limit=2,
                          price_rub_minor=19900, price_stars=99, extra_device_price_rub_minor=1000,
                          extra_device_price_stars=10, is_active=True)
        order = f"4:0:{plan_revision(plan)}"
        profile = {"name": "Magazin", "tagline": "VPN", "description": ""}
        mock_redis = SimpleNamespace(lock=lock, get=AsyncMock(side_effect=storage.get),
                                     set=AsyncMock(side_effect=storage.__setitem__))
        credentials = {"platega": {"merchant_id": "test", "secret_key": "test"}}
        device_key = "ab" * 16
        device = {"id": device_key, "name": "iPhone", "alias": "", "model": "iPhone",
                  "client": "Happ", "platform": "iOS", "os_version": "18"}
        self.enterContext(patch.object(menu, "get_operations", return_value={"required_channel": ""}))
        self.enterContext(patch.object(handlers, "get_operations", return_value={"extra_devices_max": 1000}))
        with (
            patch.object(menu, "redis", mock_redis),
            patch.object(handlers, "list_document_kinds", return_value=set()),
            patch.object(menu, "customer_is_blocked", return_value=False) as blocked_user,
            patch.object(bot.session, "make_request", side_effect=send),
            patch.object(handlers, "get_or_create_user", return_value=False),
            patch.object(handlers, "get_store_profile", return_value=profile),
            patch.object(handlers, "get_user_subscription", return_value=None) as get_subscription,
            patch.object(handlers, "get_customer_devices", return_value={"has_subscription": False, "items": []}) as get_devices,
            patch.object(handlers, "change_customer_device", return_value=device | {"name": "Телефон", "alias": "Телефон"}) as change_device,
            patch.object(handlers, "retry_paid_payments_for_user"),
            patch.object(handlers, "welcome_image_path", return_value=None),
            patch.object(handlers, "list_plans", return_value=[
                {"id": 4, "title": "Тариф", "price_rub": 199, "price_rub_minor": 19900},
            ]),
            patch.object(handlers, "get_plan", return_value=plan),
            patch.object(handlers, "get_terms_url", return_value=""),
            patch.object(handlers, "get_store_payment_settings", return_value=(
                {"telegram_stars": True, "platega": True}, credentials,
            )),
            patch.object(handlers, "create_payment_for_user", return_value=(
                SimpleNamespace(id=5, amount=19900), PaymentLink("platega", "id", "https://pay.example/5"),
            )) as create_external,
            patch.object(handlers, "is_payment_method_enabled", return_value=True),
            patch.object(handlers, "get_payment_presentation", return_value=payment_presentation()),
            patch.object(handlers, "create_stars_payment_for_user", return_value=(SimpleNamespace(id=6), 99)),
        ):
            await start(1)
            first_menu = messages[100]
            await start(2)
            self.assertEqual(set(messages), {101})
            self.assertEqual(deleted, [100, 1])
            await start(2)
            self.assertEqual(set(messages), {101})
            self.assertNotIn(2, deleted)

            await press(handlers.fixed_plan_handler, first_menu, "plan:select:4")
            self.assertEqual(set(messages), {101})
            await press(handlers.subscription_handler, messages[101], "menu:subscription")
            buttons = messages[101].reply_markup.inline_keyboard
            self.assertEqual([row[0].callback_data for row in buttons], ["menu:devices", "menu:connect", "menu:home"])
            await press(handlers.devices_handler, messages[101], "menu:devices")
            self.assertEqual(messages[101].reply_markup.inline_keyboard[0][0].callback_data, "menu:subscription")
            await press(handlers.subscription_handler, messages[101], "menu:subscription")
            await press(handlers.home_handler, messages[101], "menu:home")
            await press(handlers.plans_handler, messages[101], "menu:plans")
            await press(handlers.fixed_plan_handler, messages[101], "plan:select:4")
            self.assertIn("Тариф", messages[101].text)
            await press(handlers.fixed_plan_checkout_handler, messages[101], f"plan:confirm:{order}")
            methods_page = messages[101]
            self.assertEqual([row[0].callback_data for row in methods_page.reply_markup.inline_keyboard[:2]], [
                f"plan:pay:platega:{order}", f"plan:pay:stars:{order}",
            ])
            await press(handlers.fixed_plan_checkout_handler, methods_page, f"plan:pay:platega:{order}")
            await press(handlers.fixed_plan_checkout_handler, methods_page, f"plan:pay:platega:{order}")
            create_external.assert_awaited_once()
            self.assertEqual(set(messages), {101})
            self.assertEqual([row[0].text for row in messages[101].reply_markup.inline_keyboard], ["💳 Оплатить", "← Назад"])

            await press(handlers.fixed_plan_checkout_handler, messages[101], f"plan:confirm:{order}")
            await press(handlers.fixed_plan_checkout_handler, messages[101], f"plan:pay:stars:{order}")
            self.assertEqual(set(messages), {102})
            self.assertTrue(messages[102].reply_markup.inline_keyboard[0][0].pay)
            await press(handlers.fixed_plan_checkout_handler, messages[102], f"plan:confirm:{order}")
            self.assertEqual(set(messages), {103})
            self.assertNotIn(102, messages)

            await press(handlers.fixed_plan_handler, messages[103], "plan:select:4:0")
            await press(handlers.plans_handler, messages[103], "menu:plans")
            await press(handlers.home_handler, messages[103], "menu:home")
            get_subscription.return_value = {
                "status": "active", "expires_at": None, "traffic_gb": 0, "device_limit": 2, "plan_id": 4,
            }
            await press(handlers.subscription_handler, messages[103], "menu:subscription")
            await press(handlers.fixed_plan_handler, messages[103], "plan:select:4:0:r")
            self.assertEqual(messages[103].reply_markup.inline_keyboard[0][0].text, "+ Устройство")
            await press(handlers.fixed_plan_handler, messages[103], "plan:select:4:1:r")
            renewal_order = f"4:1:{plan_revision(plan)}:r"
            await press(handlers.fixed_plan_checkout_handler, messages[103], f"plan:confirm:{renewal_order}")
            await press(handlers.fixed_plan_checkout_handler, messages[103], f"plan:pay:platega:{renewal_order}")
            await press(handlers.fixed_plan_checkout_handler, messages[103], f"plan:confirm:{renewal_order}")
            await press(handlers.fixed_plan_handler, messages[103], "plan:select:4:1:r")
            self.assertEqual(messages[103].reply_markup.inline_keyboard[-1][0].callback_data, "menu:subscription")
            await press(handlers.subscription_handler, messages[103], "menu:subscription")
            self.assertIn("Моя подписка", messages[103].text)

            get_devices.return_value = {"has_subscription": True, "items": [device], "total": 1, "limit": 2}
            await press(handlers.devices_handler, messages[103], "menu:devices")
            device_button = messages[103].reply_markup.inline_keyboard[0][0]
            self.assertEqual(device_button.text, "iPhone · Happ")
            await press(handlers.device_action_handler, messages[103], device_button.callback_data)
            await press(handlers.device_action_handler, messages[103], f"device:name:{device_key}:0")
            name_input = telegram_message(7, from_user=user, text="Телефон")
            await middleware(lambda event, _: handlers.menu_text_handler(event), name_input, {"bot": bot})
            self.assertEqual(set(messages), {104})
            self.assertIn("Имя сохранено", messages[104].text)
            self.assertIn(7, deleted)
            change_device.assert_awaited_once_with(42, device_key, name="Телефон")

            await press(handlers.device_action_handler, messages[104], f"device:remove:{device_key}:0")
            self.assertIn("Место освободится", messages[104].text)
            get_devices.return_value = {"has_subscription": True, "items": [], "total": 0, "limit": 2}
            await press(handlers.device_action_handler, messages[104], f"device:confirm:{device_key}:0")
            change_device.assert_awaited_with(42, device_key, name=None)
            self.assertIn("Устройство удалено", messages[104].text)
            self.assertEqual(set(messages), {104})

            blocked_user.return_value = True
            await start(3)
            await start(4)
            self.assertEqual(set(messages), {106})
            self.assertEqual(messages[106].text, menu.BLOCKED_MESSAGE)
            self.assertIsNone(messages[106].reply_markup)
            self.assertTrue({2, 3} <= set(deleted))
            self.assertNotIn(4, deleted)


            blocked_user.return_value = False
            self.enterContext(patch.object(handlers, "reward_balance", return_value={"days": 7, "earned": 2}))
            self.enterContext(patch.object(handlers, "promo_balance", return_value={"days": 3}))
            self.enterContext(patch.object(handlers, "referral_count", return_value=4))
            self.enterContext(patch.object(handlers, "bot_username", return_value="test_bot"))
            self.enterContext(patch.object(handlers, "get_referral_settings", return_value={"enabled": True, "reward_days": 5}))
            trial = self.enterContext(patch.object(handlers, "get_trial_settings", return_value={"enabled": False}))
            redeem = self.enterContext(patch.object(handlers, "redeem_promo", side_effect=[
                ValueError("Промокод не найден."), {"code": "VALID", "bonus_days": 2},
            ]))

            def active_menu():
                self.assertEqual(len(messages), 1)
                return next(iter(messages.values()))

            await start(5)
            await press(handlers.bonuses_handler, active_menu(), "menu:bonuses")
            self.assertIn("Бонусных дней · <b>10</b>", active_menu().text)
            self.assertEqual(
                [row[0].callback_data for row in active_menu().reply_markup.inline_keyboard],
                ["menu:referral", "menu:promo", "menu:home"],
            )
            await press(handlers.referral_handler, active_menu(), "menu:referral")
            copy, share = active_menu().reply_markup.inline_keyboard[0]
            link = "https://t.me/test_bot?start=ref_42"
            self.assertEqual(copy.copy_text.text, link)
            self.assertEqual(parse_qs(urlsplit(share.url).query), {"url": [link]})

            trial.return_value = {"enabled": True, "days": 3, "traffic_gb": 10, "device_limit": 1}
            await press(handlers.bonuses_handler, active_menu(), "menu:bonuses")
            self.assertIn("menu:trial", [row[0].callback_data for row in active_menu().reply_markup.inline_keyboard])
            await press(handlers.promo_menu_handler, active_menu(), "menu:promo")
            await press(handlers.bonuses_handler, active_menu(), "promo:cancel")
            self.assertIn("<b>Бонусы</b>", active_menu().text)

            async def enter_code(message_id, text):
                message = telegram_message(message_id, from_user=user, text=text)
                await middleware(lambda event, _: handlers.menu_text_handler(event), message, {"bot": bot})

            await enter_code(9, "FREE")
            redeem.assert_not_awaited()
            await press(handlers.promo_menu_handler, active_menu(), "menu:promo")
            await enter_code(10, "WRONG")
            self.assertIn("Попробуйте ещё раз", active_menu().text)
            self.assertEqual(active_menu().reply_markup.inline_keyboard[0][0].callback_data, "promo:cancel")
            await enter_code(11, "VALID")
            self.assertIn("<b>Промокод VALID активирован</b>", active_menu().text)
            redeem.assert_awaited_with(42, "VALID")
            self.assertTrue({10, 11} <= set(deleted))
