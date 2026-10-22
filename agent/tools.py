"""工具边界、Pydantic 参数模型、自动生成的 Function Calling JSON Schema。"""
import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchArgs(Arguments):
    query: Annotated[str, Field(max_length=200, description="中文或英文关键词；空字符串列出资料")]
    limit: Annotated[int, Field(ge=1, le=10, description="最多返回多少条资料，建议 5")]


class ReadArgs(Arguments):
    filename: Annotated[str, Field(min_length=1, max_length=200, description="检索返回的 library 相对文件名")]
    start: Annotated[int, Field(ge=1, le=10000, description="文本起始行或 PDF 起始页，从 1 开始")]
    count: Annotated[int, Field(ge=1, le=80, description="读取行数；PDF 最多 3 页")]


class TaskArgs(Arguments):
    start_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="计划起始日期 YYYY-MM-DD")]
    days: Annotated[int, Field(ge=1, le=14, description="安排天数，1 到 14")]
    daily_minutes: Annotated[int, Field(ge=10, le=480, description="每天可用阅读分钟数，10 到 480")]


TOOL_META = {
    "search_materials": (SearchArgs, "检索本地 library 中的 Markdown、TXT、PDF 课程资料，返回带定位的片段；不是联网搜索。"),
    "read_material": (ReadArgs, "读取指定课程资料的原文行或 PDF 页，返回可引用来源。资料问答应先检索再阅读。"),
    "analyze_reading_tasks": (TaskArgs, "解析本地 reading_tasks.csv，统计剩余工作量，按截止日期和优先级分配每日阅读时间。"),
}


