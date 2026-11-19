import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
                "message_id": message_id, "date": 0, "chat": chat, **fields,
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
        with (
            patch.object(menu, "redis", mock_redis),
            patch.object(menu, "customer_is_blocked", return_value=False) as blocked_user,
            patch.object(bot.session, "make_request", side_effect=send),
            patch.object(handlers, "get_or_create_user", return_value=False),
            patch.object(handlers, "get_store_profile", return_value=profile),
            patch.object(handlers, "get_user_subscription", return_value=None) as get_subscription,
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
            self.assertEqual(deleted, [1, 100, 2])

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

            blocked_user.return_value = True
            await start(3)
            await start(4)
            self.assertEqual(set(messages), {105})
            self.assertEqual(messages[105].text, menu.BLOCKED_MESSAGE)
            self.assertIsNone(messages[105].reply_markup)
