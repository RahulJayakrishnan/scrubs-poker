"""
Sends poker messages via signal-cli RPC.
Usage: python3 signal_send.py <messages_json>
  messages_json: JSON list of {target, text}
  target: "group" | E.164 phone number

Configure via env vars:
  SIGNAL_BOT_ACCOUNT — bot's Signal number (E.164)
  POKER_GROUP_ID     — base64 Signal group ID
"""

import os
import sys
import json
import requests

SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
BOT_ACCOUNT = os.environ.get("SIGNAL_BOT_ACCOUNT", "")
GROUP_ID = os.environ.get("POKER_GROUP_ID", "")


def send_rpc(method, params):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    try:
        r = requests.post(SIGNAL_RPC, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def send_group(text):
    return send_rpc("send", {
        "account": BOT_ACCOUNT,
        "groupId": GROUP_ID,
        "message": text
    })


def normalize_recipient(recipient):
    if isinstance(recipient, str) and recipient.startswith("uuid:"):
        return recipient.split(":", 1)[1]
    return recipient


def send_dm(number, text):
    number = normalize_recipient(number)
    return send_rpc("send", {
        "account": BOT_ACCOUNT,
        "recipients": [number],
        "message": text
    })


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: signal_send.py '<messages_json>'")
        sys.exit(1)

    messages = json.loads(sys.argv[1])
    results = []
    for msg in messages:
        target = msg.get("target", "group")
        text = msg.get("text", "")
        if target == "group":
            r = send_group(text)
        else:
            r = send_dm(target, text)
        results.append({"target": target, "result": r})
        # Small delay between messages
        import time
        time.sleep(0.3)

    print(json.dumps(results, indent=2))
