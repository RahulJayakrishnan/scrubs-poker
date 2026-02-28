#!/usr/bin/env python3
"""
Autonomous Signal Poker dealer.

- Listens to signal-cli SSE events (`/api/v1/events`)
- Filters to one Signal group
- Converts chat messages -> poker commands
- Runs cmd.py and dispatches resulting group/DM messages via signal_send.py helpers

No LLM required during gameplay.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import requests

from engine import parse_action
from signal_send import GROUP_ID as DEFAULT_GROUP_ID, send_dm, send_group

DIR = os.path.dirname(os.path.abspath(__file__))
CMD_PATH = os.path.join(DIR, "cmd.py")
STATE_FILE = os.path.join(DIR, "auto-dealer-state.json")
EVENTS_URL = os.environ.get("SIGNAL_EVENTS_URL", "http://127.0.0.1:8080/api/v1/events")

ACTIVE_PHASES = {"pre_flop", "flop", "turn", "river"}


def normalize_group_id(group_id: Optional[str]) -> str:
    if not group_id:
        return ""
    g = group_id.strip()
    if g.startswith("group:"):
        g = g.split(":", 1)[1]
    g = g.replace("-", "+").replace("_", "/")
    return g.rstrip("=")


def same_group(a: Optional[str], b: Optional[str]) -> bool:
    return normalize_group_id(a) == normalize_group_id(b)


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def norm_name(name: str) -> str:
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9+\-\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def normalize_sender_id(raw: Optional[str]) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("uuid:"):
        return raw.split(":", 1)[1]
    return raw


class AutoDealer:
    def __init__(self, group_id: str, dry_run: bool = False):
        self.group_id = group_id
        self.dry_run = dry_run

        self.state = self._load_state()
        self.seen = deque(self.state.get("seen", []), maxlen=500)
        self.seen_set = set(self.seen)
        self.aliases: Dict[str, str] = self.state.get("aliases", {})
        self.id_to_name: Dict[str, str] = self.state.get("idToName", {})

    def _load_state(self) -> dict:
        if not os.path.exists(STATE_FILE):
            return {}
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self):
        payload = {
            "seen": list(self.seen),
            "aliases": self.aliases,
            "idToName": self.id_to_name,
        }
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, STATE_FILE)

    def _mark_seen(self, event_key: str):
        if event_key in self.seen_set:
            return
        if len(self.seen) == self.seen.maxlen:
            old = self.seen.popleft()
            self.seen_set.discard(old)
        self.seen.append(event_key)
        self.seen_set.add(event_key)

    def _remember_sender(self, sender_id: str, sender_name: str):
        sender_id = normalize_sender_id(sender_id)
        if not sender_id:
            return

        sender_name = (sender_name or "").strip() or sender_id
        self.id_to_name[sender_id] = sender_name

        keys = {norm_name(sender_name)}
        first = norm_name(sender_name).split(" ")[0] if norm_name(sender_name) else ""
        if first:
            keys.add(first)

        for k in keys:
            if k:
                self.aliases[k] = sender_id

    def _resolve_name(self, token: str, sender_id: str) -> Optional[str]:
        t = norm_name(token)
        if not t:
            return None
        if t in {"me", "myself", "i"}:
            return normalize_sender_id(sender_id)
        if t.startswith("uuid:"):
            return normalize_sender_id(t)
        if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", t):
            return t
        if t.startswith("+"):
            return t

        if t in self.aliases:
            return self.aliases[t]

        # Fallback: partial alias match.
        for alias, sid in self.aliases.items():
            if t in alias or alias in t:
                return sid
        return None

    def _run_cmd(self, command: str, args: List[str]) -> dict:
        proc = subprocess.run(
            [sys.executable, CMD_PATH, command] + args,
            capture_output=True,
            text=True,
            cwd=DIR,
        )

        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip() or f"cmd.py exited {proc.returncode}", "messages": []}

        try:
            data = json.loads(proc.stdout)
            return data
        except Exception:
            return {
                "ok": False,
                "error": f"Could not parse cmd.py output: {proc.stdout[:400]}",
                "messages": [],
            }

    def _dispatch(self, messages: List[dict]):
        for msg in messages:
            target = msg.get("target", "group")
            text = msg.get("text", "")
            if not text:
                continue

            if self.dry_run:
                print(f"[DRY] -> {target}: {text}")
                continue

            if target == "group":
                send_group(text)
            else:
                send_dm(target, text)

            time.sleep(0.25)

    def _run_and_dispatch(self, command: str, args: List[str], send_error: bool = True) -> dict:
        data = self._run_cmd(command, args)
        if data.get("ok"):
            self._dispatch(data.get("messages", []))
        elif send_error and data.get("error"):
            self._dispatch([{"target": "group", "text": f"❌ {data['error']}"}])
        return data

    def _game_state(self) -> Optional[dict]:
        data = self._run_cmd("status", [])
        if not data.get("ok"):
            return None
        return data.get("data")

    def _classify(self, text: str) -> Optional[Tuple[str, object]]:
        raw = (text or "").strip()
        t = norm_text(raw)

        if t.startswith("!poker"):
            t = t[len("!poker"):].strip() or "status"

        if t.startswith("start with "):
            return ("start_with", raw[len("start with "):].strip())

        m = re.match(r"^new(?:\s+(\d+))?(?:\s+(\d+))?$", t)
        if m:
            chips = m.group(1)
            blind = m.group(2)
            out = []
            if chips:
                out.append(chips)
            if blind:
                out.append(blind)
            return ("new", out)

        if t in {"poker", "poker game", "lets play poker", "let's play poker", "new game"}:
            return ("new", [])

        if t in {"join", "i'm in", "im in", "add me", "me in"}:
            return ("join_self", None)

        if t.startswith("join "):
            return ("join_name", raw[len("join "):].strip())

        if t in {"start", "deal", "deal cards", "deal hand"}:
            return ("start", None)

        if t in {"next", "next hand", "deal again", "another hand", "run it back"}:
            return ("next", None)

        if t in {"end game", "stop game", "wrap up", "game over", "force end"}:
            return ("end_force", None)

        if t == "end":
            return ("end", None)

        if t in {"status", "stacks", "score", "scores"}:
            return ("status", None)

        if t == "reset":
            return ("reset", None)

        return None

    def _is_action_like(self, text: str) -> bool:
        t = norm_text(text)
        if re.match(r"^(call|check|fold|raise|bet|all\s*in|allin|shove|push)\b", t):
            return True
        return parse_action(text) is not None

    def _extract_message(self, payload: dict) -> Optional[dict]:
        env = payload.get("envelope") or {}
        data_msg = env.get("dataMessage") or {}

        text = (data_msg.get("message") or "").strip()
        if not text:
            return None

        group_info = data_msg.get("groupInfo") or {}
        group_id = group_info.get("groupId")

        sender = normalize_sender_id(
            env.get("sourceNumber")
            or env.get("sourceUuid")
            or env.get("source")
            or ""
        )

        if not sender:
            return None

        return {
            "sender": sender,
            "senderName": (env.get("sourceName") or sender).strip(),
            "text": text,
            "groupId": group_id,
            "timestamp": env.get("timestamp") or env.get("serverReceivedTimestamp") or 0,
        }

    def _handle_start_with(self, sender: str, sender_name: str, names_blob: str):
        game = self._game_state()
        if not game:
            self._run_and_dispatch("new", [])
            game = self._game_state()

        if game and game.get("phase") != "waiting":
            self._dispatch([
                {
                    "target": "group",
                    "text": f"❌ Can only use 'start with ...' before dealing (current: {game.get('phase')}).",
                }
            ])
            return

        parts = [p.strip() for p in re.split(r",|\band\b|&", names_blob, flags=re.IGNORECASE) if p.strip()]
        if not parts:
            self._dispatch([{"target": "group", "text": "❌ Give me player names after 'start with'."}])
            return

        unresolved = []
        resolved = []
        seen = set()

        for p in parts:
            sid = self._resolve_name(p, sender)
            if not sid:
                unresolved.append(p)
                continue
            if sid in seen:
                continue
            seen.add(sid)
            name = self.id_to_name.get(sid) or (sender_name if sid == sender else p.strip())
            resolved.append((name, sid))

        if unresolved:
            self._dispatch([
                {
                    "target": "group",
                    "text": "❌ Couldn’t resolve: " + ", ".join(unresolved),
                }
            ])
            return

        for name, sid in resolved:
            data = self._run_cmd("join", [name, sid])
            if data.get("ok"):
                self._dispatch(data.get("messages", []))
            else:
                err = data.get("error", "")
                if "already in" not in err.lower():
                    self._dispatch([{"target": "group", "text": f"❌ {err}"}])

        self._run_and_dispatch("start", [])

    def _handle_message(self, msg: dict):
        sender = msg["sender"]
        sender_name = msg["senderName"]
        text = msg["text"]

        self._remember_sender(sender, sender_name)

        cmd = self._classify(text)
        game = self._game_state()

        if cmd:
            kind, payload = cmd

            if kind == "new":
                self._run_and_dispatch("new", payload or [])
                return

            if kind == "join_self":
                if not game:
                    self._run_and_dispatch("new", [])
                self._run_and_dispatch("join", [sender_name, sender])
                return

            if kind == "join_name":
                sid = self._resolve_name(str(payload), sender)
                if not sid:
                    self._dispatch([{"target": "group", "text": f"❌ Couldn’t resolve player: {payload}"}])
                    return
                name = self.id_to_name.get(sid) or str(payload)
                if not game:
                    self._run_and_dispatch("new", [])
                self._run_and_dispatch("join", [name, sid])
                return

            if kind == "start_with":
                self._handle_start_with(sender, sender_name, str(payload))
                return

            if kind == "start":
                if not game:
                    self._run_and_dispatch("new", [])
                    self._run_and_dispatch("join", [sender_name, sender], send_error=False)
                self._run_and_dispatch("start", [])
                return

            if kind == "next":
                self._run_and_dispatch("next", [])
                return

            if kind == "end":
                self._run_and_dispatch("end", [])
                return

            if kind == "end_force":
                self._run_and_dispatch("end", ["--force"])
                return

            if kind == "status":
                self._run_and_dispatch("status", [])
                return

            if kind == "reset":
                self._run_and_dispatch("reset", [])
                return

        phase = game.get("phase") if game else None
        if phase in ACTIVE_PHASES and self._is_action_like(text):
            self._run_and_dispatch("action", [sender, text])

    def run(self):
        print(f"[auto-dealer] listening on {EVENTS_URL}")
        print(f"[auto-dealer] group filter: {self.group_id}")
        if self.dry_run:
            print("[auto-dealer] DRY RUN mode")

        while True:
            try:
                with requests.get(EVENTS_URL, stream=True, timeout=(5, 300)) as r:
                    r.raise_for_status()
                    event_name = None

                    for raw_line in r.iter_lines(decode_unicode=True):
                        if raw_line is None:
                            continue
                        line = raw_line.strip("\r")
                        if not line:
                            continue

                        if line.startswith("event:"):
                            event_name = line.split(":", 1)[1].strip()
                            continue

                        if not line.startswith("data:"):
                            continue

                        if event_name and event_name != "receive":
                            continue

                        data = line.split(":", 1)[1].strip()
                        if not data:
                            continue

                        try:
                            payload = json.loads(data)
                        except Exception:
                            continue

                        msg = self._extract_message(payload)
                        if not msg:
                            continue

                        if not same_group(msg.get("groupId"), self.group_id):
                            continue

                        event_key = f"{msg['sender']}|{msg['timestamp']}|{msg['text']}"
                        if event_key in self.seen_set:
                            continue

                        self._mark_seen(event_key)
                        self._save_state()

                        print(f"[auto-dealer] {msg['senderName']}: {msg['text']}")
                        self._handle_message(msg)

            except KeyboardInterrupt:
                print("\n[auto-dealer] stopped")
                return
            except Exception as e:
                print(f"[auto-dealer] reconnecting after error: {e}")
                time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Signal Poker dealer")
    parser.add_argument("--group-id", default=os.environ.get("POKER_GROUP_ID", DEFAULT_GROUP_ID), help="Target Signal group ID")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without sending messages")
    args = parser.parse_args()

    dealer = AutoDealer(group_id=args.group_id, dry_run=args.dry_run)
    dealer.run()


if __name__ == "__main__":
    main()
