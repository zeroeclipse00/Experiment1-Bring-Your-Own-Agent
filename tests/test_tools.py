import json
from types import SimpleNamespace
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from agent.tools import ToolRegistry


@pytest.fixture
def registry(sample_data):
    return ToolRegistry(sample_data)


def test_search_read_sources(registry):
    filename = registry.search_materials("Function Calling", 5)["results"][0]["filename"]
    read = registry.read_material(filename, 1, 30)
    assert any("tool_call_id" in e["text"] for e in read["excerpts"])


@pytest.mark.parametrize("name,args", [
    ("read_material", {"filename": "../../.env", "start": 1, "count": 1}),
    ("read_material", {"filename": "missing.md", "start": 1, "count": 1}),
    ("read_material", {"filename": "01_agent_basics.md", "start": 0, "count": 1}),
    ("read_material", {"filename": "01_agent_basics.md", "start": "1", "count": 1}),
    ("search_materials", {"query": "x", "limit": 100}),
    ("search_materials", {"query": "x", "limit": 1, "unexpected": "x"}),
    ("analyze_csv", {"filename": "../../private.csv", "sample_rows": 3}),
])
def test_reject_invalid_calls(registry, name, args):
    assert not registry.execute(name, json.dumps(args))["ok"]


def test_unknown_and_invalid_json(registry):
    assert not registry.execute("run_shell", "{}")["ok"]
    assert not registry.execute("analyze_reading_tasks", "{}")["ok"]
    assert not registry.execute("search_materials", "{broken")["ok"]


def test_generic_csv_analysis_and_changed_context(registry):
    result = registry.analyze_csv("measurements.csv", 2)
    assert result["row_count"] == 3 and result["columns"][0]["mean"] == 3
    assert result["columns"][1]["top_values"] == [("A", 2), ("B", 1)]
    (registry.library / "measurements.csv").write_text("length,group\n12,A\n,B\n", encoding="utf-8")
    changed = registry.analyze_csv("measurements.csv", 2)
    assert changed["row_count"] == 2 and changed["columns"][0]["mean"] == 12
    assert changed["columns"][0]["missing"] == 1


def test_bad_csv(registry):
    for text in ["x,x\n1,2\n", "x,y\n1,2,3\n"]:
        (registry.library / "bad.csv").write_text(text, encoding="utf-8")
        assert not registry.execute("analyze_csv", '{"filename":"bad.csv","sample_rows":2}')["ok"]


def write_pdf(path, pages=1):
    writer = PdfWriter()
    for i in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 250 Td (Topic {i+1}: definitions examples and proof.) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)


def test_pdf_sources_and_inspection_is_not_reading(registry):
    write_pdf(registry.library / "book.pdf", 31)
    inspection = registry.inspect_material("book.pdf")
    assert inspection["total"] == 31 and not inspection["read_positions"]
    with pytest.raises(ValueError, match="尚未读取"):
        registry._source_positions("book.pdf", "book.pdf:p1-p3")
    for start in range(1, 32, 3):
        registry.read_material("book.pdf", start, min(3, 32-start))
    assert len(registry._source_positions("book.pdf", "book.pdf:p1-p31")) == 31


def test_scanned_and_garbled_pdf_feedback(registry, monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(registry.library / "blank.pdf")
    assert "OCR" in registry.execute("read_material", '{"filename":"blank.pdf","start":1,"count":1}')["error"]
    monkeypatch.setattr("agent.tools.PdfReader", lambda _: SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: "\x01"*20+"bad mapping"*10)]))
    assert "乱码" in registry.execute("inspect_material", '{"filename":"blank.pdf"}')["error"]


def unit(id="U1", start=1, end=4, minutes=40, prereqs=None):
    return {"id": id, "title": "概念与基本性质" if id=="U1" else "定理证明与应用",
            "material": "lesson.md", "sources": [f"lesson.md:L{start}-L{end}"],
            "objective": "能够解释概念并独立完成对应例题", "difficulty": "基础" if id=="U1" else "较难",
            "difficulty_reason": "包含新定义和例子，需要理解后完成练习" if id=="U1" else "证明依赖多个先修概念，需要额外推导与练习时间",
            "estimated_minutes": minutes, "prerequisites": prereqs or []}


def payload(units=None, **changes):
    return {"start_date": "2026-10-22", "days": 2, "daily_minutes": 60,
            "materials": ["lesson.md"], "scope": "whole_material",
            "units": units or [unit(),unit("U2",5,8,60,["U1"])], **changes}


@pytest.fixture
def lesson(registry):
    (registry.library / "lesson.md").write_text("# Definitions\nDefinition\nExample\nProperty\n# Theorem\nProof\nApplication\nExercise\n", encoding="utf-8")
    return registry


def test_plan_needs_actual_evidence(lesson):
    assert not lesson.execute("create_study_plan", json.dumps(payload()))["ok"]
    lesson.read_material("lesson.md", 1, 8)
    result = lesson.execute("create_study_plan", json.dumps(payload()))
    assert result["ok"]
    data = result["data"]
    assert [d["minutes"] for d in data["plan"]] == [40,60]
    assert [d["sessions"][0]["id"] for d in data["plan"]] == ["U1","U2"]
    assert not data["unscheduled"] and not data["coverage"][0]["uncovered_positions"]


def test_plan_rejects_partial_book_disguised_as_whole(lesson):
    lesson.read_material("lesson.md", 1, 8)
    result = lesson.execute("create_study_plan", json.dumps(payload([unit()])))
    assert not result["ok"] and "未完整覆盖" in result["error"]
    result = lesson.execute("create_study_plan", json.dumps(payload([unit()],scope="selected_topics")))
    assert result["ok"] and result["data"]["coverage"][0]["uncovered_positions"] == [5,6,7,8]


def test_plan_preserves_unit_and_reports_too_large(lesson):
    lesson.read_material("lesson.md",1,8)
    result = lesson.execute("create_study_plan", json.dumps(payload([unit(end=8,minutes=90)])))
    assert result["ok"] and result["data"]["scheduled_minutes"] == 0
    assert "超过单日预算" in result["data"]["unscheduled"][0]["reason"]


def test_plan_rejects_cycles(lesson):
    lesson.read_material("lesson.md",1,8)
    result = lesson.execute("create_study_plan", json.dumps(payload([unit(prereqs=["U2"]),unit("U2",5,8,60,["U1"])])))
    assert not result["ok"] and "循环" in result["error"]


def test_csv_profile_is_usable_evidence(registry):
    data = registry.analyze_csv("measurements.csv", 2)
    u = unit()
    u.update(material="measurements.csv", sources=[data["source"]])
    result=registry.execute("create_study_plan",json.dumps(payload([u],materials=["measurements.csv"])))
    assert result["ok"] and not result["data"]["coverage"][0]["uncovered_positions"]


def test_plan_rejects_page_only_units(lesson):
    lesson.read_material("lesson.md",1,8)
    u=unit(end=8)
    u["title"]="阅读教材第1-8页"
    result=lesson.execute("create_study_plan",json.dumps(payload([u])))
    assert not result["ok"] and "知识主题" in result["error"]
    u["title"]="概念与基本性质"
    u["difficulty_reason"]="按照每页10分钟计算学习用时"
    result=lesson.execute("create_study_plan",json.dumps(payload([u])))
    assert not result["ok"] and "不能以页数" in result["error"]
