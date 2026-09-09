"""Bounded OpenAI JSON requests shared by Observatory and Brief.

The selected model is fixed for a process (and exported for a workflow job).
Only token counts, IDs and timing enter the cost ledger; never source text.
Existing domain validators still decide whether an answer may be published.
"""
from __future__ import annotations

import argparse
import copy
import functools
import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VERSION = "aieo-openai-json-v1"
ENDPOINT = "https://api.openai.com/v1/chat/completions"
# USD / million tokens, verified 2026-09-08. Cached input is charged at the
# uncached rate here; Luna includes its documented possible cache-write premium.
RATES = {"gpt-5-nano": (0.05, 0.40), "gpt-5.6-luna": (0.25, 1.20)}


class AIError(RuntimeError):
    """Safe error text, without response bodies or authorization headers."""


class AIBudgetExceeded(TimeoutError):
    """Leave remaining work resumable when the configured job cap is reached."""


def resolve_policy(now=None, config=None):
    config = config or json.loads((ROOT / "config/ai-models.json").read_text())
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("Model selection requires a timezone-aware date")
    cutoff = datetime.fromisoformat(config["switch_at"].replace("Z", "+00:00"))
    policy = dict(config["after" if instant >= cutoff else "before"])
    provider = os.environ.get("AIEO_AI_PROVIDER", config["provider"])
    if provider not in {"openai", "local"}:
        raise ValueError("AIEO_AI_PROVIDER must be openai or local")
    policy["provider"] = provider
    policy["model"] = os.environ.get("AIEO_AI_MODEL") or policy["model"]
    if policy["model"] not in RATES:
        raise ValueError("Only the approved Nano and Luna model IDs are supported")
    for key, env in (("max_job_usd", "AIEO_AI_MAX_JOB_USD"),
                     ("max_job_requests", "AIEO_AI_MAX_JOB_REQUESTS"),
                     ("max_completion_tokens", "AIEO_AI_MAX_COMPLETION_TOKENS")):
        value = float(os.environ.get(env, config[key]))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a positive finite number")
        policy[key] = value if key == "max_job_usd" else int(value)
    if not 2048 <= policy["max_completion_tokens"] <= 16384:
        raise ValueError("Use 2048–16384 completion tokens, including reasoning")
    policy["switch_at"] = config["switch_at"]
    policy["revision"] = f"{policy['model']}:{policy['reasoning_effort']}:{VERSION}"
    return policy


@functools.lru_cache(maxsize=1)
def selected_policy():
    return resolve_policy()


def uses_openai():
    return selected_policy()["provider"] == "openai"


def identity():
    p = selected_policy()
    return {key: p[key] for key in ("provider", "model", "revision", "reasoning_effort")}


def require_key():
    if uses_openai() and not os.environ.get("OPENAI_API_KEY", "").strip():
        raise AIError("Add the OPENAI_API_KEY repository Actions secret before running AI generation.")


def strict_schema(schema):
    """Normalize our disjoint dimension unions for OpenAI's JSON Schema subset.

Every property is explicitly produced; domain schemas are validated again by
the caller, including the original oneOf rules. No prompt-only fallback.
"""
    value = copy.deepcopy(schema)
    def visit(node):
        if not isinstance(node, dict):
            return
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        for child in node.values():
            if isinstance(child, dict):
                visit(child)
            elif isinstance(child, list):
                for item in child:
                    visit(item)
    visit(value)
    return value


def ledger_path():
    return Path(os.environ.get("AIEO_AI_LEDGER", str(ROOT / ".ai-usage/job.json")))


@contextmanager
def ledger():
    import fcntl
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {"version": VERSION, "calls": []}
        yield state
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(temp, path)


