from __future__ import annotations

import codecs
import json
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from codex_cil.services.codex_config import CodexConfig


class PureChatService(QObject):
    """无工具模型通道，不向请求中注册任何 Agent 工具。"""

    responseStarted = Signal()
    reasoningStarted = Signal(str)
    reasoningSummaryDelta = Signal(str, int, str)
    reasoningTextDelta = Signal(str, int, str)
    textDelta = Signal(str)
    responseReady = Signal(str, dict, dict)
    requestCancelled = Signal()
    requestFailed = Signal(str)

    def __init__(
        self,
        config: CodexConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._sse = _SSEParser()
        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.setInterval(40)
        self._stream_timer.timeout.connect(self._flush_stream_events)
        self._pending_events: list[tuple[str, tuple[Any, ...]]] = []
        self._answer_deltas: list[str] = []
        self._completed_text_parts: list[str] = []
        self._diagnostic_bytes = bytearray()
        self._response_emitted = False
        self._failure_emitted = False
        self._cancel_requested = False
        self.config = config or CodexConfig.load()

    def send(
        self,
        messages: list[dict[str, str]],
        instructions: str | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
    ) -> None:
        self.config = CodexConfig.load()
        if self.config.load_error:
            self.requestFailed.emit(self.config.load_error)
            return
        if not self.config.model:
            self.requestFailed.emit(
                "核心配置中没有设置默认模型。请在 config.toml 中配置 model。"
            )
            return
        if not self.config.provider.base_url:
            self.requestFailed.emit(
                f"模型 Provider `{self.config.provider_id}` 没有配置 base_url。"
            )
            return
        if self.config.provider.wire_api != "responses":
            self.requestFailed.emit(
                "当前无工具纯对话只支持模型 Provider 的 Responses API 协议；"
                f"当前 wire_api={self.config.provider.wire_api}。"
            )
            return
        if not self.config.provider.resolved_bearer_token() and self.config.provider.has_authentication:
            self.requestFailed.emit(
                f"模型 Provider `{self.config.provider_id}` 的认证不可用："
                f"{self.config.auth_description}。"
            )
            return
        if self._reply is not None:
            self.requestFailed.emit("上一条纯对话仍在生成中。")
            return

        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": [
                {
                    "role": item["role"],
                    "content": _response_input_content(item["content"]),
                }
                for item in messages
            ],
            "store": False,
            "stream": True,
        }
        if instructions:
            payload["instructions"] = instructions
        effort = reasoning_effort or self.config.reasoning_effort
        if effort:
            payload["reasoning"] = {"effort": effort}
        if self.config.verbosity:
            payload["text"] = {"verbosity": self.config.verbosity}
        if service_tier:
            payload["service_tier"] = service_tier

        request_url = QUrl(self.config.provider.request_url())
        if self.config.provider.query_params:
            query = QUrlQuery()
            for key, value in self.config.provider.query_params.items():
                query.addQueryItem(key, value)
            request_url.setQuery(query)
        request = QNetworkRequest(request_url)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Accept", b"text/event-stream")
        request.setRawHeader(b"Cache-Control", b"no-cache")
        for name, value in self.config.provider.request_headers().items():
            request.setRawHeader(name.encode("utf-8"), value.encode("utf-8"))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._reset_stream_state()
        self._reply = self._manager.post(request, body)
        self._reply.readyRead.connect(self._read_stream_data)
        self._reply.finished.connect(self._handle_finished)

    def cancel(self) -> None:
        self._cancel_requested = True
        self._stream_timer.stop()
        self._pending_events.clear()
        if self._reply is None:
            self._cancel_requested = False
            self.requestCancelled.emit()
            return
        self._reply.abort()

    def _reset_stream_state(self) -> None:
        self._stream_timer.stop()
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._sse = _SSEParser()
        self._pending_events.clear()
        self._answer_deltas.clear()
        self._completed_text_parts.clear()
        self._diagnostic_bytes.clear()
        self._response_emitted = False
        self._failure_emitted = False
        self._cancel_requested = False

    def _read_stream_data(self, reply: QNetworkReply | None = None) -> None:
        reply = reply or self._reply
        if reply is None:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        chunk = bytes(reply.readAll())
        if not chunk:
            return
        if len(self._diagnostic_bytes) < 262_144:
            self._diagnostic_bytes.extend(
                chunk[: 262_144 - len(self._diagnostic_bytes)]
            )
        if status and int(status) >= 400:
            return
        text = self._decoder.decode(chunk)
        for event_name, data_text in self._sse.feed(text):
            self._handle_sse_event(event_name, data_text)

    def _handle_sse_event(self, event_name: str, data_text: str) -> None:
        if self._cancel_requested:
            return
        if not data_text or data_text == "[DONE]":
            return
        try:
            data = json.loads(data_text)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        event_type = str(data.get("type") or event_name or "")
        if event_type in {"response.created", "response.in_progress"}:
            self.responseStarted.emit()
            return
        if event_type == "response.output_item.added":
            item = data.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "reasoning":
                self.reasoningStarted.emit(_event_item_id(data, item))
            return
        if event_type == "response.reasoning_summary_part.added":
            self.reasoningStarted.emit(_event_item_id(data))
            return
        if event_type == "response.reasoning_summary_text.delta":
            delta = str(data.get("delta") or "")
            if delta:
                self._queue_event(
                    "reasoning_summary",
                    (
                        delta,
                        int(data.get("summary_index") or 0),
                        _event_item_id(data),
                    ),
                )
            return
        if event_type == "response.reasoning_text.delta":
            delta = str(data.get("delta") or "")
            if delta:
                self._queue_event(
                    "reasoning_text",
                    (
                        delta,
                        int(data.get("content_index") or 0),
                        _event_item_id(data),
                    ),
                )
            return
        if event_type == "response.output_text.delta":
            delta = str(data.get("delta") or "")
            if delta:
                self._answer_deltas.append(delta)
                self._queue_event("text", (delta,))
            return
        if event_type == "response.output_text.done":
            text = str(data.get("text") or "")
            if text:
                self._completed_text_parts.append(text)
            return
        if event_type in {"response.completed", "response.incomplete"}:
            self._flush_stream_events()
            response = data.get("response") or {}
            if not isinstance(response, dict):
                response = {}
            self._emit_final_response(response)
            return
        if event_type == "response.failed":
            self._flush_stream_events()
            response = data.get("response") or {}
            error = response.get("error") if isinstance(response, dict) else None
            message = _error_message(error or data)
            self._emit_failure(f"纯对话请求失败: {message}")
            return
        if event_type == "error":
            self._flush_stream_events()
            self._emit_failure(
                f"纯对话请求失败: {_error_message(data)}"
            )

    def _queue_event(self, kind: str, payload: tuple[Any, ...]) -> None:
        self._pending_events.append((kind, payload))
        if not self._stream_timer.isActive():
            self._stream_timer.start()

    def _flush_stream_events(self) -> None:
        self._stream_timer.stop()
        pending = self._pending_events
        self._pending_events = []
        for kind, payload in pending:
            if kind == "text":
                self.textDelta.emit(str(payload[0]))
            elif kind == "reasoning_summary":
                self.reasoningSummaryDelta.emit(
                    str(payload[0]),
                    int(payload[1]),
                    str(payload[2]),
                )
            elif kind == "reasoning_text":
                self.reasoningTextDelta.emit(
                    str(payload[0]),
                    int(payload[1]),
                    str(payload[2]),
                )

    def _emit_final_response(self, response: dict[str, Any]) -> None:
        if self._failure_emitted or self._cancel_requested:
            return
        self._flush_stream_events()
        text = _extract_output_text(response)
        if not text:
            text = "".join(self._completed_text_parts)
        if not text:
            text = "".join(self._answer_deltas)
        usage = _extract_usage(response.get("usage") or {})
        reasoning = _extract_reasoning(response)
        self._response_emitted = True
        self.responseReady.emit(text, usage, reasoning)

    def _emit_failure(self, message: str) -> None:
        if (
            self._failure_emitted
            or self._response_emitted
            or self._cancel_requested
        ):
            return
        self._failure_emitted = True
        self._stream_timer.stop()
        self.requestFailed.emit(message)

    def _handle_finished(self) -> None:
        reply = self._reply
        if reply is None:
            return
        self._reply = None
        if self._cancel_requested:
            self._cancel_requested = False
            self._stream_timer.stop()
            self._pending_events.clear()
            self.requestCancelled.emit()
            reply.deleteLater()
            return
        self._read_stream_data(reply)
        try:
            text = self._decoder.decode(b"", final=True)
            for event_name, data_text in self._sse.feed(text):
                self._handle_sse_event(event_name, data_text)
            for event_name, data_text in self._sse.close():
                self._handle_sse_event(event_name, data_text)
            status = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute
            )
            if reply.error() != QNetworkReply.NetworkError.NoError or (status and status >= 400):
                raw = bytes(self._diagnostic_bytes).decode("utf-8", errors="replace")
                try:
                    details = json.loads(raw)
                    message = _error_message(details)
                except json.JSONDecodeError:
                    message = raw or reply.errorString()
                self._emit_failure(f"纯对话请求失败: {message or reply.errorString()}")
                return
            if self._response_emitted or self._failure_emitted:
                return
            fallback_text = "".join(self._completed_text_parts) or "".join(
                self._answer_deltas
            )
            self._response_emitted = True
            self.responseReady.emit(fallback_text, _extract_usage({}), {})
        except Exception as exc:
            self._emit_failure(f"纯对话响应解析失败: {exc}")
        finally:
            self._stream_timer.stop()
            self._sse = _SSEParser()
            reply.deleteLater()


