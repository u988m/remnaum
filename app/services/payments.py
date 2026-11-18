from dataclasses import dataclass
from typing import Protocol

@dataclass
class PaymentLink:
    provider: str
    payment_id: str
    url: str

class PaymentProvider(Protocol):
    async def create_payment(self, *, user_id: int, amount: int, currency: str, description: str) -> PaymentLink: ...
    async def verify_webhook(self, payload: bytes, signature: str) -> bool: ...

class StubPaymentProvider:
    async def create_payment(self, *, user_id: int, amount: int, currency: str, description: str) -> PaymentLink:
        raise NotImplementedError("Configure a real payment provider before accepting payments")

    async def verify_webhook(self, payload: bytes, signature: str) -> bool:
        return False
