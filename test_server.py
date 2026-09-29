"""In-memory tests against a tiny synthetic vault (no network, never touches the real vault)."""
import json
import os
import textwrap

import pytest

FIXTURE = {
    "tools/Ollama.md": """
        ---
        type: tool
        aliases: [ollama]
        summary: "Local LLM runner with an OpenAI-compatible API."
        depth: studied
        learned: false
        buzz: 90
        requires: ["[[llama.cpp]]"]
        runs_on: ["[[Apple Silicon]]"]
        ---
        ## Definition
        Runs open models on your machine.
        ## Gotchas
        Set OLLAMA_NO_CLOUD=1 for fail-closed privacy.
        """,
    "tools/llama.cpp.md": """
        ---
        type: tool
        summary: "C/C++ engine for quantized models."
        depth: collected
        buzz: 80
        ---
        ## Definition
        Inference engine.
        """,
    "concepts/Apple Silicon.md": """
        ---
        type: concept
        summary: "Apple's ARM chips with unified memory."
        stub: true
        ---
        """,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server(tmp_path, monkeypatch):
    for rel, text in FIXTURE.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip())
    monkeypatch.setenv("KB_VAULT", str(tmp_path))
    monkeypatch.setenv("KB_EMBED_CACHE", str(tmp_path / "cache" / "emb.json"))
    monkeypatch.setenv("KB_EMBED_URL", "http://127.0.0.1:9")  # nothing listens: embeddings unavailable
    import importlib
    import server as srv
    importlib.reload(srv)
    return srv


async def call(srv, tool, **args):
    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(srv.mcp._mcp_server) as client:
        res = await client.call_tool(tool, args)
        assert not res.isError, res.content
        return res.structuredContent.get("result", res.structuredContent) if res.structuredContent \
            else json.loads(res.content[0].text)


@pytest.mark.anyio
async def test_tools_are_read_only(server):
    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(server.mcp._mcp_server) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {"search_vault", "semantic_search", "get_note", "related",
                                       "learning_next", "vault_stats"}
    assert all(t.annotations.readOnlyHint for t in tools)


@pytest.mark.anyio
async def test_search_ranks_exact_name_first(server):
    hits = await call(server, "search_vault", query="ollama")
    assert hits[0]["name"] == "Ollama"


@pytest.mark.anyio
async def test_search_body_text(server):
    hits = await call(server, "search_vault", query="fail-closed privacy")
    assert [h["name"] for h in hits] == ["Ollama"]


@pytest.mark.anyio
async def test_get_note_sections_and_alias(server):
    note = await call(server, "get_note", name="OLLAMA", sections=["Gotchas"])
    assert note["name"] == "Ollama"
    assert list(note["sections"]) == ["Gotchas"]
    assert note["edges"]["requires"] == ["llama.cpp"]


@pytest.mark.anyio
async def test_get_note_unknown_suggests(server):
    res = await call(server, "get_note", name="llama")
    assert "error" in res and "llama.cpp" in res["did_you_mean"]


@pytest.mark.anyio
async def test_related_both_directions(server):
    res = await call(server, "related", name="llama.cpp")
    assert res["incoming"] == {"requires": ["Ollama"]}


@pytest.mark.anyio
async def test_stats(server):
    s = await call(server, "vault_stats")
    assert s["by_type"] == {"tool": 2, "concept": 1}


def test_no_writes_in_source():
    src = open(os.path.join(os.path.dirname(__file__), "vault.py")).read() + \
          open(os.path.join(os.path.dirname(__file__), "server.py")).read()
    for bad in ("write_text(", "open(", ".unlink(", "os.remove", "shutil"):
        assert bad not in src.replace("read_text(", ""), bad


@pytest.mark.anyio
async def test_semantic_falls_back_to_keyword(server):
    res = await call(server, "semantic_search", query="ollama")
    assert res["mode"] == "keyword" and res["results"][0]["name"] == "Ollama"


def test_non_local_embedding_endpoint_refused(monkeypatch):
    import embed
    monkeypatch.setenv("KB_EMBED_URL", "https://api.example.com")
    with pytest.raises(embed.EmbeddingsUnavailable, match="non-local"):
        embed.embed(["private note text"])


@pytest.mark.anyio
async def test_semantic_ranks_by_meaning(server, monkeypatch, tmp_path):
    import embed
    fake = {"privacy": [1.0, 0.0], "engine": [0.0, 1.0]}
    def fake_embed(texts):
        return [fake["privacy"] if ("privacy" in t.lower() or "runs open models" in t.lower()) else fake["engine"]
                for t in texts]
    monkeypatch.setattr(embed, "embed", fake_embed)
    res = await call(server, "semantic_search", query="keep private data local privacy")
    assert res["mode"] == "semantic" and res["results"][0]["name"] == "Ollama"
    assert (tmp_path / "cache" / "emb.json").exists()          # cache lives outside the vault
    assert not list(tmp_path.glob("tools/*.tmp"))             # vault untouched


@pytest.mark.anyio
async def test_note_resource_and_quiz_prompt(server):
    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(server.mcp._mcp_server) as client:
        res = await client.read_resource("note://ollama")
        assert "OLLAMA_NO_CLOUD" in res.contents[0].text
        prompt = await client.get_prompt("quiz", {"topic": "Ollama"})
        text = prompt.messages[0].content.text
        assert "3 questions" in text and "OLLAMA_NO_CLOUD" in text
