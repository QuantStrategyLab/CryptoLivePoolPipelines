"""Existing issue state helpers and a record-only research hook."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from scripts.advisory_review_loop import opinion, strict_json

STATE_PREFIX = "<!-- advisory-review-loop-v1 "


class IssueState:
    def __init__(self, issue, *, archive, owner="Pigbibi"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner):
            raise ValueError("invalid owner login")
        self.issue, self.archive, self.owner = issue, Path(archive), owner

    def load(self, identity):
        state = None
        for comment in self.issue.own_comments():
            body = comment.get("body", "")
            if body.startswith(STATE_PREFIX):
                value = strict_json(body[len(STATE_PREFIX):].split(" -->", 1)[0])
                if value.get("identity") == identity:
                    state = value
        return state

    def save(self, state):
        encoded = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False)
        encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e")
        self.archive.parent.mkdir(parents=True, exist_ok=True)
        self.archive.write_text(encoded + "\n", encoding="utf-8")
        self.issue.post(STATE_PREFIX + encoded + " -->")

    def post(self, body):
        marker = "<!-- advisory-loop-message:" + hashlib.sha256(body.encode()).hexdigest() + " -->"
        if not any(marker in item.get("body", "") for item in self.issue.own_comments()):
            self.issue.post(marker + "\n" + body)

    def close(self, state):
        self.post("Archived advisory verdict:\n```json\n" + json.dumps(state, sort_keys=True) + "\n```")
        self.issue.auto_close()

    def notify(self, message):
        self.post(f"@{self.owner} {message}")


def apply_monthly_loop(issue, results, *, identity, materials, archive, env=None, dot=None):
    """AI-5 shadow record only; no remote tasks, issue writes or actuator callbacks."""
    parsed = {role: opinion(text) for role, text in results}
    if set(parsed) != {"OpenAI", "Anthropic"} or not identity:
        raise ValueError("two complete reviews and cycle identity required")
    env = dict(os.environ if env is None else env)
    required = ("AI_SERVICE_URL", "AI_SERVICE_AUDIENCE", "AI_SERVICE_PROFILE", "AI_SERVICE_MODEL")
    configured = all(isinstance(env.get(name), str) and env[name].strip() for name in required)
    configured = (configured and env.get("AI_SERVICE_TARGET") == "quant-ai-service-v2"
                  and env.get("AI_HOOK_MODE") == "shadow")
    state = {"schema": "ai-hook-shadow.v1", "disposition": "shadow_recorded",
             "binding_status": "present_unverified" if configured else "blocked_configuration",
             "review_count": len(parsed), "flag_count": sum(value["verdict"] == "flag" for value in parsed.values()),
             "finished": False, "ai_submitted": False, "formal_actions_enabled": False}
    path = Path(archive)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(state, stream, sort_keys=True)
        stream.write("\n")
    return state
