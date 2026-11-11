from __future__ import annotations

import json

from PySide6.QtTest import QSignalSpy

from codex_cil.services.pure_chat import (
    PureChatService,
    _SSEParser,
    _extract_reasoning,
    _response_input_content,
)


def test_reasoning_summary_is_extracted_from_responses_output() -> None:
    reasoning = _extract_reasoning(
        {
            "output": [
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "先检查需求"},
                        {"type": "summary_text", "text": "再组织答案"},
                    ],
                    "content": [
                        {"type": "reasoning_text", "text": "内部推理文本"},
                    ],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "最终回答"}],
                },
            ]
        }
    )

    assert reasoning["reasoning_summary"] == "先检查需求\n\n再组织答案"
    assert reasoning["reasoning_raw"] == "内部推理文本"


def test_sse_parser_handles_split_chunks_and_multiline_data() -> None:
    parser = _SSEParser()

    assert parser.feed('event: response.output_text.delta\r\ndata: {"type":') == []
    events = parser.feed(
        '"response.output_text.delta",\r\n'
        'data: "delta":"你好"}\r\n\r\n'
    )

    assert len(events) == 1
    assert events[0][0] == "response.output_text.delta"
    assert json.loads(events[0][1])["delta"] == "你好"


def test_pure_chat_stream_emits_incremental_and_final_events(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    service = PureChatService()
    summary_spy = QSignalSpy(service.reasoningSummaryDelta)
    text_spy = QSignalSpy(service.textDelta)
    completed_spy = QSignalSpy(service.responseReady)

    service._handle_sse_event(
        "response.reasoning_summary_text.delta",
        json.dumps(
            {
                "type": "response.reasoning_summary_text.delta",
                "delta": "先检索资料",
                "summary_index": 0,
                "item_id": "reason-1",
            },
            ensure_ascii=False,
        ),
    )
    service._handle_sse_event(
        "response.output_text.delta",
        json.dumps(
            {
                "type": "response.output_text.delta",
                "delta": "最终回答",
            },
            ensure_ascii=False,
        ),
    )
    service._flush_stream_events()
    service._handle_sse_event(
        "response.completed",
        json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "reasoning",
                            "summary": [{"text": "先检索资料"}],
                            "content": [],
                        },
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "最终回答"}
                            ],
                        },
                    ],
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 8,
                        "total_tokens": 20,
                    },
                },
            },
            ensure_ascii=False,
        ),
    )

    assert summary_spy.count() == 1
    assert text_spy.count() == 1
    assert completed_spy.count() == 1
    text, usage, reasoning = completed_spy.at(0)
    assert text == "最终回答"
    assert usage["total_tokens"] == 20
    assert reasoning["reasoning_summary"] == "先检索资料"


def test_pure_chat_accepts_structured_image_content() -> None:
    content = [
        {"type": "input_text", "text": "看看截图"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,AAAA",
        },
    ]

    assert _response_input_content(content) == content
