# kb-mcp

A read-only [MCP](https://modelcontextprotocol.io) server over my personal AI-learning knowledge graph, an Obsidian vault of tools, concepts and projects linked by typed edges. With it, Claude (or any MCP client) answers from my own notes instead of from memory.

## Tools

| Tool | What it does |
|---|---|
| `search_vault(query, kind?, limit?)` | Keyword (BM25) search. Name and alias matches count 3×, summaries and tags 2×, note text 1×. Handles partial names, so "llama" finds "llama.cpp". |
| `get_note(name, sections?)` | Returns one note by name or alias: its metadata, typed edges, and chosen sections (e.g. `["Definition", "Gotchas"]`). If the name isn't found, it returns "did you mean" suggestions. |
| `related(name, edge_types?)` | Returns the note's typed graph neighbours, both outgoing and incoming. Edge types: `requires`, `alternative_to`, `used_with`, `runs_on`, and others. |
| `learning_next(project?)` | Suggests what to learn next. With a project, it follows that project's phase order. |
| `vault_stats()` | Returns counts by type, depth and learned state, plus the active projects. |

## Design choices
- **Read-only by construction.** No tool writes, and every tool is annotated `readOnlyHint: true`. A test fails if write calls appear in the source.
- **Local only.** It uses the stdio transport and makes no network calls, so private notes never leave the machine. This matches the fail-closed rule in [agent-lab](https://github.com/shanjeevrajendran/agent-lab).
- **Untrusted content.** Notes are partly web-derived, so the server tells clients to treat note text as data, not instructions.
- **Cheap freshness.** It rescans only when a file's modification time changes.

## Run

```bash
uv run --group dev pytest -q              # 8 in-memory tests on a synthetic vault
KB_VAULT=/path/to/vault uv run python server.py   # stdio server
```

Register with Claude Code:

```bash
claude mcp add kb -s user -e KB_VAULT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/AI-KB" -- uv run --directory ~/code/kb-mcp python server.py
```

Configuration:
- `KB_VAULT` is the vault path.
- `KB_SCRIPTS` is the folder containing `kb_next.py`. The default is `~/.claude/skills/kb-study/scripts`.

## Next
- Embeddings for semantic search, run locally (e.g. via Ollama).
- Expose notes as MCP resources, and add a `quiz` prompt.
