"""Optional host accounting at each provider attempt, including retries.

The host owns pricing, scope authorization and durable reservations. Local use
has no host dependency. An accounting error prevents the next provider request.
"""

from contextvars import ContextVar
from typing import Any, Protocol


class CallAccounting(Protocol):
    async def authorize(self, provider: str, model: str, request: dict[str, Any]) -> str: ...

    async def settle(self, reservation: str, response: Any) -> None: ...


current_call_accounting: ContextVar[CallAccounting | None] = ContextVar(
    "provider_call_accounting", default=None
)


async def accounted_call(provider, call, request):
    accounting = current_call_accounting.get()
    if accounting is None:
        return await call(**request)
    reservation = await accounting.authorize(
        type(provider).__name__, request.get("model") or provider.get_default_model(), request
    )
    # On cancellation, process death or transport failure the durable reservation
    # remains held. Never interpret an unknown provider outcome as zero cost.
    response = await call(**request)
    await accounting.settle(reservation, response)
    return response