def completion(messages, schema, *, name="aieo_reading", timeout=120):
    require_key()
    p = selected_policy()
    if not uses_openai():
        raise AIError("OpenAI completion called while local provider is selected")
    clean_messages = [{**m, "content": str(m["content"]).replace("/no_think", "")} for m in messages]
    payload = {"model": p["model"], "messages": clean_messages,
               "reasoning_effort": p["reasoning_effort"],
               "max_completion_tokens": p["max_completion_tokens"], "store": False,
               "response_format": {"type": "json_schema", "json_schema": {
                   "name": name, "strict": True, "schema": strict_schema(schema)}}}
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 196608:
        raise AIError("Input exceeds the bounded request size; retain complete evidence in smaller segments")
    input_rate, output_rate = RATES[p["model"]]
    # UTF-8 bytes provide a conservative upper bound for text tokenization;
    # add framing/schema overhead before reserving the maximum output cost.
    reserved = ((len(encoded) + 4096) * input_rate + p["max_completion_tokens"] * output_rate) / 1e6
    last = None
    for attempt in range(3):
        with ledger() as state:
            if state.get("fatal_error"):
                raise AIError(state["fatal_error"])
            if len(state["calls"]) >= p["max_job_requests"] or sum(c["cost_usd"] for c in state["calls"]) + reserved > p["max_job_usd"]:
                raise AIBudgetExceeded("AI job spending/request limit reached; saved work can be resumed")
            slot = len(state["calls"])
            state["calls"].append({"requested_model": p["model"], "cost_usd": reserved,
                                  "status": "reserved", "prompt_sha256": hashlib.sha256(encoded).hexdigest()})
        start = time.monotonic()
        try:
            response = requests.post(ENDPOINT, json=payload,
                headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"].strip()},
                timeout=(10, min(180, max(1, timeout))), allow_redirects=False)
        except requests.RequestException:
            # The request may have been billed. Keep its reservation and avoid
            # an automatic duplicate after an ambiguous transport failure.
            raise AIError("OpenAI connection failed; request reservation retained") from None
        if response.status_code != 200:
            code = ""
            try:
                code = str((response.json().get("error") or {}).get("code") or "")
            except (ValueError, AttributeError):
                pass
            transient = response.status_code in {429, 500, 502, 503, 504} and code != "insufficient_quota"
            safe_error = f"OpenAI HTTP {response.status_code}. Check API access, quota and the configured model."
            with ledger() as state:
                state["calls"][slot]["status"] = f"http_{response.status_code}"
                # A 4xx response did not generate an answer; 5xx is uncertain.
                if 400 <= response.status_code < 500:
                    state["calls"][slot]["cost_usd"] = 0
                if not transient:
                    state["fatal_error"] = safe_error
            if transient and attempt < 2:
                try:
                    delay = min(15, max(1, float(response.headers.get("Retry-After", 2 ** (attempt + 1)))))
                except ValueError:
                    delay = 2 ** (attempt + 1)
                time.sleep(delay)
                last = safe_error
                continue
            raise AIError(safe_error)
        try:
            result = response.json()
        except ValueError:
            raise AIError("OpenAI returned an invalid response; request reservation retained") from None
        usage = result.get("usage") or {}
        actual = reserved
        if all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens")):
            actual = (usage["prompt_tokens"] * input_rate + usage["completion_tokens"] * output_rate) / 1e6
        audit = {"provider": "openai", "requested_model": p["model"], "returned_model": result.get("model"),
                 "revision": p["revision"], "reasoning_effort": p["reasoning_effort"],
                 "request_id": response.headers.get("x-request-id"), "usage": usage,
                 "cost_usd": actual, "elapsed_seconds": round(time.monotonic() - start, 3), "status": "completed"}
        with ledger() as state:
            state["calls"][slot].update(audit)
        choice = (result.get("choices") or [{}])[0]
        if (choice.get("message") or {}).get("refusal"):
            raise AIError("OpenAI declined this request; no classification or draft was accepted")
        if choice.get("finish_reason") != "stop":
            raise AIError("OpenAI answer was unfinished; no classification or draft was accepted")
        result["aieo_ai"] = audit
        return result
    raise AIError(last or "OpenAI request failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    p = selected_policy()
    if args.preflight:
        require_key()
    if args.export and os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a") as handle:
            handle.write(f"AIEO_AI_PROVIDER={p['provider']}\nAIEO_AI_MODEL={p['model']}\n")
    if args.report and ledger_path().exists():
        data = json.loads(ledger_path().read_text())
        info = {"requests": len(data["calls"]), "estimated_or_reserved_usd": round(sum(c["cost_usd"] for c in data["calls"]), 6), "job_limit_usd": p["max_job_usd"]}
        print(json.dumps(info))
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
                handle.write(f"\nAI usage: **{info['requests']} requests**, estimated/reserved **${info['estimated_or_reserved_usd']:.4f}**; job cap **${p['max_job_usd']:.2f}**. API billing is authoritative.\n")
    else:
        print(json.dumps(p, indent=2))


if __name__ == "__main__":
    main()
