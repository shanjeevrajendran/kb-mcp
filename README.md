# kb-mcp

A read-only [MCP](https://modelcontextprotocol.io) server over my personal AI-learning knowledge graph, an Obsidian vault of tools, concepts and projects linked by typed edges. With it, Claude (or any MCP client) answers from my own notes instead of from memory.

## Tools

| Tool | What it does |
|---|---|
| `search_vault(query, kind?, limit?)` | Keyword (BM25) search. Name and alias matches count 3×, summaries and tags 2×, note text 1×. Handles partial names, so "llama" finds "llama.cpp". |
| `semantic_search(query, kind?, limit?)` | Search by meaning, e.g. "how do I keep private data off the cloud?". It uses **local** Ollama embeddings (`embeddinggemma` by default), blended 0.6 semantic + 0.4 keyword. When Ollama isn't running it falls back to keyword search and says so. |
| `get_note(name, sections?)` | Returns one note by name or alias: its metadata, typed edges, and chosen sections (e.g. `["Definition", "Gotchas"]`). If the name isn't found, it returns "did you mean" suggestions. |
| `related(name, edge_types?)` | Returns the note's typed graph neighbours, both outgoing and incoming. Edge types: `requires`, `alternative_to`, `used_with`, `runs_on`, and others. |
| `learning_next(project?)` | Suggests what to learn next. With a project, it follows that project's phase order. |
| `vault_stats()` | Returns counts by type, depth and learned state, plus the active projects. |

Also:
- **Resource** `note://{name}` returns the whole note as markdown.
- **Prompt** `quiz(topic)` runs a 3-question active-recall quiz built from the note's own content.

## kb-write: the separate, write-enabled server (`writer.py`)
The read-only server above can never change the vault. Learning progress goes through a **second server** that you register and approve separately:

| Tool | What it does |
|---|---|
| `record_quiz(topic, grades, takeaway)` | Takes 3 grades (`correct`, `partly` or `missed`). **The server decides pass or fail** (at least 2 correct, and the apply question not missed), then updates `learned`, `learned_on` and `reviews`, and appends to `## Quiz log`. |
| `mark_learned(topic, learned, reason)` | An explicit override for when you say "mark X learned". A reason is required. |

Guards:
- It only accepts tool and concept notes, found by name or alias; file paths are never accepted.
- Only 3 frontmatter fields can ever change. The rest of the file stays byte-identical, which is tested.
- Every write is atomic, preceded by a vault snapshot (`KB_BACKUP_CMD`), and appended to `_history/learning.jsonl`.

## Design choices
- **Read-only by construction.** No tool writes, and every tool is annotated `readOnlyHint: true`. A test fails if write calls appear in the source.
- **Local only.** It uses the stdio transport. The only network call is optional: embeddings from a **localhost** Ollama. Any non-local embedding endpoint is refused (a test covers this), so private notes never leave the machine.
- **Never writes to the vault.** Embeddings are cached per note, keyed by modification time, in `~/.cache/kb-mcp/`. This matches the fail-closed rule in [agent-lab](https://github.com/shanjeevrajendran/agent-lab).
- **Untrusted content.** Notes are partly web-derived, so the server tells clients to treat note text as data, not instructions.
- **Cheap freshness.** It rescans only when a file's modification time changes.

## Run

```bash
uv run --group dev pytest -q              # 22 in-memory tests on synthetic vaults (also run in CI on every push/PR)
ollama pull embeddinggemma                # optional: enables semantic_search
KB_VAULT=/path/to/vault uv run python server.py   # stdio server
```

Register with Claude Code (the reader, plus the writer only if you want quiz results saved):

```bash
claude mcp add kb -s user -e KB_VAULT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/AI-KB" -- uv run --directory ~/code/kb-mcp python server.py
claude mcp add kb-write -s user -e KB_VAULT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/AI-KB" -- uv run --directory ~/code/kb-mcp python writer.py
```

Configuration:
- `KB_VAULT` is the vault path.
- `KB_SCRIPTS` is the folder containing `kb_next.py`. The default is `~/.claude/skills/kb-study/scripts`.
- `KB_EMBED_URL` defaults to `http://localhost:11434` and must be local. `KB_EMBED_MODEL` defaults to `embeddinggemma`.

## Next
- Spaced-review reminders pushed to my phone when `learned_on + 30·2^reviews` days have passed.
