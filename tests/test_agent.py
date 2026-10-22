import asyncio
import json
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from agent.app import create_app
from agent.brain import LiveBrain
from agent.config import Settings
from agent.runner import AgentRunner


def test_demo_end_to_end(sample_data):
    result = asyncio.run(AgentRunner(Settings(data_dir=sample_data)).run("阅读 Agent 资料，安排未来3天计划，每天60分钟，从2026-10-08开始"))
    assert not result["error"]
    assert result["mode"] == "demo"
    assert {t["tool"] for t in result["traces"]} == {"search_materials", "read_material", "analyze_reading_tasks"}
    assert "未调用大模型" in result["answer"]
    assert "35 分钟" in result["answer"]
    assert "reading_tasks.csv:2" in result["answer"]


def test_live_function_call_wire_and_error_recovery(sample_data):
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert all(t["function"]["strict"] for t in payload["tools"])
        assert "当前北京时间：" in payload["messages"][0]["content"]
        assert "UTC+8" in payload["messages"][0]["content"]
        assert "Asia/Shanghai" not in payload["messages"][0]["content"]
        if len(requests) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "bad", "type": "function", "function": {"name": "read_material", "arguments": '{"filename":"../../.env","start":1,"count":1}'}},
                {"id": "find", "type": "function", "function": {"name": "search_materials", "arguments": '{"query":"Agent","limit":2}'}}
            ]}
        elif len(requests) == 2:
            tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
            assert [m["tool_call_id"] for m in tool_messages] == ["bad", "find"]
            assert not json.loads(tool_messages[0]["content"])["ok"]
            assert json.loads(tool_messages[1]["content"])["ok"]
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "read", "type": "function", "function": {"name": "read_material", "arguments": '{"filename":"01_agent_basics.md","start":9,"count":5}'}}
            ]}
        else:
            read = json.loads(payload["messages"][-1]["content"])
            assert read["ok"] and read["data"]["excerpts"]
            message = {"role": "assistant", "content": "应用执行工具后，通过 tool_call_id 回传结果。[01_agent_basics.md:L12]"}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})
    settings = Settings(data_dir=sample_data, mode="live", api_key="test-only-key", model="mock-model")
    result = asyncio.run(AgentRunner(settings, LiveBrain(settings, httpx.MockTransport(respond))).run("解释 Agent 工具调用"))
    assert not result["error"] and len(requests) == 3
    assert result["created_at"].endswith("+08:00")
    assert len(result["traces"]) == 3
    assert result["traces"][0]["result"]["ok"] is False


def test_live_missing_credentials():
    result = asyncio.run(AgentRunner(Settings(mode="live")).run("解释 Agent"))
    assert result["error"] and not result["traces"]
    assert "LLM_API_KEY" in result["error"]


def test_no_tool_answer_is_not_accepted():
    class NoTools:
        async def complete(self, messages, tools):
            return {"role": "assistant", "content": "invented"}
    result = asyncio.run(AgentRunner(replace(Settings(), max_rounds=2), NoTools()).run("问题"))
    assert result["error"] and result["answer"] == ""


def test_loop_limit(sample_data):
    class Forever:
        async def complete(self, messages, tools):
            return {"role": "assistant", "content": None, "tool_calls": [
                {"id": "x", "type": "function", "function": {"name": "search_materials", "arguments": '{"query":"Agent","limit":1}'}}
            ]}
    result = asyncio.run(AgentRunner(Settings(data_dir=sample_data, max_calls=1), Forever()).run("问题"))
    assert "调用次数上限" in result["error"]
    assert len(result["traces"]) == 1


def test_api_health_run_and_validation(sample_data):
    with TestClient(create_app(Settings(data_dir=sample_data))) as client:
        assert client.get("/").status_code == 200
        health = client.get("/api/health").json()
        assert len(health["tools"]) == 3 and len(health["materials"]) >= 3
        assert "api_key" not in json.dumps(health)
        result = client.post("/api/run", json={"query": "解释 Function Calling", "mode": "demo"})
        assert result.status_code == 200 and result.json()["traces"]
        assert client.post("/api/run", json={"query": "", "mode": "demo"}).status_code == 422
        assert client.post("/api/run", json={"query": "   ", "mode": "demo"}).status_code == 422
        assert client.post("/api/run", json={"query": "x", "mode": "bad"}).status_code == 422
        assert client.post("/api/run", json={"query": "x", "mode": "live"}).status_code == 502


def test_provider_errors_are_sanitized():
    settings = Settings(mode="live", api_key="secret-sentinel", model="test")
    def fail(request):
        return httpx.Response(401, text="secret-sentinel private body")
    result = asyncio.run(AgentRunner(settings, LiveBrain(settings, httpx.MockTransport(fail))).run("x"))
    assert "HTTP 401" in result["error"]
    assert "secret-sentinel" not in json.dumps(result)
