"""SnowLuma (OneBot v11) channel over HTTP API + WebSocket."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Annotated, Any, Literal

import aiohttp
from loguru import logger
from pydantic import Field
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Base
from nanobot.security.network import validate_url_target
from nanobot.utils.helpers import safe_filename

# Import the SnowLuma action tool so we can wire it up
from nanobot.agent.tools.snowluma import configure as snowluma_tool_configure

_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=60)
_ACTION_TIMEOUT = 20.0


# `"mention"` (only @mentions / replies) | `"open"` (every message) | float p
# in [0, 1]: mentions/replies always reply; other messages reply with probability
# p. 0.0 ≡ "mention", 1.0 ≡ "open".
GroupPolicy = Literal["mention", "open"] | Annotated[float, Field(ge=0.0, le=1.0)]


class SnowlumaConfig(Base):
    """SnowLuma (OneBot v11) channel configuration."""

    enabled: bool = False
    http_url: str = "http://127.0.0.1:3000"  # SnowLuma HTTP API
    ws_url: str = "ws://127.0.0.1:3001"       # SnowLuma WebSocket server
    access_token: str = ""                     # WS and HTTP access token (shared)
    http_access_token: str | None = None       # Separate HTTP token if different from WS
    allow_from: list[str] = Field(default_factory=list)
    group_policy: GroupPolicy = "mention"
    group_policy_overrides: dict[str, GroupPolicy] = Field(default_factory=dict)
    welcome_new_members: bool = True
    max_image_bytes: int = Field(default=20 * 1024 * 1024, ge=1)


class SnowlumaChannel(BaseChannel):
    """SnowLuma / OneBot v11 channel (HTTP API + WebSocket)."""

    name = "snowluma"
    display_name = "SnowLuma (QQ)"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return SnowlumaConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = SnowlumaConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: SnowlumaConfig = config

        self._ws: ClientConnection | None = None
        self._http: aiohttp.ClientSession | None = None
        self._media_root: Path = get_media_dir("snowluma")
        self._self_id: int | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._processed_ids: deque[int] = deque(maxlen=2000)
        self._bot_outbound_ids: deque[int] = deque(maxlen=2000)
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def _http_token(self) -> str:
        """Return the HTTP API token (can differ from WS token)."""
        return self.config.http_access_token if self.config.http_access_token else self.config.access_token

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.config.ws_url or not self.config.http_url:
            logger.error("snowluma: ws_url and http_url must be configured")
            return

        self._running = True
        self._http = aiohttp.ClientSession(timeout=_DOWNLOAD_TIMEOUT)

        # Wire up the snowluma_action tool with our HTTP session and HTTP token
        snowluma_tool_configure(self.config.http_url, self._http_token, self._http)

        backoff = iter((5, 10))
        while self._running:
            try:
                await self._run_once()
                backoff = iter((5, 10))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("snowluma: connection lost: {}", e)
            if self._running:
                await asyncio.sleep(next(backoff, 30))

    async def _run_once(self) -> None:
        logger.info("snowluma: connecting to WS {}", self.config.ws_url)
        headers = []
        if self.config.access_token:
            headers.append(("Authorization", f"Bearer {self.config.access_token}"))
        async with ws_connect(self.config.ws_url, additional_headers=headers) as ws:
            self._ws = ws
            logger.info("snowluma: connected to WS")

            try:
                info = await self._http_action("get_login_info", {})
                data = info.get("data") or {}
                self._self_id = data.get("user_id")
                logger.info(
                    "snowluma: logged in as {} (user_id={})",
                    data.get("nickname"),
                    self._self_id,
                )
            except Exception as e:
                logger.warning("snowluma: login info check failed: {}", e)

            try:
                async for raw in ws:
                    await self._dispatch_frame(raw)
            finally:
                self._ws = None

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._http is not None:
            try:
                await self._http.close()
            except Exception:
                pass
            self._http = None
        self._fail_pending(RuntimeError("snowluma: stopped"))
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _fail_pending(self, err: BaseException) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()

    # ------------------------------------------------------------------
    # HTTP Action Helper
    # ------------------------------------------------------------------

    async def _http_action(
        self, action: str, params: dict[str, Any], timeout: float = _ACTION_TIMEOUT,
    ) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("snowluma: HTTP session not available")

        url = self.config.http_url.rstrip("/") + "/" + action
        headers = {"Content-Type": "application/json"}
        http_token = self._http_token
        if http_token:
            headers["Authorization"] = f"Bearer {http_token}"

        async with self._http.post(
            url, json=params, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.json()
            status = body.get("status")
            retcode = body.get("retcode")
            if (status and status != "ok") or (retcode not in (None, 0)):
                raise RuntimeError(
                    f"snowluma: action {action} failed status={status!r} retcode={retcode!r}: {body.get('wording', '')}"
                )
            return body

    # ------------------------------------------------------------------
    # Frame dispatch
    # ------------------------------------------------------------------

    async def _dispatch_frame(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("snowluma: dropping non-JSON frame")
            return
        if not isinstance(payload, dict):
            return

        if (sid := payload.get("self_id")) is not None:
            try:
                self._self_id = int(sid)
            except (TypeError, ValueError):
                pass

        post_type = payload.get("post_type")
        if post_type == "message":
            self._create_background_task(self._on_message(payload), "message")
        elif post_type == "notice":
            self._create_background_task(self._on_notice(payload), "notice")

    def _create_background_task(self, coro: Any, kind: str) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _done(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("snowluma: {} handler failed: {}", kind, e)

        task.add_done_callback(_done)

    # ------------------------------------------------------------------
    # Inbound: messages
    # ------------------------------------------------------------------

    async def _on_message(self, ev: dict[str, Any]) -> None:
        msg_id = ev.get("message_id")
        if isinstance(msg_id, int):
            if msg_id in self._processed_ids:
                return
            self._processed_ids.append(msg_id)

        message_type = ev.get("message_type")
        user_id = ev.get("user_id")
        if user_id is None or message_type not in ("group", "private"):
            return

        segments = self._normalize_segments(ev.get("message"))
        text, images, mentioned_self, reply_to_id = self._parse_segments(segments)

        media_paths: list[str] = []
        for info in images:
            if local := await self._download_image(info):
                media_paths.append(local)

        sender = ev.get("sender") or {}
        nickname = sender.get("card") or sender.get("nickname")

        if message_type == "group":
            group_id = ev.get("group_id")
            if group_id is None:
                return

            replying_to_bot = (
                isinstance(reply_to_id, int) and reply_to_id in self._bot_outbound_ids
            )
            if not self._should_reply_in_group(
                group_id=group_id,
                mentioned_self=mentioned_self,
                replying_to_bot=replying_to_bot,
            ):
                return

            chat_id = f"group:{group_id}"
            content = self._format_group_content(
                text=text,
                nickname=nickname,
                user_id=user_id,
            )
        else:
            chat_id = f"private:{user_id}"
            content = text

        if not content and not media_paths:
            return

        await self._handle_message(
            sender_id=str(user_id),
            chat_id=chat_id,
            content=content,
            media=media_paths or None,
            metadata={
                "message_id": msg_id,
                "is_group": message_type == "group",
                "nickname": nickname,
                "reply_to": reply_to_id,
            },
        )

    @staticmethod
    def _normalize_segments(message: Any) -> list[dict[str, Any]]:
        if isinstance(message, list):
            return [seg for seg in message if isinstance(seg, dict)]
        if isinstance(message, str) and message:
            return [{"type": "text", "data": {"text": message}}]
        return []

    def _parse_segments(
        self, segments: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]], bool, int | None]:
        parts: list[str] = []
        images: list[dict[str, Any]] = []
        mentioned_self = False
        reply_to: int | None = None
        self_id_str = str(self._self_id) if self._self_id is not None else None

        for seg in segments:
            stype = seg.get("type")
            data = seg.get("data") or {}
            if stype == "text":
                if txt := data.get("text"):
                    parts.append(str(txt))
            elif stype == "image":
                url = data.get("url")
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    images.append(
                        {
                            "url": url,
                            "file": data.get("file"),
                            "file_size": data.get("file_size"),
                        }
                    )
                else:
                    logger.warning("snowluma: received invalid image url: {}", url)
            elif stype == "at":
                qq = str(data.get("qq", ""))
                if self_id_str and qq == self_id_str:
                    mentioned_self = True
                else:
                    parts.append(f"@{qq}")
            elif stype == "reply":
                rid = data.get("id")
                try:
                    reply_to = int(rid) if rid is not None else None
                except (TypeError, ValueError):
                    pass
            elif stype == "face":
                parts.append(f"[face:{data.get('id', '')}]")

        text = " ".join(p.strip() for p in parts if p.strip()).strip()
        return text, images, mentioned_self, reply_to

    def _should_reply_in_group(
        self, *, group_id: Any, mentioned_self: bool, replying_to_bot: bool
    ) -> bool:
        if mentioned_self or replying_to_bot:
            return True
        policy = self.config.group_policy_overrides.get(str(group_id), self.config.group_policy)
        if policy == "open":
            return True
        if policy == "mention":
            return False
        return random.random() < float(policy)

    @staticmethod
    def _format_group_content(
        *,
        text: str,
        nickname: str,
        user_id: Any,
    ) -> str:
        label = nickname or str(user_id)
        return f"{label}: {text}"

    # ------------------------------------------------------------------
    # Inbound: notices
    # ------------------------------------------------------------------

    async def _on_notice(self, ev: dict[str, Any]) -> None:
        if ev.get("notice_type") != "group_increase" or not self.config.welcome_new_members:
            return

        group_id = ev.get("group_id")
        user_id = ev.get("user_id")
        if group_id is None or user_id is None:
            return

        try:
            group_id_int = int(group_id)
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return

        nickname = await self._lookup_member_name(group_id_int, user_id_int)

        await self._handle_message(
            sender_id=str(user_id),
            chat_id=f"group:{group_id}",
            content=f"[group event] new member {nickname} joined group {group_id}",
            metadata={
                "is_group": True,
                "event": "group_increase",
            },
        )

    async def _lookup_member_name(self, group_id: int, user_id: int) -> str:
        try:
            resp = await self._http_action(
                "get_group_member_info",
                {"group_id": group_id, "user_id": user_id, "no_cache": True},
            )
            data = resp.get("data", {})
            return data.get("card") or data.get("nickname") or str(user_id)
        except Exception as e:
            logger.warning("snowluma: get_group_member_info failed: {}", e)
            return str(user_id)

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> None:
        kind, _, target = msg.chat_id.partition(":")
        if kind not in ("private", "group") or not target:
            logger.error("snowluma: invalid chat_id '{}'", msg.chat_id)
            return

        segments: list[dict[str, Any]] = []
        for ref in msg.media or []:
            if seg := await self._build_image_segment(ref):
                segments.append(seg)
        if text := (msg.content or "").strip():
            segments.append({"type": "text", "data": {"text": text}})
        if not segments:
            return

        params: dict[str, Any] = {"message": segments}
        if kind == "group":
            params["message_type"] = "group"
            params["group_id"] = int(target)
        else:
            params["message_type"] = "private"
            params["user_id"] = int(target)

        try:
            resp = await self._http_action("send_msg", params)
            data = resp.get("data") or {}
            if (mid := data.get("message_id")) is not None:
                self._bot_outbound_ids.append(int(mid))
        except Exception as e:
            logger.error("snowluma: send failed: {}", e)

    async def _build_image_segment(self, ref: str) -> dict[str, Any] | None:
        ref = (ref or "").strip()
        if not ref:
            return None
        if ref.startswith(("http://", "https://")):
            ok, err = validate_url_target(ref)
            if not ok:
                logger.warning("snowluma: rejected remote image '{}': {}", ref, err)
                return None
            return {"type": "image", "data": {"file": ref}}
        path = Path(os.path.expanduser(ref)).resolve()
        if not path.is_file():
            logger.warning("snowluma: local image not found: {}", path)
            return None
        data = await asyncio.to_thread(path.read_bytes)
        return {"type": "image", "data": {"file": "base64://" + base64.b64encode(data).decode()}}

    # ------------------------------------------------------------------
    # Image download
    # ------------------------------------------------------------------

    async def _download_image(self, info: dict[str, Any]) -> str | None:
        url = info.get("url")
        if not isinstance(url, str):
            return None
        if self._http is None:
            return None
        ok, err = validate_url_target(url)
        if not ok:
            logger.warning("snowluma: skip image '{}': {}", url, err)
            return None
        max_bytes = self.config.max_image_bytes

        try:
            declared_size = int(info["file_size"])
            if declared_size > max_bytes:
                logger.warning(
                    "snowluma: image declared size={} exceeds max_image_bytes={} url={}",
                    declared_size, max_bytes, url,
                )
                return None
        except (TypeError, KeyError):
            pass

        try:
            async with self._http.get(url, allow_redirects=False) as resp:
                if 300 <= resp.status < 400:
                    logger.warning("snowluma: image download redirect rejected url={}", url)
                    return None
                if resp.status >= 400:
                    logger.warning("snowluma: image download status={} url={}", resp.status, url)
                    return None
                buf = bytearray()
                truncated = False
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        truncated = True
                        break
                if truncated:
                    logger.warning(
                        "snowluma: image exceeds max_image_bytes={} url={}", max_bytes, url
                    )
                    return None
                data = bytes(buf)
        except Exception as e:
            logger.warning("snowluma: image download error url={} err={}", url, e)
            return None

        filename_hint = info.get("file")
        if filename_hint:
            name = safe_filename(filename_hint)
        else:
            name = f"{int(time.time() * 1000)}.jpg"
        path = self._media_root / name
        try:
            await asyncio.to_thread(path.write_bytes, data)
        except OSError as e:
            logger.warning("snowluma: failed to save image: {}", e)
            return None
        return str(path)
