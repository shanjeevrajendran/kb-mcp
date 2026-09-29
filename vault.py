"""Read-only index over the AI-KB Obsidian vault: notes, typed edges, keyword search."""
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FOLDERS = ("tools", "concepts", "projects", "sources", "digests")
EDGES = ("requires", "alternative_to", "part_of", "used_with", "runs_on", "enables",
         "measured_by", "contrasts_with", "supersedes", "surfaced_by")
LINK = re.compile(r"\[\[([^\]|#]+)")
WORD = re.compile(r"[a-z0-9][a-z0-9.+-]*")


@dataclass
class Note:
    name: str
    folder: str
    meta: dict
    body: str
    edges: dict = field(default_factory=dict)  # edge type -> [target names]

    @property
    def type(self) -> str:
        return str(self.meta.get("type", self.folder.rstrip("s")))

    def card(self) -> dict:
        m = self.meta
        return {"name": self.name, "type": self.type, "summary": m.get("summary") or "",
                "depth": m.get("depth"), "learned": m.get("learned"), "buzz": m.get("buzz"),
                "status": m.get("status"), "project": m.get("project")}

    def sections(self) -> dict:
        out, cur = {}, "_intro"
        for line in self.body.splitlines():
            if line.startswith("## "):
                cur = line[3:].strip()
                out[cur] = []
            else:
                out.setdefault(cur, []).append(line)
        return {k: "\n".join(v).strip() for k, v in out.items() if "\n".join(v).strip()}


def _tokens(text: str) -> list[str]:
    """Words, plus the parts of dotted/hyphenated names ("llama.cpp" -> llama.cpp, llama, cpp)."""
    out = []
    for w in WORD.findall(text.lower()):
        w = w.rstrip(".-+")
        out.append(w)
        parts = [p for p in re.split(r"[.+-]", w) if p]
        if len(parts) > 1:
            out.extend(parts)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Vault:
    """Scans the vault lazily and rescans only when a file's mtime changes."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser()
        self._stamp = None
        self.notes: dict[str, Note] = {}
        self._alias: dict[str, str] = {}

    def _scan_stamp(self):
        return tuple(sorted((str(p), p.stat().st_mtime) for f in FOLDERS
                            for p in (self.root / f).glob("*.md")))

    def refresh(self):
        stamp = self._scan_stamp()
        if stamp == self._stamp:
            return
        notes, alias = {}, {}
        for f in FOLDERS:
            for p in sorted((self.root / f).glob("*.md")):
                text = p.read_text(encoding="utf-8")
                meta, body = {}, text
                if text.startswith("---\n"):
                    head, sep, rest = text[4:].partition("\n---\n")
                    if sep:
                        try:
                            meta = yaml.safe_load(head) or {}
                        except yaml.YAMLError:
                            meta = {}
                        body = rest
                edges = {e: [m for v in (meta.get(e) or []) for m in LINK.findall(str(v))] for e in EDGES}
                n = Note(p.stem, f, meta, body, {e: v for e, v in edges.items() if v})
                notes[n.name] = n
                alias[_norm(n.name)] = n.name
                for a in meta.get("aliases") or []:
                    alias.setdefault(_norm(str(a)), n.name)
        self.notes, self._alias, self._stamp = notes, alias, stamp
        self._build_index()

    def _build_index(self):
        self._docs = {}
        for n in self.notes.values():
            fields = [(n.name + " " + " ".join(map(str, n.meta.get("aliases") or [])), 3.0),
                      (str(n.meta.get("summary") or "") + " " + " ".join(map(str, n.meta.get("tags") or [])), 2.0),
                      (n.body, 1.0)]
            tf = Counter()
            for text, w in fields:
                for t in _tokens(text):
                    tf[t] += w
            self._docs[n.name] = tf
        df = Counter(t for tf in self._docs.values() for t in tf)
        N = max(1, len(self._docs))
        self._idf = {t: math.log(1 + (N - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self._avglen = sum(sum(tf.values()) for tf in self._docs.values()) / N

    def resolve(self, name: str) -> Note | None:
        self.refresh()
        key = self._alias.get(_norm(name))
        return self.notes.get(key) if key else None

    def search(self, query: str, types: set[str] | None = None, limit: int = 8) -> list[dict]:
        """BM25-style keyword ranking; name/alias hits weigh 3x, summary/tags 2x, body 1x."""
        self.refresh()
        q = _tokens(query)
        exact = self.resolve(query)
        k1, b, scored = 1.2, 0.75, []
        for name, tf in self._docs.items():
            n = self.notes[name]
            if types and n.type not in types:
                continue
            dl = sum(tf.values())
            s = sum(self._idf.get(t, 0) * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * dl / self._avglen))
                    for t in q if t in tf)
            if exact and exact.name == name:
                s += 100
            if s > 0:
                scored.append((s, n))
        scored.sort(key=lambda x: -x[0])
        return [{**n.card(), "score": round(s, 2)} for s, n in scored[:limit]]

    def embed_docs(self, types: set[str] | None = None) -> dict:
        """note name -> (mtime, text to embed): name, summary, definition and how-it-works."""
        self.refresh()
        out = {}
        for n in self.notes.values():
            if types and n.type not in types:
                continue
            secs = n.sections()
            text = " \n".join([n.name, str(n.meta.get("summary") or ""), secs.get("Definition", ""),
                                secs.get("How it works", "")])[:2000]
            out[n.name] = ((self.root / n.folder / f"{n.name}.md").stat().st_mtime, text)
        return out

    def related(self, name: str, edge_types: set[str] | None = None) -> dict:
        n = self.resolve(name)
        if n is None:
            return {}
        out = {e: v for e, v in n.edges.items() if not edge_types or e in edge_types}
        incoming = {}
        for other in self.notes.values():
            for e, targets in other.edges.items():
                if (not edge_types or e in edge_types) and n.name in targets:
                    incoming.setdefault(e, []).append(other.name)
        return {"note": n.name, "outgoing": out, "incoming": incoming}
