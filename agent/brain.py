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
    """A deterministic test driver, not an LLM. Uses the real tool protocol and data."""

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        query = next(m["content"] for m in messages if m["role"] == "user")
        results = []
        names = {c["id"]: c["function"]["name"] for m in messages for c in m.get("tool_calls", [])}
        for m in messages:
            if m["role"] == "tool":
                results.append((names[m["tool_call_id"]], json.loads(m["content"])))
        want_plan = any(word in query for word in ["计划", "安排", "任务", "截止", "分钟", "plan", "schedule"])
        if not results:
            calls = [_call("search_materials", {"query": query[:200], "limit": 5}, 1)]
        elif not any(name == "read_material" for name, _ in results):
            search = results[0][1].get("data", {}).get("results", [])
            calls = [_call("read_material", {"filename": h["filename"], "start": max(1, h["start"] - (0 if h["kind"] == "page" else 3)),
                                            "count": 1 if h["kind"] == "page" else 30}, i + 2)
                     for i, h in enumerate(search[:2])]
            if not calls:
                return self._plan_or_answer(query, results, want_plan)
        else:
            return self._plan_or_answer(query, results, want_plan)
        return {"role": "assistant", "content": None, "tool_calls": calls}

    def _plan_or_answer(self, query, results, want_plan):
        if want_plan and not any(name == "analyze_reading_tasks" for name, _ in results):
            days_match = re.search(r"(?:未来|接下来|连续)?\s*(\d+)\s*天", query)
            time_match = re.search(r"(?:每天|每日)\s*(\d+)\s*分钟", query)
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", query)
            args = {"start_date": date_match[0] if date_match else datetime.now(BEIJING_TZ).date().isoformat(),
                    "days": min(14, max(1, int(days_match[1]))) if days_match else 3,
                    "daily_minutes": min(480, max(10, int(time_match[1]))) if time_match else 60}
            return {"role": "assistant", "content": None,
                    "tool_calls": [_call("analyze_reading_tasks", args, 10)]}
        lines = ["【规则演示 · 未调用大模型】以下内容直接摘自本地文件，排期由工具计算。"]
        reads = [r["data"] for name, r in results if name == "read_material" and r["ok"]]
        if reads:
            lines.extend(["", "## 资料摘录"])
            for read in reads:
                lines.append(f"\n### {read['filename']}")
                excerpts = [e for e in read["excerpts"] if e["text"].strip() and not e["text"].startswith("#")][:4]
                for e in excerpts:
                    lines.append(f"- {e['text'][:600]} [{e['source']}]")
        else:
            lines.append("\n未找到可读取的相关资料。请调整关键词或在 data/library 中加入资料。")
        for name, result in results:
            if not result["ok"]:
                lines.append(f"\n工具 {name} 失败：{result['error']}")
            if name != "analyze_reading_tasks" or not result["ok"]:
                continue
            data = result["data"]
            lines.extend(["", "## 阅读安排", f"剩余工作量 {data['remaining_minutes']} 分钟；计划容量 {data['capacity_minutes']} 分钟。"])
            lines.append(data["policy"] + "。")
            for day in data["plan"]:
                lines.append(f"\n{day['date']} · {day['minutes']} 分钟")
                for s in day["sessions"]:
                    lines.append(f"- {s['title']}：{s['minutes']} 分钟" + ("（已超过截止日期）" if s["past_due"] else "") + f" [{s['source']}]")
            overdue = [t for t in data["tasks"] if t["overdue_at_start"]]
            if overdue:
                lines.append("\n起始日期前已逾期：" + "、".join(t["title"] + f" [{t['source']}]" for t in overdue))
            if data["unscheduled"]:
                lines.append("\n尚未排完：" + "、".join(f"{t['task_id']} 剩余 {t['minutes']} 分钟" for t in data["unscheduled"]))
        lines.append("\n演示模式只按规则选取摘录和分配时间；自由语义理解与综合总结请切换真实模型。")
        return {"role": "assistant", "content": "\n".join(lines)}
