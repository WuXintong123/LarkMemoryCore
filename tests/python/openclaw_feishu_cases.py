import json

import pytest

OPENCLAW_TIMEZONE_PREFIX_CASES = [
    pytest.param("[10:14 GMT+8] nice to meet you", id="hh-mm"),
    pytest.param("[11 GMT+8] nice to meet you", id="hour-only"),
    pytest.param("11 GMT+8] nice to meet you", id="missing-left-bracket"),
    pytest.param("[1:1 GMT+8] nice to meet you", id="single-digit-minute"),
]

OPENCLAW_REAL_FEISHU_SENDER_ID = "ou_b7a2af6fd238fe904886425f8477efe5"


def build_real_openclaw_feishu_transport_text(
    visible_text: str,
    *,
    message_id: str,
    sender_id: str = OPENCLAW_REAL_FEISHU_SENDER_ID,
    transport_timestamp: str = "2026-04-16 10:35:31 GMT+8",
    message_timestamp: str = "Thu 2026-04-16 10:30 GMT+8",
    surface_label: str = "Feishu[default] DM",
) -> str:
    metadata = {
        "message_id": message_id,
        "sender_id": sender_id,
        "sender": sender_id,
        "timestamp": message_timestamp,
    }
    sender_payload = {
        "label": sender_id,
        "id": sender_id,
        "name": sender_id,
    }
    return (
        f"System: [{transport_timestamp}] {surface_label} | "
        f"{sender_id} [msg:{message_id}]\n\n"
        "Conversation info (untrusted metadata):\n"
        f"```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n\n"
        "Sender (untrusted metadata):\n"
        f"```json\n{json.dumps(sender_payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"{visible_text}"
    )


def build_real_openclaw_feishu_content_parts(
    visible_text: str,
    *,
    message_id: str,
    sender_id: str = OPENCLAW_REAL_FEISHU_SENDER_ID,
    transport_timestamp: str = "2026-04-16 10:35:31 GMT+8",
    message_timestamp: str = "Thu 2026-04-16 10:30 GMT+8",
    surface_label: str = "Feishu[default] DM",
):
    return [
        {
            "type": "text",
            "text": build_real_openclaw_feishu_transport_text(
                visible_text,
                message_id=message_id,
                sender_id=sender_id,
                transport_timestamp=transport_timestamp,
                message_timestamp=message_timestamp,
                surface_label=surface_label,
            ),
        }
    ]
