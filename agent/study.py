"""Content-based study planning: semantic units supplied by the model, checked locally."""
from datetime import date, timedelta
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InspectArgs(StrictArgs):
    filename: Annotated[str, Field(min_length=1, max_length=200)]


class CsvArgs(StrictArgs):
    filename: Annotated[str, Field(min_length=1, max_length=200, description="library 内实际 CSV 文件名，无预设列名")]
    sample_rows: Annotated[int, Field(ge=1, le=10, description="返回样例记录数，其余记录仍纳入统计")]


class LearningUnit(StrictArgs):
    id: Annotated[str, Field(min_length=1, max_length=40)]
    title: Annotated[str, Field(min_length=1, max_length=120, description="完整知识主题，不能仅写第几页到第几页")]
    material: Annotated[str, Field(min_length=1, max_length=200)]
    sources: Annotated[list[str], Field(min_length=1, max_length=8, description="已实际读取的来源，可用 book.pdf:p1-p3 或 note.md:L1-L20")]
    objective: Annotated[str, Field(min_length=5, max_length=350, description="学完后能够完成的具体目标或检查任务")]
    difficulty: Literal["基础", "中等", "较难"]
    difficulty_reason: Annotated[str, Field(min_length=5, max_length=350, description="结合原文概念抽象性、证明、公式或数据操作说明难度与用时依据")]
    estimated_minutes: Annotated[int, Field(ge=5, le=480, description="模型根据内容估计的分钟数，不得按页数等比例分配")]
    prerequisites: Annotated[list[str], Field(max_length=20, description="必须先完成的其他单元 id；无则 []")]


class PlanArgs(StrictArgs):
    start_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    days: Annotated[int, Field(ge=1, le=14)]
    daily_minutes: Annotated[int, Field(ge=10, le=480)]
    materials: Annotated[list[str], Field(min_length=1, max_length=5)]
    scope: Literal["whole_material", "selected_topics"]
    units: Annotated[list[LearningUnit], Field(min_length=1, max_length=30)]


def schedule(args: PlanArgs) -> dict:
    start = date.fromisoformat(args.start_date)
    units = [unit.model_dump() for unit in args.units]
    ids = {u["id"] for u in units}
    if len(ids) != len(units):
        raise ValueError("学习单元 id 不能重复")
    for unit in units:
        if re.fullmatch(r"(?:阅读|学习|复习|教材|书籍|第|页|到|至|\d|[\s\-~—～、:：])+", unit["title"]):
            raise ValueError("学习单元标题不能只是页码区间，请按实际知识主题命名")
        if re.search(r"按页数|按页|每页.{0,8}分钟|页数[多少]+|平均分配|均分", unit["difficulty_reason"]):
            raise ValueError("难度和用时不能以页数均分为依据，请说明原文概念、证明、例题或数据操作的难度")
        if unit["material"] not in args.materials:
            raise ValueError("每个学习单元必须属于 materials 中选定的资料")
        if set(unit["prerequisites"]) - ids or unit["id"] in unit["prerequisites"]:
            raise ValueError("先修关系包含未知或自身单元")
    reached = set()
    while True:
        available = {u["id"] for u in units if set(u["prerequisites"]) <= reached}
        next_reached = reached | available
        if next_reached == reached:
            break
        reached = next_reached
    if reached != ids:
        raise ValueError("学习单元的先修关系存在循环，请修正")
    completed, plan = set(), []
    for offset in range(args.days):
        sessions, budget = [], args.daily_minutes
        while True:
            eligible = next((u for u in units if u["id"] not in completed
                             and set(u["prerequisites"]) <= completed
                             and u["estimated_minutes"] <= budget), None)
            if eligible is None:
                break
            sessions.append(eligible)
            completed.add(eligible["id"])
            budget -= eligible["estimated_minutes"]
        plan.append({"date": (start + timedelta(days=offset)).isoformat(),
                     "minutes": args.daily_minutes - budget, "sessions": sessions})
    unscheduled = []
    for u in units:
        if u["id"] in completed:
            continue
        reason = "计划容量不足，保留完整学习单元，未按剩余分钟强行切碎"
        if u["estimated_minutes"] > args.daily_minutes:
            reason = "完整单元超过单日预算；请按概念、例题、练习等自然目标细分后重新规划"
        elif set(u["prerequisites"]) - completed:
            reason = "先修单元尚未完成"
        unscheduled.append({**u, "reason": reason})
    return {"units": units, "plan": plan, "unscheduled": unscheduled,
            "total_estimated_minutes": sum(u["estimated_minutes"] for u in units),
            "capacity_minutes": args.days * args.daily_minutes,
            "scheduled_minutes": sum(d["minutes"] for d in plan),
            "estimate_note": "难度及用时为模型基于已读取内容的学习建议，并非资料中提供的客观时长。",
            "policy": "先修关系优先；按完整主题安排，单元不在定义、证明或例题中间任意截断；每日不超预算。"}
