import asyncio
import json
import time
import re
from datetime import datetime
from uuid import uuid4

from .brain import AgentError, Brain, DemoBrain, LiveBrain, BEIJING_TZ
from .config import Settings
from .prompts import SYSTEM_PROMPT
from .tools import ToolRegistry


class AgentRunner:
    def __init__(self, settings: Settings, brain: Brain | None = None):
        self.settings = settings
        self.registry = ToolRegistry(settings.data_dir)
        self.brain = brain or (DemoBrain() if settings.mode == "demo" else LiveBrain(settings))

    async def run(self, query: str) -> dict:
        now = datetime.now(BEIJING_TZ)
        run_id, started, traces = uuid4().hex[:12], time.perf_counter(), []
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + f"\n当前北京时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（UTC+8）。"},
            {"role": "user", "content": query},
        ]
        successful = 0
        study_plan = None
        needs_plan = self.settings.mode == "live" and bool(re.search(r"计划|规划|安排|学习路径|schedule|study\s*plan", query, re.I))

        def finish(answer: str, error: str | None = None):
            return {"run_id": run_id, "mode": self.settings.mode,
                    "model": "规则驱动（无 LLM）" if self.settings.mode == "demo" else self.settings.model,
                    "created_at": now.isoformat(), "query": query, "answer": answer,
                    "traces": traces, "study_plan": study_plan,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000), "error": error}

        try:
            for round_number in range(1, self.settings.max_rounds + 1):
                message = await self.brain.complete(messages, self.registry.schemas())
                calls = message.get("tool_calls", [])
                if not calls:
                    failures = [t["result"]["error"] for t in traces if not t["result"]["ok"]]
                    if failures and (not successful or (needs_plan and study_plan is None)):
                        raise AgentError("无法基于当前资料完成可靠回答或学习计划。" + failures[-1])
                    if successful:
                        if needs_plan and study_plan is None:
                            messages.append(message)
                            messages.append({"role": "user", "content": "本次要求学习计划。请先阅读/分析原文，按完整主题、难度和先修关系调用 create_study_plan；不要直接编造日程。"})
                            continue
                        return finish(message.get("content") or "模型未生成回答。")
                    messages.append(message)
                    messages.append({"role": "user", "content": "请先成功调用至少一个工具获取实际上下文。不要凭基础知识回答。"})
                    continue
                if len(traces) + len(calls) > self.settings.max_calls:
                    raise AgentError("已达到工具调用次数上限，请缩小问题范围。")
                messages.append(message)
                for call in calls:
                    name, raw_args = call["function"]["name"], call["function"]["arguments"]
                    tick = time.perf_counter()
                    result = await asyncio.to_thread(self.registry.execute, name, raw_args)
                    successful += int(result["ok"])
                    if name == "create_study_plan" and result["ok"]:
                        study_plan = result["data"]
                    try:
                        args = json.loads(raw_args)
                    except ValueError:
                        args = {"invalid_json": raw_args[:200]}
                    traces.append({"round": round_number, "call_id": call["id"], "tool": name,
                                   "arguments": args, "result": result,
                                   "elapsed_ms": round((time.perf_counter() - tick) * 1000)})
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(result, ensure_ascii=False)})
            raise AgentError("已达到模型编排轮次上限，本次任务未完成，请缩小问题范围。")
        except AgentError as exc:
            return finish("", str(exc))