class _SSEParser:
    def __init__(self) -> None:
        self._buffer = ""
        self._event_name = ""
        self._data_lines: list[str] = []

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buffer += text
        events: list[tuple[str, str]] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.endswith("\r"):
                line = line[:-1]
            self._consume_line(line, events)
        return events

    def close(self) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if self._buffer:
            self._consume_line(self._buffer.rstrip("\r"), events)
            self._buffer = ""
        self._dispatch(events)
        return events

    def _consume_line(
        self,
        line: str,
        events: list[tuple[str, str]],
    ) -> None:
        if not line:
            self._dispatch(events)
            return
        if line.startswith(":"):
            return
        if line.startswith("event:"):
            self._event_name = line[6:].lstrip()
            return
        if line.startswith("data:"):
            self._data_lines.append(line[5:].lstrip())

    def _dispatch(self, events: list[tuple[str, str]]) -> None:
        if self._data_lines:
            events.append((self._event_name, "\n".join(self._data_lines)))
        self._event_name = ""
        self._data_lines = []


def _event_item_id(
    data: dict[str, Any],
    item: dict[str, Any] | None = None,
) -> str:
    item = item or {}
    return str(
        data.get("item_id")
        or data.get("itemId")
        or item.get("id")
        or "reasoning"
    )


def _response_input_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if isinstance(content, list):
        return [
            dict(part)
            for part in content
            if isinstance(part, dict)
        ]
    return [{"type": "input_text", "text": str(content)}]


