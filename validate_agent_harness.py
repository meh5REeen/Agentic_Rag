"""
Lightweight checks for the agent harness (no live LLM required).
"""
from agent_harness.memory import AgentRunStore, SubAgentMemory, wipe_subagent_context
from agent_harness.parent import _normalize_subtasks, _fallback_plan, merge_citations
from agent_harness.config import MAX_SUBAGENTS


def test_memory_store_and_wipe():
    store = AgentRunStore()
    run_id = store.start_run(session_id="s1")
    mem = SubAgentMemory(
        subtask_id="t1",
        subtask_type="rag",
        status="ok",
        result_summary="Found ADHD criteria on page 15.",
        sources_used=[{"source": "x.pdf", "page": 15, "document_id": 17}],
    )
    store.write(run_id, mem)
    assert len(store.read_all(run_id)) == 1

    messages = [{"role": "user", "content": "secret transcript"}]
    scratch = {"raw": "should vanish"}
    wipe_subagent_context(messages, scratch)
    assert messages == []
    assert scratch == {}

    store.clear_run(run_id)
    assert store.read_all(run_id) == []
    print("PASS: memory store write/read/clear + transcript wipe")


def test_normalize_caps_subagents():
    raw = [
        {"id": f"t{i}", "type": "rag", "instruction": f"q{i}", "tools": ["retrieve", "answer"]}
        for i in range(10)
    ]
    plan = _normalize_subtasks(raw)
    assert len(plan) <= MAX_SUBAGENTS
    assert all(p["type"] == "rag" for p in plan)
    print(f"PASS: planner normalization caps at MAX_SUBAGENTS={MAX_SUBAGENTS}")


def test_fallback_plan_optional_web():
    plan = _fallback_plan("What is ADHD?", allow_web=True)
    assert plan[0]["type"] == "rag"
    assert any(p["type"] == "web" for p in plan)
    print("PASS: fallback plan includes rag (+ web when allowed)")


def test_merge_citations_reindexes():
    m1 = SubAgentMemory(
        subtask_id="t1", subtask_type="rag", status="ok", result_summary="a",
        citations=[{"index": 1, "document_id": 8, "source": "a.pdf", "page": 2}],
    )
    m2 = SubAgentMemory(
        subtask_id="t2", subtask_type="rag", status="ok", result_summary="b",
        citations=[
            {"index": 1, "document_id": 8, "source": "a.pdf", "page": 2},
            {"index": 2, "document_id": 17, "source": "b.pdf", "page": 15},
        ],
    )
    merged = merge_citations([m1, m2])
    assert len(merged) == 2
    assert merged[0]["index"] == 1
    assert merged[1]["index"] == 2
    assert merged[1]["document_id"] == 17
    print("PASS: citation merge dedupes and reindexes")


if __name__ == "__main__":
    test_memory_store_and_wipe()
    test_normalize_caps_subagents()
    test_fallback_plan_optional_web()
    test_merge_citations_reindexes()
    print("\nAll agent harness checks passed.")
