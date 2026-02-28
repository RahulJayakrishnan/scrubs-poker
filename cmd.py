"""
Scrubs Poker Command Processor
Usage: python3 cmd.py <command> [args...]

Commands:
  new [chips] [blind]     — Create new game
  join <name> <number>    — Add player
  start                   — Deal first hand
  action <number> <text>  — Process player action (natural language)
  next_round              — Start next round after hand ends
  status                  — Print game status JSON
  showdown                — Resolve showdown
  reset                   — Delete game state

Output: JSON with keys: ok, error, messages (list of {target, text})
  target = "group" or a phone number for DM
"""

import sys
import json
import os
from engine import (
    new_game, start_round, process_action, resolve,
    parse_action, standings, fmt_cards, get_to_call, HAND_NAMES
)

STATE_PATH = os.path.join(os.path.dirname(__file__), "game-state.json")
GROUP_ID = "Q/y2ue+lbnPpG7HSMYwDbHFKGsLYxEwziRrkZBptTE4="


def normalize_number(number):
    if isinstance(number, str) and number.startswith("uuid:"):
        return number.split(":", 1)[1]
    return number


def load():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH) as f:
        state = json.load(f)

    # Backward compatibility: older states stored UUIDs as "uuid:<uuid>".
    for p in state.get("players", []):
        p["number"] = normalize_number(p.get("number"))

    return state


