import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .runner import AgentRunner


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="研读助手 CLI")
    parser.add_argument("query", help="研读问题或阅读安排要求")
    parser.add_argument("--mode", choices=["demo", "live"])
    parser.add_argument("--output", type=Path, help="将回答和完整工具轨迹导出为 JSON")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.mode:
        settings = replace(settings, mode=args.mode)
    result = asyncio.run(AgentRunner(settings).run(args.query))
    print(result["answer"] or result["error"])
    print(f"\n运行 {result['run_id']} · {len(result['traces'])} 次工具调用 · {result['elapsed_ms']} ms")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"轨迹已保存：{args.output.resolve()}")
    raise SystemExit(1 if result["error"] else 0)


if __name__ == "__main__":
    main()
