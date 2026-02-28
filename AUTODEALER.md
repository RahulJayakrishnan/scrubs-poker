# Auto Dealer (No LLM in gameplay loop)

`auto_dealer.py` runs poker hands directly from Signal group messages.

## What it does

- Listens to `signal-cli` SSE stream: `GET /api/v1/events`
- Filters to one group (`POKER_GROUP_ID`)
- Parses messages into poker commands
- Runs `cmd.py`
- Sends resulting group + DM messages through `signal_send.py`

No Maya/LLM turn is needed for in-hand actions.

---

## Start it

```bash
cd ~/clawd/poker
python3 auto_dealer.py
```

## Run it as a background user service (recommended)

```bash
mkdir -p ~/.config/systemd/user
cp ~/clawd/poker/poker-autodealer.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now poker-autodealer.service
```

Helper:

```bash
~/clawd/poker/dealerctl.sh status
~/clawd/poker/dealerctl.sh tail 80
```

Optional:

```bash
POKER_GROUP_ID='Q/y2ue+lbnPpG7HSMYwDbHFKGsLYxEwziRrkZBptTE4=' python3 auto_dealer.py
```

Dry run (no sends):

```bash
python3 auto_dealer.py --dry-run
```

---

## Supported chat commands

- `new` / `new <chips> <blind>` / `poker` / `new game`
- `join` / `i'm in` / `add me`
- `start`
- `start with me, Rahul A and pavan`
- `next`
- `end`
- `status`
- `reset`
- In-hand actions: `call`, `check`, `fold`, `bet 100`, `raise 300`, `all in`, etc.

---

## Notes

- Listener state is stored at `~/clawd/poker/auto-dealer-state.json`.
- UUID recipients are normalized automatically (`uuid:<id>` or raw UUID both work).
- Group sends still use `signal_send.py` and your configured bot account.
