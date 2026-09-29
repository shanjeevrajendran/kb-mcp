"""kb-write tests on a synthetic vault: only learning fields change, grading is enforced server-side."""
import json
import textwrap

import pytest

NOTE = """
    ---
    type: concept
    aliases: [pp]
    summary: "First LLM phase."
    depth: studied
    learned: false
    buzz: 60
    ---
    ## Definition
    Reading the prompt.

    ## Gotchas
    - Caches need an exact prefix.

    ## Sources
    - example
    """
FIXTURE = {
    "concepts/Prefill.md": NOTE,
    "concepts/KV cache.md": """
        ---
        type: concept
        depth: collected
        stub: true
        ---
        """,
    "projects/agent-lab.md": """
        ---
        type: project
        active: true
        ---
        """,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def w(tmp_path, monkeypatch):
    for rel, text in FIXTURE.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip())
    monkeypatch.setenv("KB_VAULT", str(tmp_path))
    monkeypatch.setenv("KB_BACKUP_CMD", "")
    import importlib
    import writer
    importlib.reload(writer)
    writer.root = tmp_path
    return writer


async def call(w, tool, **args):
    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(w.mcp._mcp_server) as client:
        res = await client.call_tool(tool, args)
        if res.isError:
            return {"error": res.content[0].text}
        return res.structuredContent.get("result", res.structuredContent) if res.structuredContent \
            else json.loads(res.content[0].text)


def text(w, rel="concepts/Prefill.md"):
    return (w.root / rel).read_text()


@pytest.mark.anyio
async def test_only_two_write_tools(w):
    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(w.mcp._mcp_server) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {"record_quiz", "mark_learned"}
    assert all(t.annotations.readOnlyHint is False and t.annotations.destructiveHint is False for t in tools)


@pytest.mark.anyio
async def test_first_pass_marks_learned_and_changes_nothing_else(w):
    before = text(w)
    res = await call(w, "record_quiz", topic="pp", grades=["correct", "correct", "partly"], takeaway="prefill is compute-bound")
    after = text(w)
    assert res["passed"] and res["attempt"] == "first" and res["changed"]["reviews"] == 0
    assert "learned: true" in after and "learned_on: " in after
    removed = set(before.splitlines()) - set(after.splitlines())
    assert removed == {"learned: false"}                      # the only line that changed
    assert after.rstrip().endswith("prefill is compute-bound")
    assert "## Quiz log\n- " in after and "## Sources" in after


@pytest.mark.anyio
async def test_server_decides_pass_not_caller(w):
    res = await call(w, "record_quiz", topic="Prefill", grades=["correct", "correct", "missed"], takeaway="x")
    assert res["passed"] is False and res["changed"] == {}     # apply question missed -> fail
    assert "learned: false" in text(w) and "fail (first" in text(w)


@pytest.mark.anyio
async def test_review_pass_then_fail(w):
    await call(w, "record_quiz", topic="Prefill", grades=["correct"] * 3, takeaway="a")
    r2 = await call(w, "record_quiz", topic="Prefill", grades=["correct", "correct", "correct"], takeaway="b")
    assert r2["attempt"] == "review" and r2["changed"]["reviews"] == 1
    r3 = await call(w, "record_quiz", topic="Prefill", grades=["missed", "partly", "missed"], takeaway="c")
    assert r3["changed"] == {"learned": False, "reviews": 0}
    assert text(w).count("\n- 20") == 3                        # three log lines, one section
    assert text(w).count("## Quiz log") == 1


@pytest.mark.anyio
@pytest.mark.parametrize("topic,grades,why", [
    ("Prefill", ["correct", "yes", "correct"], "grades must be"),
    ("Prefill", ["correct", "correct"], "grades must be"),
    ("KV cache", ["correct"] * 3, "not studied"),
    ("agent-lab", ["correct"] * 3, "only tools/concepts"),
    ("../concepts/Prefill", ["correct"] * 3, "no note named"),
])
async def test_refusals(w, topic, grades, why):
    before = text(w)
    res = await call(w, "record_quiz", topic=topic, grades=grades, takeaway="x")
    assert why in res["error"] and text(w) == before


@pytest.mark.anyio
async def test_mark_learned_needs_reason_and_is_audited(w):
    assert "reason is required" in (await call(w, "mark_learned", topic="Prefill", learned=True, reason=" "))["error"]
    res = await call(w, "mark_learned", topic="Prefill", learned=True, reason="user already knows it")
    assert res["changed"]["learned"] is True
    audit = [json.loads(l) for l in (w.root / "_history" / "learning.jsonl").read_text().splitlines()]
    assert audit[-1]["action"] == "mark_learned" and audit[-1]["reason"] == "user already knows it"
    assert "marked learned manually" in text(w)
