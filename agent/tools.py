"""工具边界、Pydantic 参数模型、自动生成的 Function Calling JSON Schema。"""
import csv
import json
import re
from collections import Counter
import math
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader
from .study import InspectArgs, CsvArgs, PlanArgs, schedule


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchArgs(Arguments):
    query: Annotated[str, Field(max_length=200, description="中文或英文关键词；空字符串列出资料")]
    limit: Annotated[int, Field(ge=1, le=10, description="最多返回多少条资料，建议 5")]


class ReadArgs(Arguments):
    filename: Annotated[str, Field(min_length=1, max_length=200, description="检索返回的 library 相对文件名")]
    start: Annotated[int, Field(ge=1, le=10000, description="文本起始行或 PDF 起始页，从 1 开始")]
    count: Annotated[int, Field(ge=1, le=80, description="读取行数；PDF 最多 3 页")]


TOOL_META = {
    "search_materials": (SearchArgs, "检索本地 library 中的 Markdown、TXT、PDF、CSV 资料，返回带定位的片段；不是联网搜索。"),
    "read_material": (ReadArgs, "读取指定课程资料的原文行或 PDF 页，返回可引用来源。资料问答应先检索再阅读。"),
    "inspect_material": (InspectArgs, "查看资料结构、页数或章节线索。只用于导航，不能代替阅读原文；发现乱码会报错。"),
    "analyze_csv": (CsvArgs, "分析任意真实 CSV 数据，返回列类型、缺失值、数值统计、类别分布和样例；不要求任务表格式。"),
    "create_study_plan": (PlanArgs, "将模型从已读资料中提炼的完整主题、目标、难度及先修关系编排成学习计划。检查来源是否真实读取、全文覆盖和每日预算；不接收预制任务 CSV，不按页数均分。"),
}


