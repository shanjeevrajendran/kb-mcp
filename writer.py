"""kb-write: the ONLY server allowed to change the vault, and only my learning progress.

Kept separate from the read-only kb-mcp server so it can be registered (and permission-gated)
on its own. Two narrow tools, no generic write:
  record_quiz  - server grades pass/fail from the per-question grades (kb-learn rules),
                 updates learned / learned_on / reviews, appends to "## Quiz log".
  mark_learned - explicit override, only when I said so; a reason is required.
Guards: only topic notes in tools/ or concepts/, found by name/alias (no paths); only three
frontmatter fields ever change; rest of the file is preserved byte-for-byte; atomic write;
vault snapshot first (KB_BACKUP_CMD); every change appended to _history/learning.jsonl.
"""
import datetime as dt
import json
import os
import re
import subprocess

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from vault import Vault

VAULT_PATH = os.environ.get("KB_VAULT", "~/Library/Mobile Documents/com~apple~CloudDocs/AI-KB")
BACKUP_CMD = os.environ.get("KB_BACKUP_CMD", os.path.expanduser("~/code/ai-kb-mirror/backup.sh"))
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
GRADES = ("correct", "partly", "missed")
MARK = {"correct": "✅", "partly": "🟡", "missed": "❌"}
ALLOWED_FIELDS = ("learned", "learned_on", "reviews")

mcp = FastMCP("kb-write", instructions=(
    "Writes ONLY learning progress (learned, learned_on, reviews, quiz log) to my AI-KB vault. "
    "Call record_quiz after grading a quiz; call mark_learned only when I explicitly ask."))
vault = Vault(VAULT_PATH)


class WriteRefused(ValueError):
    pass


def _today() -> str:
    return dt.date.today().isoformat()


def _note(topic: str):
    n = vault.resolve(topic)
    if n is None:
        raise WriteRefused(f"no note named {topic!r}")
    if n.folder not in ("tools", "concepts"):
        raise WriteRefused(f"{n.name!r} is a {n.folder[:-1]} note; only tools/concepts track learning")
    return n, vault.root / n.folder / f"{n.name}.md"


def _set_fields(text: str, values: dict) -> str:
    """Edit only ALLOWED_FIELDS inside the frontmatter; everything else stays byte-identical."""
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise WriteRefused("note has no frontmatter")
    head, sep, body = text[4:].partition("\n---\n")
    for k, v in values.items():
        assert k in ALLOWED_FIELDS, k
        line = f"{k}: {str(v).lower() if isinstance(v, bool) else v}"
        if re.search(rf"^{k}:.*$", head, re.M):
            head = re.sub(rf"^{k}:.*$", line, head, count=1, flags=re.M)
        else:
            head += "\n" + line
    return "---\n" + head + sep + body


def _append_log(text: str, line: str) -> str:
    if "\n## Quiz log\n" in text:
        head, _, rest = text.partition("\n## Quiz log\n")
        nxt = rest.find("\n## ")
        section, tail = (rest, "") if nxt == -1 else (rest[:nxt], rest[nxt:])
        return head + "\n## Quiz log\n" + section.rstrip("\n") + "\n" + line + "\n" + tail
    return text.rstrip("\n") + "\n\n## Quiz log\n" + line + "\n"


def _commit(path, new_text: str, audit: dict):
    if BACKUP_CMD and os.path.exists(BACKUP_CMD):  # snapshot before touching the vault
        subprocess.run([BACKUP_CMD], capture_output=True, timeout=120)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)  # atomic; vault lives in iCloud
    hist = vault.root / "_history" / "learning.jsonl"
    hist.parent.mkdir(exist_ok=True)
    with hist.open("a", encoding="utf-8") as f:  # append-only audit trail
        f.write(json.dumps({"date": _today(), **audit}, ensure_ascii=False) + "\n")


@mcp.tool(annotations=WRITE)
def record_quiz(topic: str, grades: list[str], takeaway: str) -> dict:
    """Record a graded 3-question quiz and update learning progress.

    grades: exactly 3 of "correct" | "partly" | "missed", in question order (recall, understand, apply).
    Pass = at least 2 correct AND the apply question (3rd) not missed - decided here, not by the caller.
    First pass -> learned: true, learned_on: today, reviews: 0. Passed review -> reviews + 1.
    Failed review -> learned: false, reviews: 0. Failed first attempt -> progress unchanged.
    """
    grades = [g.strip().lower() for g in grades]
    if len(grades) != 3 or any(g not in GRADES for g in grades):
        raise WriteRefused('grades must be 3 values from "correct", "partly", "missed"')
    if not takeaway.strip():
        raise WriteRefused("takeaway is required")
    n, path = _note(topic)
    if n.meta.get("stub") or n.meta.get("depth") != "studied":
        raise WriteRefused(f"{n.name!r} is not studied yet - study it before quizzing")
    passed = grades.count("correct") >= 2 and grades[2] != "missed"
    was_learned = n.meta.get("learned") is True
    reviews = int(n.meta.get("reviews") or 0)
    if passed:
        fields = {"learned": True, "learned_on": _today(), "reviews": reviews + 1 if was_learned else 0}
    elif was_learned:
        fields = {"learned": False, "reviews": 0}
    else:
        fields = {}
    text = path.read_text(encoding="utf-8")
    kind = "review" if was_learned else "first"
    line = (f"- {_today()}: {'pass' if passed else 'fail'} ({kind}; {''.join(MARK[g] for g in grades)}) - "
            f"{takeaway.strip().splitlines()[0][:200]}")
    new = _append_log(_set_fields(text, fields) if fields else text, line)
    _commit(path, new, {"topic": n.name, "action": "record_quiz", "passed": passed, "kind": kind,
                        "grades": grades, "fields": fields})
    next_review = None
    if passed:
        next_review = (dt.date.today() + dt.timedelta(days=30 * 2 ** fields["reviews"])).isoformat()
    return {"topic": n.name, "passed": passed, "attempt": kind, "changed": fields, "next_review": next_review}


@mcp.tool(annotations=WRITE)
def mark_learned(topic: str, learned: bool, reason: str) -> dict:
    """Explicit override of my learning state. Use ONLY when I directly ask ("mark X learned").
    reason: why (e.g. "user said they already know it"). Logged in the note and the audit trail."""
    if not reason.strip():
        raise WriteRefused("reason is required")
    n, path = _note(topic)
    fields = {"learned": learned, "reviews": 0} | ({"learned_on": _today()} if learned else {})
    text = _set_fields(path.read_text(encoding="utf-8"), fields)
    text = _append_log(text, f"- {_today()}: marked {'learned' if learned else 'unlearned'} manually - {reason.strip()[:200]}")
    _commit(path, text, {"topic": n.name, "action": "mark_learned", "fields": fields, "reason": reason.strip()})
    return {"topic": n.name, "changed": fields}


if __name__ == "__main__":
    mcp.run()  # stdio
