#!/usr/bin/env python3
"""
Poker one-liner: runs cmd.py and dispatches all messages via Signal automatically.
Usage: python3 poker.py <command> [args...]
"""

import sys
import json
import subprocess
import os

DIR = os.path.dirname(os.path.abspath(__file__))


def run(command, args):
    result = subprocess.run(
        ["python3", os.path.join(DIR, "cmd.py"), command] + args,
        capture_output=True, text=True, cwd=DIR
    )
    if result.returncode != 0:
        print(f"cmd.py error: {result.stderr}", file=sys.stderr)
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Bad output: {result.stdout}", file=sys.stderr)
        return None

    if not data.get("ok") and data.get("error"):
        print(f"❌ Error: {data['error']}", file=sys.stderr)
        return data

    messages = data.get("messages", [])
    if messages:
        send_result = subprocess.run(
            ["python3", os.path.join(DIR, "signal_send.py"), json.dumps(messages)],
            capture_output=True, text=True, cwd=DIR
        )
        if send_result.returncode != 0:
            print(f"signal_send error: {send_result.stderr}", file=sys.stderr)
        else:
            sent = json.loads(send_result.stdout) if send_result.stdout else []
            for s in sent:
                tgt = s.get("target", "group")
                print(f"  ✓ sent → {tgt}")

    print(f"✅ {command}: ok")
    if data.get("data"):
        print(json.dumps(data["data"], indent=2))

    return data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: poker.py <command> [args...]")
        print("Commands: new, join, start, action, next, status, end, reset")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]
    run(cmd, args)