class ToolRegistry:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.library = (self.data_dir / "library").resolve()
        self._cache = {}
        self._covered = {}

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
        if len(raw_args) > (64000 if name == "create_study_plan" else 4000):
            return {"ok": False, "error": "参数过长"}
        try:
            args = TOOL_META[name][0].model_validate_json(raw_args)
            result = self.create_study_plan(args) if name == "create_study_plan" else getattr(self, name)(**args.model_dump())
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
        if target.suffix.lower() not in {".md", ".txt", ".pdf", ".csv"}:
            raise ValueError("仅支持 Markdown、TXT、PDF、CSV")
        if not target.is_file():
            raise ValueError("资料不存在，请先检索文件名")
        if target.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("单份资料不能超过 5 MB")
        return target

    def _units(self, path: Path) -> list[str]:
        key = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        if key in self._cache:
            return self._cache[key]
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError("不支持加密 PDF，请先解密")
            if len(reader.pages) > 100:
                raise ValueError("PDF 最多支持 100 页，请拆分")
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            if not any(pages):
                raise ValueError("PDF 无可提取文字，扫描件需要先 OCR")
            text = "".join(pages)
            controls = sum(ord(c) < 32 and c not in "\n\r\t" for c in text)
            if controls / max(1, len(text)) > 0.008 or text.count("\ufffd") / max(1, len(text)) > 0.01:
                raise ValueError("PDF 文字提取存在乱码或字体编码异常，无法可靠理解内容。请先 OCR 或换用可正常提取的文本版本。")
            self._cache[key] = pages
            return pages
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        self._cache[key] = lines
        return lines

    def inventory(self) -> list[str]:
        if not self.library.is_dir():
            return []
        files = []
        for path in sorted(self.library.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".pdf", ".csv"}:
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
            except Exception as exc:
                skipped.append({"filename": filename, "reason": str(exc) if isinstance(exc, ValueError) else "资料解析失败"})
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
            if len(text) == len(units[i]):
                self._covered.setdefault(filename, set()).add(i + 1)
            chars += len(text)
            if chars >= 12000:
                break
        return {"filename": filename, "unit": "page" if is_pdf else "line", "total": len(units),
                "excerpts": selected, "truncated": chars >= 12000,
                "next_start": start + len(selected) if start + len(selected) <= len(units) else None}

    def inspect_material(self, filename: str) -> dict:
        path = self._safe_file(filename)
        units = self._units(path)
        pdf = path.suffix.lower() == ".pdf"
        if pdf:
            structure = [{"start": i + 1, "source": f"{filename}:p{i + 1}",
                          "characters": len(text), "preview": text[:180]}
                         for i, text in enumerate(units)]
        else:
            structure = [{"start": i + 1, "source": f"{filename}:L{i + 1}", "heading": text[:180]}
                         for i, text in enumerate(units)
                         if re.match(r"^#{1,6}\s|^第.+[章节]|^\d+(?:\.\d+)+\s", text)]
        return {"filename": filename, "unit": "page" if pdf else "line", "total": len(units),
                "structure": structure[:100], "read_positions": sorted(self._covered.get(filename, set())),
                "note": "导航信息，不表示已阅读全文。先读取相关原文；整份资料计划需要完整阅读或分析覆盖。"}

    def analyze_csv(self, filename: str, sample_rows: int) -> dict:
        path = self._safe_file(filename)
        if path.suffix.lower() != ".csv":
            raise ValueError("analyze_csv 只接受 CSV 资料")
        if path.stat().st_size > 2_000_000:
            raise ValueError("CSV 分析最多支持 2 MB，请拆分")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames
            if not columns or len(columns) > 30 or len(columns) != len(set(columns)) or any(not c.strip() for c in columns):
                raise ValueError("CSV 需有非空且不重复的表头，最多30列")
            rows = []
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f"CSV 第 {reader.line_num} 行的列数与表头不一致")
                rows.append(row)
                if len(rows) > 10000:
                    raise ValueError("CSV 分析最多支持10000条记录")
        if not rows:
            raise ValueError("CSV 没有数据记录")
        stats = []
        for column in columns:
            values = [r[column].strip() for r in rows if r[column].strip()]
            numbers = []
            for v in values:
                try:
                    number = float(v)
                    if not math.isfinite(number):
                        break
                    numbers.append(number)
                except ValueError:
                    break
            item = {"column": column, "missing": len(rows) - len(values), "distinct": len(set(values))}
            if values and len(numbers) == len(values):
                item.update({"type": "numeric", "min": min(numbers), "max": max(numbers),
                             "mean": math.fsum(number / len(numbers) for number in numbers)})
            else:
                item.update({"type": "text", "top_values": [(v[:120], n) for v, n in Counter(values).most_common(8)]})
            stats.append(item)
        total = len(self._units(path))
        self._covered[filename] = set(range(1, total + 1))
        source = f"{filename}:L1-L{total}"
        return {"filename": filename, "source": source, "row_count": len(rows), "columns": stats,
                "sample": [{key: value[:400] for key, value in row.items()} for row in rows[:sample_rows]],
                "coverage": "统计扫描了全部记录；返回的是统计及样例，不是预制学习任务。"}

    def _source_positions(self, filename: str, source: str) -> set[int]:
        prefix = "p" if Path(filename).suffix.lower() == ".pdf" else "L"
        match = re.fullmatch(re.escape(filename) + ":" + prefix + r"(\d+)(?:-" + prefix + r"?(\d+))?", source)
        if not match:
            raise ValueError("学习单元来源必须是实际文件的页码或行号，例如 book.pdf:p1-p3")
        start, end = int(match[1]), int(match[2] or match[1])
        total = len(self._units(self._safe_file(filename)))
        if not 1 <= start <= end <= total:
            raise ValueError("学习单元引用范围超出实际资料")
        positions = set(range(start, end + 1))
        if not positions <= self._covered.get(filename, set()):
            raise ValueError("学习单元引用了尚未读取或分析的内容，请先调用 read_material / analyze_csv")
        return positions

    def create_study_plan(self, args: PlanArgs) -> dict:
        result = schedule(args)
        represented = {name: set() for name in args.materials}
        for unit in args.units:
            for source in unit.sources:
                represented[unit.material].update(self._source_positions(unit.material, source))
        coverage = []
        for name in args.materials:
            units = self._units(self._safe_file(name))
            needed = {i + 1 for i, text in enumerate(units) if text.strip()}
            missing = needed - represented[name]
            if args.scope == "whole_material" and missing:
                raise ValueError(f"整份资料计划尚未完整覆盖 {name}，缺少位置 {sorted(missing)[:40]}；请继续读取并补充内容单元。")
            coverage.append({"filename": name, "total_positions": len(needed),
                             "represented_positions": len(represented[name] & needed),
                             "uncovered_positions": sorted(missing), "scope": args.scope})
        result["coverage"] = coverage
        return result

