import json
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from .config import Settings

BEIJING_TZ = timezone(timedelta(hours=8), name="北京时间")


class AgentError(Exception):
    """A safe, user-facing agent error."""


class Brain(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict]) -> dict: ...


class LiveBrain:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        if not self.settings.live_ready:
            raise AgentError("真实模型尚未配置：请在 .env 填写 LLM_API_KEY、LLM_MODEL 和 LLM_BASE_URL 后重启。")
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout, transport=self.transport,
                                         follow_redirects=False) as client:
                response = await client.post(
                    self.settings.base_url + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.api_key}"},
                    json={"model": self.settings.model, "messages": messages, "tools": tools,
                          "tool_choice": "auto"},
                )
            if response.status_code >= 300:
                raise AgentError(f"模型 API 返回 HTTP {response.status_code}。请检查密钥、模型名、余额及工具调用支持。")
            body = response.json()
            choice = body["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise AgentError("模型输出被截断或过滤，本次任务未完成，请缩小问题范围。")
            raw = choice["message"]
            message = {"role": "assistant", "content": raw.get("content")}
            if raw.get("tool_calls"):
                calls = raw["tool_calls"]
                if not isinstance(calls, list) or len(calls) > 24:
                    raise ValueError()
                seen = set()
                for call in calls:
                    if (call.get("type") != "function" or not isinstance(call.get("id"), str)
                            or call["id"] in seen or not call["id"]
                            or not isinstance(call["function"]["name"], str)
                            or not isinstance(call["function"]["arguments"], str)):
                        raise ValueError()
                    seen.add(call["id"])
                message["tool_calls"] = calls
            elif not isinstance(message["content"], str) or not message["content"].strip():
                raise ValueError()
            return message
        except httpx.TimeoutException:
            raise AgentError("模型请求超时，请稍后重试或提高 LLM_TIMEOUT。") from None
        except httpx.RequestError:
            raise AgentError("无法连接模型服务，请检查 LLM_BASE_URL 和网络。") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AgentError("模型服务未返回标准 Chat Completions 消息，请确认该服务支持 Function Calling。") from None


def _call(name: str, args: dict, number: int) -> dict:
    return {"id": f"demo_call_{number}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


class DemoBrain:
    """Offline context demo. Semantic planning is deliberately left to the real model."""

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        query = next(m["content"] for m in messages if m["role"] == "user")
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            return {"role": "assistant", "content": None, "tool_calls": [
                _call("search_materials", {"query": query[:200], "limit": 5}, 1)]}
        if len(results) == 1:
            hits = results[0].get("data", {}).get("results", [])
            if "csv" in query.casefold():
                hits = [hit for hit in hits if hit["filename"].lower().endswith(".csv")]
            calls = []
            for i, hit in enumerate(hits[:2], 2):
                if hit["filename"].lower().endswith(".csv"):
                    calls.append(_call("analyze_csv", {"filename": hit["filename"], "sample_rows": 3}, i))
                else:
                    calls.append(_call("read_material", {"filename": hit["filename"], "start": max(1, hit["start"] - (0 if hit["kind"] == "page" else 3)), "count": 1 if hit["kind"] == "page" else 30}, i))
            if calls:
                return {"role": "assistant", "content": None, "tool_calls": calls}
        lines = ["【规则演示 · 未调用大模型】仅展示实际资料和数据分析，不生成内容难度判断或学习计划。"]
        for result in results[1:]:
            if not result["ok"]:
                lines.append(result["error"])
                continue
            data = result["data"]
            if "row_count" in data:
                lines.append(f"\n## {data['filename']}\n共 {data['row_count']} 条记录。[{data['source']}]")
                for col in data["columns"]:
                    lines.append(f"- {col['column']}：{col['type']}，缺失 {col['missing']}，不同值 {col['distinct']}")
            else:
                lines.append(f"\n## {data['filename']}")
                for e in [e for e in data["excerpts"] if e["text"].strip()][:5]:
                    lines.append(f"- {e['text'][:500]} [{e['source']}]")
        if len(results) == 1:
            lines.append("未找到可读取的相关资料。")
        lines.append("\n请切换真实模型，让模型根据原文提炼主题、先修关系和难度，并调用规划工具。")
        return {"role": "assistant", "content": "\n".join(lines)}
