"""Read llama.cpp progress without treating a slow answer as a silent server.

Only complete answers reach the existing schema and research validators.
Progress logs contain timing and counts, never article text or model prose.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from symbiosis_model_output import ModelOutputError

STREAM_VERSION = "symbiosis_stream_v1"
READ_TIMEOUT_SECONDS = 90
MAX_REQUEST_SECONDS = 1200
MAX_CONTENT_CHARACTERS = 32000


def chat_completion(
    url: str,
    payload: dict[str, Any],
    *,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    max_seconds: float = MAX_REQUEST_SECONDS,
    progress_interval: float = 60,
) -> dict[str, Any]:
    started = time.monotonic()
    last_report = started
    characters = 0
    events = 0
    finish_reason = None
    response = None
    parts: list[str] = []
    usage: dict[str, Any] = {}
    timings: dict[str, Any] = {}

    def diagnostic(error_type: str = "") -> dict[str, Any]:
        result = {
            "stream_version": STREAM_VERSION,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "received_characters": characters,
            "stream_events": events,
            "finish_reason": finish_reason,
        }
        if error_type:
            result["error_type"] = error_type
        return result

    def fail(message: str, error_type: str = "InvalidModelStream") -> None:
        raise ModelOutputError(message, [diagnostic(error_type)])

    body = {**payload, "stream": True, "stream_options": {"include_usage": True},
            "return_progress": True, "sse_ping_interval": 15}
    try:
        response = requests.post(url, json=body, stream=True, timeout=(10, read_timeout))
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            # An explicit non-stream response remains subject to all original
            # result checks. This also supports small compatible model servers.
            result = response.json()
            if time.monotonic() - started >= max_seconds:
                fail("Model request exceeded its total runtime budget.", "ModelRuntimeLimit")
            if not isinstance(result, dict):
                fail("Model returned a non-object response.")
            return result
        if content_type != "text/event-stream":
            fail("Model did not return a supported response type.")

        # A one-byte read prevents Requests from buffering small SSE pings
        # while llama.cpp is still processing a long multilingual prompt.
        for raw_line in response.iter_lines(chunk_size=1):
            now = time.monotonic()
            if now - started >= max_seconds:
                fail("Model request exceeded its total runtime budget.", "ModelRuntimeLimit")
            if now - last_report >= progress_interval:
                print(f"Model progress: {int(now-started)} seconds, "
                      f"{characters} answer characters received.", flush=True)
                last_report = now
            if len(raw_line) > 131072:
                fail("Model stream frame exceeded its size limit.")
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                # Event names and IDs are SSE metadata, not answer text.
                if line.startswith(("event:", "id:", "retry:")):
                    continue
                fail("Model stream contained an invalid event.")
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                item = json.loads(data)
            except ValueError:
                fail("Model stream contained invalid JSON.")
            if not isinstance(item, dict) or item.get("error"):
                fail("Model stream returned an error or a non-object event.")
            events += 1
            if isinstance(item.get("usage"), dict):
                usage = item["usage"]
            if isinstance(item.get("timings"), dict):
                timings = item["timings"]
            choices = item.get("choices", [])
            if not isinstance(choices, list):
                fail("Model stream contained invalid choices.")
            for choice in choices:
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    fail("Model stream contained an unexpected choice.")
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    fail("Model stream contained an invalid answer delta.")
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        fail("Model stream contained non-text answer content.")
                    if finish_reason is not None and content:
                        fail("Model stream continued after its final answer.")
                    parts.append(content)
                    characters += len(content)
                    if characters > MAX_CONTENT_CHARACTERS:
                        fail("Model answer exceeded its size limit.")
                reason = choice.get("finish_reason")
                if reason is not None:
                    if not isinstance(reason, str) or reason not in {"stop", "length"}:
                        fail("Model stream ended without a supported final answer.")
                    finish_reason = reason
        if finish_reason is None:
            fail("Model stream ended before its completion marker.", "IncompleteModelStream")
        # A token-limit finish is passed through so response_result still
        # rejects it. Partial JSON can never be normalized into a finding.
        return {
            "choices": [{"message": {"content": "".join(parts)}, "finish_reason": finish_reason}],
            "usage": usage,
            "timings": timings,
            "stream_diagnostics": diagnostic(),
        }
    except ModelOutputError:
        raise
    except requests.RequestException as error:
        raise ModelOutputError("Model connection stopped before a complete answer.",
                               [diagnostic(type(error).__name__)]) from error
    except (ValueError, UnicodeError) as error:
        raise ModelOutputError("Model returned an invalid response encoding or JSON.",
                               [diagnostic(type(error).__name__)]) from error
    finally:
        if response is not None:
            response.close()
