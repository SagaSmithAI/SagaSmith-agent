"""Host-issued accounting callback; no hosted billing policy lives in the Agent."""

import json
import uuid
from urllib.parse import urlsplit


class HostedCallAccounting:
    def __init__(self, client, callback):
        self.client = client
        self.callback = callback
        for name in ("authorize_url", "settle_url"):
            parsed = urlsplit(str(callback.get(name) or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid accounting callback URL")
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("invalid accounting callback URL")
        if not isinstance(callback.get("token"), str) or not callback["token"]:
            raise ValueError("accounting callback credential required")
        self.reservations = set()

    async def _post(self, endpoint, payload):
        response = await self.client.post(
            self.callback[endpoint],
            headers={"Authorization": f"Bearer {self.callback['token']}"},
            json=payload,
            follow_redirects=False,
            timeout=15,
        )
        if response.status_code != 200:
            # Response bodies can contain secrets or implementation details.
            raise RuntimeError("host accounting rejected the model request")
        return response.json()

    async def authorize(self, provider, model, request):
        if provider != "OpenAICompatProvider":
            raise RuntimeError("hosted accounting supports OpenAICompatProvider only")
        output_limit = request.get("max_tokens")
        if not isinstance(output_limit, int) or isinstance(output_limit, bool) or output_limit <= 0:
            raise RuntimeError("hosted model output must have an explicit positive limit")
        encoded = json.dumps(
            {"messages": request.get("messages"), "tools": request.get("tools")},
            ensure_ascii=False,
        ).encode("utf-8")
        result = await self._post("authorize_url", {
            "call_id": str(uuid.uuid4()), "provider": provider, "model": model,
            "max_output_tokens": output_limit, "request_bytes": len(encoded),
        })
        reservation = result.get("reservation_id")
        if not isinstance(reservation, str) or not reservation or reservation in self.reservations:
            raise RuntimeError("host accounting returned an invalid reservation")
        self.reservations.add(reservation)
        return reservation

    async def settle(self, reservation, response):
        await self._post("settle_url", {
            "reservation_id": reservation,
            "usage": response.usage or {},
            "finish_reason": response.finish_reason,
            "request_id": getattr(response, "request_id", None),
        })
