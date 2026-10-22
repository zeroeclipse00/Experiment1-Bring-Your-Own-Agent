import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from agent.tools import ToolRegistry


@pytest.fixture
def registry(sample_data):
    return ToolRegistry(sample_data)


def test_search_read_sources(registry):
    hits = registry.execute("search_materials", '{"query":"Function Calling","limit":5}')
    assert hits["ok"]
    filename = hits["data"]["results"][0]["filename"]
    read = registry.execute("read_material", json.dumps({"filename": filename, "start": 1, "count": 30}))
    assert read["ok"]
    assert any("tool_call_id" in e["text"] for e in read["data"]["excerpts"])
    assert all(e["source"].startswith(filename + ":L") for e in read["data"]["excerpts"])


@pytest.mark.parametrize("name,args", [
    ("read_material", {"filename": "../../.env", "start": 1, "count": 1}),
    ("read_material", {"filename": "missing.md", "start": 1, "count": 1}),
    ("read_material", {"filename": "01_agent_basics.md", "start": 0, "count": 1}),
    ("read_material", {"filename": "01_agent_basics.md", "start": "1", "count": 1}),
    ("search_materials", {"query": "x", "limit": 100}),
    ("search_materials", {"query": "x", "limit": 1, "unexpected": "x"}),
    ("analyze_reading_tasks", {"start_date": "2026-02-30", "days": 3, "daily_minutes": 60}),
])
def test_reject_invalid_calls(registry, name, args):
    assert not registry.execute(name, json.dumps(args))["ok"]


def test_unknown_and_invalid_json(registry):
    assert not registry.execute("run_shell", "{}")["ok"]
    assert not registry.execute("search_materials", "{broken")["ok"]


def test_planning_budget_and_deadlines(registry):
    result = registry.analyze_reading_tasks("2026-10-08", 3, 60)
    assert result["remaining_minutes"] == 215
    assert result["capacity_minutes"] == 180
    assert sum(x["minutes"] for x in result["unscheduled"]) == 35
    assert all(day["minutes"] <= 60 for day in result["plan"])
    assert not any(t["id"] == "R05" for t in result["tasks"])
    assert result["plan"][0]["sessions"][0]["task_id"] == "R01"
    late = registry.analyze_reading_tasks("2026-10-15", 1, 30)
    assert all(t["overdue_at_start"] for t in late["tasks"])
    assert all(s["past_due"] for s in late["plan"][0]["sessions"])


def test_context_changes_result(tmp_path):
    (tmp_path / "library").mkdir()
    task_file = tmp_path / "reading_tasks.csv"
    header = "id,title,material,due_date,estimated_minutes,priority,status\n"
    task_file.write_text(header + "1,Read,test.md,2026-10-09,40,1,todo\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    assert registry.analyze_reading_tasks("2026-10-08", 1, 60)["remaining_minutes"] == 40
    task_file.write_text(header + "1,Read,test.md,2026-10-09,90,1,todo\n", encoding="utf-8")
    changed = registry.analyze_reading_tasks("2026-10-08", 1, 60)
    assert changed["remaining_minutes"] == 90
    assert changed["unscheduled"][0]["minutes"] == 30


def test_library_csv_is_visible_and_takes_precedence(sample_data):
    path = sample_data / "library" / "reading_tasks.csv"
    path.write_text("id,title,material,due_date,estimated_minutes,priority,status\n"
                    "CUSTOM,自定义教材阅读,book.pdf,2026-10-23,75,1,todo\n", encoding="utf-8-sig")
    registry = ToolRegistry(sample_data)
    assert "reading_tasks.csv" in registry.inventory()
    assert registry.search_materials("自定义教材", 1)["results"][0]["filename"] == "reading_tasks.csv"
    assert "CUSTOM" in registry.read_material("reading_tasks.csv", 2, 1)["excerpts"][0]["text"]
    result = registry.analyze_reading_tasks("2026-10-22", 1, 60)
    assert result["source_file"] == "library/reading_tasks.csv"
    assert result["remaining_minutes"] == 75
    assert result["tasks"][0]["source"] == "library/reading_tasks.csv:2"
    assert result["unscheduled"] == [{"task_id": "CUSTOM", "minutes": 15}]


def test_bad_library_csv_does_not_silently_use_sample_tasks(sample_data):
    (sample_data / "library" / "reading_tasks.csv").write_text("title,minutes\nx,1\n", encoding="utf-8")
    result = ToolRegistry(sample_data).execute("analyze_reading_tasks", '{"start_date":"2026-10-22","days":1,"daily_minutes":60}')
    assert not result["ok"] and "缺少必要列" in result["error"]


def test_bad_csv_and_missing_file(tmp_path):
    registry = ToolRegistry(tmp_path)
    args = '{"start_date":"2026-10-08","days":3,"daily_minutes":60}'
    assert not registry.execute("analyze_reading_tasks", args)["ok"]
    (tmp_path / "reading_tasks.csv").write_text("title,minutes\nx,3\n", encoding="utf-8")
    assert "缺少必要列" in registry.execute("analyze_reading_tasks", args)["error"]


def test_scanned_pdf_feedback(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(library / "scan.pdf")
    result = ToolRegistry(tmp_path).execute("read_material", '{"filename":"scan.pdf","start":1,"count":1}')
    assert not result["ok"] and "OCR" in result["error"]


def test_text_pdf_page_citations(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 250 Td (Agent tool calling reads external context.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(library / "reading.pdf")
    registry = ToolRegistry(tmp_path)
    search = registry.search_materials("Agent", 1)
    assert search["results"][0]["source"] == "reading.pdf:p1"
    result = registry.read_material("reading.pdf", 1, 1)
    assert result["excerpts"][0]["source"] == "reading.pdf:p1"
    assert "external context" in result["excerpts"][0]["text"]


def test_multiline_csv_cites_physical_start_line(tmp_path):
    (tmp_path / "reading_tasks.csv").write_text(
        'id,title,material,due_date,estimated_minutes,priority,status\n'
        '1,"two\nlines",test.md,2026-10-09,40,1,todo\n'
        '2,second,test.md,2026-10-10,20,1,todo\n', encoding="utf-8")
    tasks = ToolRegistry(tmp_path).analyze_reading_tasks("2026-10-08", 1, 60)["tasks"]
    assert [t["source"] for t in tasks] == ["reading_tasks.csv:2", "reading_tasks.csv:4"]


def test_symlink_escape_if_supported(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (library / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("系统未允许创建符号链接")
    result = ToolRegistry(tmp_path).execute("read_material", '{"filename":"link.txt","start":1,"count":1}')
    assert not result["ok"]
