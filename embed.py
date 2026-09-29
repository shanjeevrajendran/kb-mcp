"""Optional local embeddings via Ollama, for semantic search. Fail-closed: localhost only.

Embeddings are cached per note (keyed by file mtime) under ~/.cache/kb-mcp, never in the vault.
"""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class EmbeddingsUnavailable(RuntimeError):
    pass


def _config():
    url = os.environ.get("KB_EMBED_URL", "http://localhost:11434")
    host = urllib.parse.urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        # Note text must never leave this machine to be embedded.
        raise EmbeddingsUnavailable(f"refusing non-local embedding endpoint {host!r}")
    return url.rstrip("/"), os.environ.get("KB_EMBED_MODEL", "embeddinggemma")


def embed(texts: list[str]) -> list[list[float]]:
    """L2-normalized vectors from Ollama /api/embed (cosine similarity = dot product)."""
    url, model = _config()
    req = urllib.request.Request(f"{url}/api/embed", data=json.dumps({"model": model, "input": texts}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["embeddings"]
    except Exception as e:  # not running, model not pulled, timeout
        raise EmbeddingsUnavailable(f"Ollama embeddings unavailable at {url} ({model}): {e}") from e


class EmbeddingCache:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or os.environ.get("KB_EMBED_CACHE", "~/.cache/kb-mcp/embeddings.json")).expanduser()
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def vectors(self, docs: dict[str, tuple[float, str]], embed_fn=None) -> dict[str, list[float]]:
        """docs: note name -> (mtime, text). Embeds only new or changed notes."""
        embed_fn = embed_fn or globals()["embed"]  # resolved at call time (swappable in tests)
        _, model = _config()
        cache = self.data.setdefault(model, {})
        stale = [n for n, (mt, _) in docs.items() if cache.get(n, {}).get("mtime") != mt]
        for i in range(0, len(stale), 32):
            batch = stale[i:i + 32]
            for n, v in zip(batch, embed_fn([docs[n][1] for n in batch])):
                cache[n] = {"mtime": docs[n][0], "vec": v}
        if stale:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
        return {n: cache[n]["vec"] for n in docs if n in cache}


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