def save(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def out(ok=True, error=None, messages=None, data=None):
    print(json.dumps({
        "ok": ok,
        "error": error,
        "messages": messages or [],
        "data": data or {}
    }, indent=2))


def group_msg(text):
    return {"target": "group", "text": text}


def dm(number, text):
    return {"target": number, "text": text}


def current_player_info(state):
    p = state["players"][state["current_player"]]
    to_call = get_to_call(state)
    chips = p["chips"]
    options = []
    if to_call == 0:
        options = ["check", "bet", "all in", "fold"]
    else:
        options = [f"call {to_call}", "raise", "all in", "fold"]
    return p, to_call, options


def phase_label(phase):
    return {"pre_flop": "Pre-Flop", "flop": "Flop", "turn": "Turn", "river": "River"}.get(phase, phase.title())


def pot_bar(state):
    players = state["players"]
    pot = state["pot"]
    active = [p for p in players if not p["folded"]]
    chips_display = " | ".join(f"{p['name']}: {p['chips']}" for p in players if not p["folded"])
    return f"💰 Pot: {pot}  •  {chips_display}"


def community_display(state):
    c = state["community"]
    if not c:
        return ""
    phase = state["phase"]
    label = phase_label(phase)
    return f"🂠 {label}: {fmt_cards(c)}"


# ── Commands ────────────────────────────────────────────────────────────────

def cmd_new(args):
    chips = int(args[0]) if args else 1000
    blind = int(args[1]) if len(args) > 1 else 10
    state = new_game(starting_chips=chips, small_blind=blind)
    save(state)
    out(messages=[
        group_msg(
            f"🃏 *New poker game!* Starting chips: {chips} | Blinds: {blind}/{blind*2}\n"
            f"Reply *join* to play! When everyone's in, I'll deal."
        )
    ], data={"chips": chips, "blind": blind})


def cmd_join(args):
    if len(args) < 2:
        out(ok=False, error="Usage: join <name> <number>")
        return
    name, number = args[0], normalize_number(args[1])
    state = load()
    if not state:
        out(ok=False, error="No active game. Start one first.")
        return
    if state["phase"] != "waiting":
        out(ok=False, error="Game already started.")
        return
    # Check duplicate
    if any(p["number"] == number for p in state["players"]):
        out(ok=False, error=f"{name} is already in.")
        return

    from engine import new_game as _ng
    state["players"].append({
        "name": name,
        "number": number,
        "chips": state["starting_chips"],
        "hand": [],
        "bet": 0,
        "folded": False,
        "all_in": False,
        "acted": False
    })
    save(state)
    count = len(state["players"])
    out(messages=[
        group_msg(f"✅ *{name}* is in! ({count} player{'s' if count > 1 else ''} so far)")
    ])


def cmd_start(args):
    state = load()
    if not state:
        out(ok=False, error="No game loaded.")
        return
    if len(state["players"]) < 2:
        out(ok=False, error="Need at least 2 players.")
        return

    result = start_round(state)
    if "error" in result:
        out(ok=False, error=result["error"])
        return
    save(state)

    msgs = []

    # Group: announce deal
    player_list = ", ".join(p["name"] for p in state["players"])
    msgs.append(group_msg(
        f"🃏 *Round {state['round_num']} — Dealing!*\n"
        f"Players: {player_list}\n"
        f"Dealer: {result['dealer']} | SB: {result['sb']['name']} ({result['sb']['amount']}) | BB: {result['bb']['name']} ({result['bb']['amount']})\n"
        f"{pot_bar(state)}\n"
        f"Cards going out... check your DMs 🤫"
    ))

    # DM each player their hand
    for p in state["players"]:
        hand_str = fmt_cards(p["hand"])
        msgs.append(dm(p["number"], f"🃏 Your hand: *{hand_str}* 🤫\n(Keep it secret!)"))

    # Prompt first player
    cur, to_call, options = current_player_info(state)
    opts_str = " / ".join(options)
    msgs.append(group_msg(
        f"\n👉 *{cur['name']}'s turn* ({cur['chips']} chips)\n"
        f"Options: {opts_str}"
    ))

    out(messages=msgs, data={"round": state["round_num"]})


def cmd_action(args):
    if len(args) < 2:
        out(ok=False, error="Usage: action <number> <action text>")
        return

    number = normalize_number(args[0])
    action_text = " ".join(args[1:])
    state = load()
    if not state:
        out(ok=False, error="No active game.")
        return
    if state["phase"] not in ("pre_flop", "flop", "turn", "river"):
        out(ok=False, error=f"Not in a betting phase (current: {state['phase']})")
        return

    players = state["players"]
    cur_idx = state["current_player"]
    cur = players[cur_idx]

    # Validate it's this player's turn
    if normalize_number(cur["number"]) != number:
        # Not their turn — ignore silently
        expected = cur["name"]
        out(ok=False, error=f"Not {number}'s turn. Waiting for {expected}.")
        return

    parsed = parse_action(action_text)
    if not parsed:
        out(ok=False, error=f"Couldn't parse action: '{action_text}'")
        return

    action, amount = parsed
    result = process_action(state, action, amount)
    if "error" in result:
        out(ok=False, error=result["error"])
        return

    save(state)
    msgs = []
    msgs.append(group_msg(result["message"]))

    transition = result.get("transition")

    if transition and transition.get("phase") == "showdown":
        # Resolve
        if transition.get("reason") == "all_folded":
            results = resolve(state)
            save(state)
            winner = results[0]
            msgs.append(group_msg(
                f"🏆 *{winner['player']} wins {winner['won']} chips!* (everyone else folded)\n"
                f"{pot_bar(state)}"
            ))
            msgs.append(group_msg(_standings_msg(state)))
        else:
            # Showdown — reveal hands
            from engine import best_hand, HAND_NAMES as HN, fmt_cards as fc
            active = [p for p in players if not p["folded"]]
            reveal_lines = []
            for p in active:
                hand_str = fc(p["hand"])
                reveal_lines.append(f"{p['name']}: {hand_str}")
            community_str = fmt_cards(state["community"]) if state["community"] else ""
            msgs.append(group_msg(
                f"🂠 *Showdown!*\n"
                f"Board: {community_str}\n" +
                "\n".join(reveal_lines)
            ))
            results = resolve(state)
            save(state)
            for r in results:
                hand_cards = fmt_cards(r["cards"]) if r["cards"] else ""
                msgs.append(group_msg(
                    f"🏆 *{r['player']} wins {r['won']} chips!* — {r['hand']}"
                    + (f" ({hand_cards})" if hand_cards else "")
                ))
            msgs.append(group_msg(_standings_msg(state)))

        # Suggest next round
        still_in = [p for p in state["players"] if p["chips"] > 0]
        if len(still_in) > 1:
            msgs.append(group_msg("Type *next* to deal another round, or *end* to stop."))

    elif transition and transition.get("phase"):
        # New phase
        phase = transition["phase"]
        community_str = fmt_cards(state["community"])
        msgs.append(group_msg(
            f"\n🂠 *{phase_label(phase)}*: {community_str}\n"
            f"{pot_bar(state)}"
        ))
        cur2, to_call2, options2 = current_player_info(state)
        opts_str = " / ".join(options2)
        msgs.append(group_msg(
            f"👉 *{cur2['name']}'s turn* ({cur2['chips']} chips)\n"
            f"Options: {opts_str}"
        ))
    else:
        # Same phase, next player
        cur2, to_call2, options2 = current_player_info(state)
        opts_str = " / ".join(options2)
        msgs.append(group_msg(
            f"👉 *{cur2['name']}'s turn* ({cur2['chips']} chips)\n"
            f"{pot_bar(state)}\n"
            f"Options: {opts_str}"
        ))

    out(messages=msgs)


def _standings_msg(state):
    s = standings(state)
    lines = [f"  {i+1}. {p['name']}: {p['chips']} chips" for i, p in enumerate(s)]
    return "📊 *Standings:*\n" + "\n".join(lines)


def cmd_next_round(args):
    state = load()
    if not state:
        out(ok=False, error="No game.")
        return
    if state["phase"] not in ("ended", "showdown"):
        out(ok=False, error=f"Hand not over yet (phase: {state['phase']})")
        return

    result = start_round(state)
    if "error" in result:
        out(ok=False, error=result["error"])
        return
    save(state)

    msgs = []
    msgs.append(group_msg(
        f"🃏 *Round {state['round_num']}*\n"
        f"Dealer: {result['dealer']} | SB: {result['sb']['name']} ({result['sb']['amount']}) | BB: {result['bb']['name']} ({result['bb']['amount']})\n"
        f"{pot_bar(state)}\n"
        f"Dealing cards... check your DMs 🤫"
    ))

    for p in state["players"]:
        hand_str = fmt_cards(p["hand"])
        msgs.append(dm(p["number"], f"🃏 Round {state['round_num']} — Your hand: *{hand_str}* 🤫"))

    cur, to_call, options = current_player_info(state)
    opts_str = " / ".join(options)
    msgs.append(group_msg(
        f"\n👉 *{cur['name']}'s turn* ({cur['chips']} chips)\n"
        f"Options: {opts_str}"
    ))

    out(messages=msgs)


def cmd_status(args):
    state = load()
    if not state:
        out(ok=False, error="No active game.")
        return
    lines = [f"Phase: {state['phase']} | Round: {state['round_num']} | Pot: {state['pot']}"]
    lines.append(f"Community: {fmt_cards(state['community']) if state['community'] else '—'}")
    for i, p in enumerate(state["players"]):
        marker = "→" if i == state["current_player"] and state["phase"] not in ("waiting","ended") else " "
        status = "ALL IN" if p["all_in"] else ("FOLDED" if p["folded"] else f"chips: {p['chips']}")
        lines.append(f"{marker} {p['name']} ({status}) bet:{p['bet']}")
    out(messages=[group_msg("\n".join(lines))], data=state)


def cmd_showdown(args):
    state = load()
    if not state:
        out(ok=False, error="No game.")
        return
    state["phase"] = "showdown"
    save(state)
    # Re-run action which will trigger resolution
    out(ok=True, messages=[group_msg("Triggering showdown...")])


def cmd_reset(args):
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    out(messages=[group_msg("🃏 Game reset. Type *!poker new* to start a fresh game.")])


def cmd_end(args):
    state = load()
    if not state:
        out(ok=False, error="No game.")
        return

    active_phases = {"pre_flop", "flop", "turn", "river"}
    if state["phase"] in active_phases:
        # Don't end mid-hand — tell them to finish or use 'end game' to force
        force = args and args[0] == "--force"
        if not force:
            out(ok=False, error=f"Hand in progress ({state['phase']}). Finish the hand first, or type 'end game' to force quit.")
            return

    s = standings(state)
    msgs = [group_msg(
        "🎰 *Game over!* Final standings:\n" +
        "\n".join(f"  {i+1}. {p['name']}: {p['chips']} chips" for i, p in enumerate(s))
    )]
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    out(messages=msgs)


# ── Dispatch ─────────────────────────────────────────────────────────────────

COMMANDS = {
    "new": cmd_new,
    "join": cmd_join,
    "start": cmd_start,
    "action": cmd_action,
    "next": cmd_next_round,
    "next_round": cmd_next_round,
    "status": cmd_status,
    "showdown": cmd_showdown,
    "reset": cmd_reset,
    "end": cmd_end,
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: cmd.py <command> [args...]")
        sys.exit(1)
    cmd = sys.argv[1].lower()
    args = sys.argv[2:]
    fn = COMMANDS.get(cmd)
    if not fn:
        out(ok=False, error=f"Unknown command: {cmd}. Available: {list(COMMANDS)}")
    else:
        fn(args)
