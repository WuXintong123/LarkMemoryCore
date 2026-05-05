#!/usr/bin/env python3
"""Feishu <-> LarkMemoryCore bridge using lark-oapi long connection.

Flow:
  Feishu user message -> lark-oapi WebSocket client -> this bridge ->
  LarkMemoryCore /v1/chat/completions (with memory metadata) ->
  reply text -> lark-oapi reply_message API -> Feishu chat.

No public webhook is needed. The bridge keeps a long-lived WebSocket
connection to Feishu. To run it locally:
  1. Set FEISHU_APP_ID / FEISHU_APP_SECRET env vars (and optionally
     FEISHU_VERIFY_TOKEN / FEISHU_ENCRYPT_KEY)
  2. Configure LARK_MEMORY_CORE_BASE_URL / LARK_MEMORY_CORE_API_KEY /
     LARK_MEMORY_CORE_MODEL if defaults below do not match
  3. Run: python ops/feishu_bridge.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

import requests

try:  # lark-oapi is installed lazily via requirements-bridge.txt
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )
except ImportError as exc:  # pragma: no cover
    print(
        "Missing dependency 'lark-oapi'. Install with:\n"
        "  python -m pip install lark-oapi requests",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / ".run/feishu-office-competition/logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
BRIDGE_LOG = LOG_DIR / "feishu_bridge.log"

logging.basicConfig(
    level=os.environ.get("FEISHU_BRIDGE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BRIDGE_LOG, encoding="utf-8"),
    ],
)
log = logging.getLogger("feishu_bridge")


def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value or ""


APP_ID = env("FEISHU_APP_ID", required=True)
APP_SECRET = env("FEISHU_APP_SECRET", required=True)
VERIFY_TOKEN = env("FEISHU_VERIFY_TOKEN", "")
ENCRYPT_KEY = env("FEISHU_ENCRYPT_KEY", "")
# Used to detect "is this message @-mentioning the bot?" in group chats.
# Lookup once: GET https://open.feishu.cn/open-apis/bot/v3/info with tenant token.
BOT_OPEN_ID = env("FEISHU_BOT_OPEN_ID", "")

BASE_URL = env("LARK_MEMORY_CORE_BASE_URL", "http://127.0.0.1:18100")
API_KEY = env("LARK_MEMORY_CORE_API_KEY", required=True)
MODEL_ID = env("LARK_MEMORY_CORE_MODEL", "moonshot/kimi-k2.5")
MAX_TOKENS = int(env("LARK_MEMORY_CORE_MAX_TOKENS", "256"))
TENANT_ID = env("LARK_MEMORY_CORE_TENANT_ID", "tenant-real")
PROJECT_ID = env("LARK_MEMORY_CORE_PROJECT_ID", "feishu-office")


def call_lark_memory_core(prompt: str, conversation_id: str) -> str:
    """POST /v1/chat/completions and return assistant text."""
    payload: Dict[str, Any] = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "metadata": {
            "tenant_id": TENANT_ID,
            "project_id": PROJECT_ID,
            "conversation_id": conversation_id,
        },
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    started = time.time()
    resp = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=300,
    )
    elapsed = time.time() - started
    if resp.status_code != 200:
        log.error(
            "LarkMemoryCore non-200: status=%s body=%s",
            resp.status_code,
            resp.text[:1000],
        )
        return f"[LarkMemoryCore HTTP {resp.status_code}] {resp.text[:200]}"
    data = resp.json()
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    hit = resp.headers.get("x-larkmemorycore-memory-hit-count", "0")
    log.info(
        "memory_hits=%s latency=%.2fs prompt_chars=%d reply_chars=%d",
        hit,
        elapsed,
        len(prompt),
        len(text),
    )
    return text or "(LarkMemoryCore returned empty reply)"


def extract_text(message_event: P2ImMessageReceiveV1) -> str:
    """Extract user-readable text from a Feishu message event."""
    msg = message_event.event.message
    content_raw = msg.content or "{}"
    try:
        content = json.loads(content_raw)
    except json.JSONDecodeError:
        return ""
    msg_type = msg.message_type or ""
    if msg_type == "text":
        return content.get("text", "")
    if msg_type == "post":
        try:
            chunks = []
            for line in content.get("content", []):
                for node in line:
                    if node.get("tag") == "text":
                        chunks.append(node.get("text", ""))
                    elif node.get("tag") == "at":
                        chunks.append("@" + node.get("user_name", ""))
            return "".join(chunks)
        except Exception:
            return content_raw
    return content_raw


def strip_at_mentions(text: str) -> str:
    return text.replace("@_user_1", "").strip()


def _bot_mention_keys(event: P2ImMessageReceiveV1) -> set[str]:
    """Return the set of mention "key"s that point to the configured bot."""
    keys: set[str] = set()
    mentions = getattr(event.event.message, "mentions", None) or []
    for m in mentions:
        m_id = getattr(m, "id", None)
        open_id = getattr(m_id, "open_id", None) if m_id is not None else None
        if BOT_OPEN_ID and open_id == BOT_OPEN_ID:
            key = getattr(m, "key", None)
            if key:
                keys.add(key)
    return keys


def is_bot_mentioned(event: P2ImMessageReceiveV1) -> bool:
    """True if this message @-mentions the bot.

    p2p messages are treated as always-mentioned (the user is talking
    directly to the bot). For group messages we look at message.mentions
    for an entry whose open_id matches FEISHU_BOT_OPEN_ID.
    """
    chat_type = getattr(event.event.message, "chat_type", "") or ""
    if chat_type == "p2p":
        return True
    if not BOT_OPEN_ID:
        # Without a configured bot id we can't be sure; keep prior behaviour:
        # treat any message we receive as a mention (this matches how the
        # default `im:message.group_at_msg` permission already filters
        # non-mentions out before they reach us).
        return True
    return bool(_bot_mention_keys(event))


# --- Async worker + dedup ---------------------------------------------------
# Feishu re-pushes an event over the WS channel if it does not see an ack
# within ~10s. Our LarkMemoryCore call can take 10-30s, so we must:
#   1) return from the handler ASAP (SDK acks the frame on return)
#   2) do the slow work on a worker pool
#   3) ignore duplicate message_ids within a sliding window so a retry
#      doesn't trigger a second reply.

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu-bridge")
_SEEN_LOCK = threading.Lock()
_SEEN_MESSAGES: "OrderedDict[str, float]" = OrderedDict()
_SEEN_MAX = 500
_SEEN_TTL_SECONDS = 600


def _is_duplicate(message_id: str) -> bool:
    if not message_id:
        return False
    now = time.time()
    with _SEEN_LOCK:
        # purge expired entries (oldest first)
        while _SEEN_MESSAGES:
            oldest_id, oldest_ts = next(iter(_SEEN_MESSAGES.items()))
            if (now - oldest_ts) > _SEEN_TTL_SECONDS:
                _SEEN_MESSAGES.popitem(last=False)
            else:
                break
        if message_id in _SEEN_MESSAGES:
            return True
        _SEEN_MESSAGES[message_id] = now
        if len(_SEEN_MESSAGES) > _SEEN_MAX:
            _SEEN_MESSAGES.popitem(last=False)
    return False


def _process_message(lark_client: "lark.Client", message_id: str, chat_id: str, user_text: str) -> None:
    try:
        reply_text = call_lark_memory_core(
            user_text, conversation_id=chat_id or "feishu-dm"
        )
    except requests.RequestException as exc:
        log.exception("LarkMemoryCore request failed: %s", exc)
        reply_text = f"[Bridge error] {exc}"
    except Exception as exc:  # pragma: no cover - safety net
        log.exception("worker crashed: %s", exc)
        reply_text = f"[Bridge internal error] {exc}"

    body = ReplyMessageRequestBody.builder()
    body.content(json.dumps({"text": reply_text}, ensure_ascii=False))
    body.msg_type("text")
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(body.build())
        .build()
    )
    try:
        resp = lark_client.im.v1.message.reply(req)
    except Exception as exc:  # pragma: no cover - network issues
        log.exception("reply api raised: %s", exc)
        return
    if not resp.success():
        log.error(
            "reply failed code=%s msg=%s log_id=%s",
            resp.code,
            resp.msg,
            resp.get_log_id(),
        )
    else:
        log.info("reply ok message_id=%s", message_id)


def handle_message(lark_client: "lark.Client", event: P2ImMessageReceiveV1) -> None:
    msg = event.event.message
    chat_id = msg.chat_id or ""
    message_id = msg.message_id or ""
    chat_type = getattr(msg, "chat_type", "") or "unknown"
    user_text = strip_at_mentions(extract_text(event))
    mentioned = is_bot_mentioned(event)
    log.info(
        "received chat_type=%s mentioned=%s chat_id=%s message_id=%s chars=%d preview=%s",
        chat_type,
        mentioned,
        chat_id,
        message_id,
        len(user_text),
        user_text[:80],
    )
    if not user_text:
        return
    if _is_duplicate(message_id):
        log.info("dedup skip message_id=%s", message_id)
        return
    if not mentioned:
        # Observed-only path: we have read access to the group but the bot
        # was not @-mentioned, so we record the message and return without
        # invoking LarkMemoryCore or replying. This is the read-all-but-
        # only-reply-on-mention behaviour requested by ops.
        log.info(
            "observe-only chat_id=%s message_id=%s (no reply, not mentioned)",
            chat_id,
            message_id,
        )
        return
    _EXECUTOR.submit(_process_message, lark_client, message_id, chat_id, user_text)


def main() -> int:
    lark_client = (
        lark.Client.builder()
        .app_id(APP_ID)
        .app_secret(APP_SECRET)
        .log_level(lark.LogLevel.INFO)
        .build()
    )

    # NOTE: lark-oapi signature is (encrypt_key, verification_token, level).
    event_handler = (
        lark.EventDispatcherHandler.builder(ENCRYPT_KEY, VERIFY_TOKEN)
        .register_p2_im_message_receive_v1(
            lambda data: handle_message(lark_client, data)
        )
        .build()
    )

    ws_client = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    log.info(
        "Feishu bridge starting: app_id=%s base_url=%s model=%s",
        APP_ID,
        BASE_URL,
        MODEL_ID,
    )
    ws_client.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
