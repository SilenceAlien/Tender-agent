"""Smoke test: run the full headless pipeline on REAL tender PDFs.

No API keys in env -> LLM nodes default to mock. This exercises the
real DocumentParser path (real layout/tables) that mock-PDF tests skip.
Prints per-node status, parsed docs, generated sections, export path,
and any exception. Reports only; never mutates source.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/Users/alanchris/Desktop/标书agent02/bid-agent")

from core.graph import run_pipeline
from core.state import factory_state

DOCS = [
    "/Users/alanchris/Desktop/标书agent02/2026年广州铁道车辆有限公司劳务管理服务采购（技术标）v2.pdf",
    "/Users/alanchris/Desktop/标书agent02/knowledge_base/劳务外包类/招标文件/厦门税务局食堂外包_招标文件.pdf",
]


def smoke(doc_path: str) -> None:
    print("=" * 72)
    print("REAL DOC:", doc_path)
    if not Path(doc_path).exists():
        print("  !! MISSING FILE")
        return
    state = factory_state(
        documents=[{"filename": Path(doc_path).name, "type": "pdf", "path": doc_path}],
        bid_type="劳务外包类",
        max_rounds=1,
        min_section_chars=0,
    )
    state["review_status"] = "approved"
    try:
        result = run_pipeline(state)
        ns = result.get("node_status", {})
        print("  node_status:")
        for k, v in ns.items():
            print(f"    {k}: {v}")
        docs = result.get("documents", [])
        for d in docs:
            print(
                f"    doc[{d.get('filename')}] status={d.get('status')} "
                f"chunks={len(d.get('chunks', []))} "
                f"tables={len(d.get('extracted_tables', []))}"
            )
        secs = result.get("sections", {})
        print(f"  sections generated: {len(secs)}")
        for name, c in list(secs.items())[:5]:
            print(f"    {name}: {len(c)} chars")
        print(f"  export_path: {result.get('export_path')}")
        failed = [k for k, v in ns.items() if "FAIL" in str(v)]
        if failed:
            print("  !! FAILED NODES:", failed)
    except Exception as e:  # noqa: BLE001
        print("  !! EXCEPTION:", repr(e))
        traceback.print_exc()


if __name__ == "__main__":
    for p in DOCS:
        smoke(p)
    print("SMOKE_DONE")