def _error_message(data: Any) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        for key in ("message", "detail"):
            if data.get(key):
                return str(data[key])
    return str(data)


def _extract_output_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_reasoning(data: dict[str, Any]) -> dict[str, str]:
    summaries: list[str] = []
    contents: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "reasoning":
            continue
        for entry in item.get("summary") or []:
            if isinstance(entry, dict):
                text = entry.get("text")
            else:
                text = entry
            if isinstance(text, str) and text.strip():
                summaries.append(text.strip())
        for entry in item.get("content") or []:
            if isinstance(entry, dict):
                text = entry.get("text")
            else:
                text = entry
            if isinstance(text, str) and text.strip():
                contents.append(text.strip())
    result: dict[str, str] = {}
    if summaries:
        result["reasoning_summary"] = "\n\n".join(summaries)
    if contents:
        result["reasoning_raw"] = "\n\n".join(contents)
    return result


def _extract_usage(raw: dict[str, Any]) -> dict[str, int]:
    details = raw.get("input_tokens_details") or {}
    input_tokens = int(raw.get("input_tokens") or 0)
    cached_tokens = int(details.get("cached_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or 0)
    total_tokens = int(raw.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": int(
            (raw.get("output_tokens_details") or {}).get("reasoning_tokens") or 0
        ),
        "total_tokens": total_tokens,
        "context_tokens": input_tokens + output_tokens,
    }