class ToolRegistry:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.library = (self.data_dir / "library").resolve()

    def schemas(self) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": name, "description": description, "strict": True,
                "parameters": model.model_json_schema(),
            }}
            for name, (model, description) in TOOL_META.items()
        ]

    def execute(self, name: str, raw_args: str) -> dict:
        if name not in TOOL_META:
            return {"ok": False, "error": "未知工具"}
        if len(raw_args) > 4000:
            return {"ok": False, "error": "参数过长"}
        try:
            args = TOOL_META[name][0].model_validate_json(raw_args)
            result = getattr(self, name)(**args.model_dump())
            return {"ok": True, "data": result}
        except ValidationError as exc:
            errors = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                      for e in exc.errors(include_input=False, include_url=False)]
            return {"ok": False, "error": "参数校验失败", "details": errors}
        except (ValueError, OSError, csv.Error) as exc:
            # Do not return absolute paths or arbitrary exception text to the model.
            return {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "资料读取失败，请检查文件格式和权限"}
        except Exception:
            return {"ok": False, "error": "无法解析资料，PDF 可能损坏或加密"}

    def _safe_file(self, filename: str) -> Path:
        target = (self.library / filename).resolve()
        if not target.is_relative_to(self.library) or target == self.library:
            raise ValueError("只能访问 data/library 内的资料")
        if target.suffix.lower() not in {".md", ".txt", ".pdf"}:
            raise ValueError("仅支持 Markdown、TXT、PDF")
        if not target.is_file():
            raise ValueError("资料不存在，请先检索文件名")
        if target.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("单份资料不能超过 5 MB")
        return target

    def _units(self, path: Path) -> list[str]:
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError("不支持加密 PDF，请先解密")
            if len(reader.pages) > 100:
                raise ValueError("PDF 最多支持 100 页，请拆分")
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            if not any(pages):
                raise ValueError("PDF 无可提取文字，扫描件需要先 OCR")
            return pages
        return path.read_text(encoding="utf-8-sig").splitlines()

    def inventory(self) -> list[str]:
        if not self.library.is_dir():
            return []
        files = []
        for path in sorted(self.library.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".pdf"}:
                relative = path.relative_to(self.library).as_posix()
                try:
                    self._safe_file(relative)
                    files.append(relative)
                except ValueError:
                    continue
                if len(files) == 80:
                    break
        return files

    def search_materials(self, query: str, limit: int) -> dict:
        # English words + Chinese bigrams provide a small, offline lexical retriever.
        query = query.casefold().strip()
        terms = set(re.findall(r"[a-z0-9_]+", query))
        for segment in re.findall(r"[\u4e00-\u9fff]+", query):
            terms.update(segment[i:i + 2] for i in range(max(1, len(segment) - 1)))
        hits, skipped = [], []
        files = self.inventory()
        for filename in files:
            path = self._safe_file(filename)
            try:
                units = self._units(path)
            except Exception:
                skipped.append(filename)
                continue
            best = None
            for index, unit in enumerate(units):
                score = sum(1 for t in terms if t in (filename + " " + unit).casefold())
                if query and score == 0:
                    continue
                kind = "page" if path.suffix.lower() == ".pdf" else "line"
                # One best hit per document keeps search diverse.
                hit = {"filename": filename, "kind": kind, "start": index + 1,
                       "source": f"{filename}:p{index + 1}" if kind == "page" else f"{filename}:L{index + 1}",
                       "score": score, "snippet": unit[:900]}
                if best is None or score > best["score"]:
                    best = hit
            if best:
                hits.append(best)
        hits.sort(key=lambda h: (-h["score"], h["filename"]))
        return {"query": query, "results": hits[:limit], "scanned_files": len(files),
                "skipped_files": skipped, "inventory_limit": 80}

    def read_material(self, filename: str, start: int, count: int) -> dict:
        path = self._safe_file(filename)
        units = self._units(path)
        is_pdf = path.suffix.lower() == ".pdf"
        if is_pdf and count > 3:
            raise ValueError("一次最多读取 3 页 PDF")
        if start > len(units):
            raise ValueError(f"起始位置超出资料范围，共 {len(units)} 个单位")
        selected, chars = [], 0
        for i in range(start - 1, min(start - 1 + count, len(units))):
            text = units[i][:max(0, 12000 - chars)]
            selected.append({"source": f"{filename}:p{i + 1}" if is_pdf else f"{filename}:L{i + 1}", "text": text})
            chars += len(text)
            if chars >= 12000:
                break
        return {"filename": filename, "unit": "page" if is_pdf else "line", "total": len(units),
                "excerpts": selected, "truncated": chars >= 12000,
                "next_start": start + len(selected) if start + len(selected) <= len(units) else None}

    def analyze_reading_tasks(self, start_date: str, days: int, daily_minutes: int) -> dict:
        start = date.fromisoformat(start_date)
        path = (self.data_dir / "reading_tasks.csv").resolve()
        if not path.is_relative_to(self.data_dir) or not path.is_file():
            raise ValueError("缺少 data/reading_tasks.csv 或路径不合法")
        if path.stat().st_size > 100_000:
            raise ValueError("CSV 不能超过 100 KB")
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            required = {"id", "title", "material", "due_date", "estimated_minutes", "priority", "status"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError("CSV 缺少必要列：" + ", ".join(sorted(required)))
            rows, previous_line = [], 1
            for row in reader:
                rows.append((previous_line + 1, row))
                previous_line = reader.line_num
        if len(rows) > 100:
            raise ValueError("CSV 最多支持 100 条任务")
        tasks, ids = [], set()
        for line, row in rows:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["due_date"]):
                    raise ValueError()
                due = date.fromisoformat(row["due_date"])
                minutes, priority = int(row["estimated_minutes"]), int(row["priority"])
                status = row["status"].strip()
                if not row["id"] or row["id"] in ids or not row["title"]:
                    raise ValueError()
                if len(row["id"]) > 64 or len(row["title"]) > 200 or len(row["material"]) > 200:
                    raise ValueError()
                if not 1 <= minutes <= 10000 or not 1 <= priority <= 3 or status not in {"todo", "doing", "done"}:
                    raise ValueError()
                ids.add(row["id"])
            except (ValueError, TypeError, KeyError):
                raise ValueError(f"CSV 第 {line} 行非法：检查唯一 id、日期、分钟数、优先级 1-3 和状态 todo/doing/done") from None
            if status == "done":
                continue
            tasks.append({"id": row["id"], "title": row["title"], "material": row["material"],
                          "due_date": due.isoformat(), "estimated_minutes": minutes, "priority": priority,
                          "status": status, "overdue_at_start": due < start,
                          "source": f"reading_tasks.csv:{line}"})
        tasks.sort(key=lambda t: (t["due_date"], t["priority"], t["id"]))
        remaining = {t["id"]: t["estimated_minutes"] for t in tasks}
        plan = []
        for offset in range(days):
            day, budget, sessions = start + timedelta(days=offset), daily_minutes, []
            for task in tasks:
                spent = min(remaining[task["id"]], budget)
                if spent:
                    sessions.append({"task_id": task["id"], "title": task["title"], "minutes": spent,
                                     "past_due": day.isoformat() > task["due_date"], "source": task["source"]})
                    budget -= spent
                    remaining[task["id"]] -= spent
            plan.append({"date": day.isoformat(), "minutes": daily_minutes - budget, "sessions": sessions})
        return {"tasks": tasks, "remaining_minutes": sum(t["estimated_minutes"] for t in tasks),
                "capacity_minutes": days * daily_minutes, "daily_minutes": daily_minutes, "plan": plan,
                "unscheduled": [{"task_id": key, "minutes": value} for key, value in remaining.items() if value],
                "policy": "截止日期优先，同日 priority 数字越小越优先；可跨天拆分；doing 的分钟数视为剩余用时"}
