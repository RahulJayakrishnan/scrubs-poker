"""
Scrubs Poker Engine — Texas Hold'em
Pure game logic, no I/O.
"""

import random
import json
from itertools import combinations

SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
RANK_VALUES = {r: i for i, r in enumerate(RANKS)}
RANK_DISPLAY = {'T': '10', 'J': 'J', 'Q': 'Q', 'K': 'K', 'A': 'A'}

HAND_NAMES = {
    9: 'Royal Flush', 8: 'Straight Flush', 7: 'Four of a Kind',
    6: 'Full House', 5: 'Flush', 4: 'Straight', 3: 'Three of a Kind',
    2: 'Two Pair', 1: 'One Pair', 0: 'High Card'
}

PHASES = ['waiting', 'pre_flop', 'flop', 'turn', 'river', 'showdown', 'ended']


def make_deck():
    return [f"{r}{s}" for s in SUITS for r in RANKS]


def fmt_card(card):
    r = card[:-1]
    s = card[-1]
    return RANK_DISPLAY.get(r, r) + s


def fmt_cards(cards):
    return ' '.join(fmt_card(c) for c in cards)


def card_rank(card):
    return RANK_VALUES[card[:-1]]


def card_suit(card):
    return card[-1]


def score_five(cards):
    ranks = sorted([card_rank(c) for c in cards], reverse=True)
    suits = [card_suit(c) for c in cards]
    is_flush = len(set(suits)) == 1
    unique_ranks = sorted(set(ranks), reverse=True)
    is_straight = len(unique_ranks) == 5 and (unique_ranks[0] - unique_ranks[4] == 4)
    # Wheel: A-2-3-4-5
    if unique_ranks == [12, 3, 2, 1, 0]:
        is_straight = True
        ranks = [3, 2, 1, 0, -1]

    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    counts = sorted(rank_counts.values(), reverse=True)
    by_freq = sorted(rank_counts.keys(), key=lambda r: (rank_counts[r], r), reverse=True)

    if is_flush and is_straight:
        return (9 if max(ranks) == 12 else 8, ranks)
    if counts[0] == 4:
        return (7, by_freq)
    if counts == [3, 2]:
        return (6, by_freq)
    if is_flush:
        return (5, ranks)
    if is_straight:
        return (4, ranks)
    if counts[0] == 3:
        return (3, by_freq)
    if counts[:2] == [2, 2]:
        return (2, by_freq)
    if counts[0] == 2:
        return (1, by_freq)
    return (0, ranks)


def best_hand(hole_cards, community):
    all_cards = hole_cards + community
    best = None
    for combo in combinations(all_cards, min(5, len(all_cards))):
        s = score_five(list(combo))
        if best is None or s > best:
            best = s
    return best


def new_game(starting_chips=1000, small_blind=10):
    return {
        "phase": "waiting",
        "players": [],
        "deck": [],
        "community": [],
        "pot": 0,
        "current_player": 0,
        "dealer_idx": -1,
        "small_blind": small_blind,
        "big_blind": small_blind * 2,
        "starting_chips": starting_chips,
        "round_num": 0,
        "current_bet": 0,
        "min_raise": small_blind * 2,
        "last_aggressor": -1,
    }


def start_round(state):
    players = state["players"]
    n = len(players)
    # Remove busted players
    state["players"] = [p for p in players if p["chips"] > 0]
    players = state["players"]
    n = len(players)

    if n < 2:
        return {"error": "Not enough players"}

    state["dealer_idx"] = (state["dealer_idx"] + 1) % n
    state["round_num"] += 1

    for p in players:
        p["hand"] = []
        p["bet"] = 0
        p["folded"] = False
        p["all_in"] = False
        p["acted"] = False

    deck = make_deck()
    random.shuffle(deck)
    state["deck"] = deck
    state["community"] = []
    state["pot"] = 0
    state["current_bet"] = 0
    state["min_raise"] = state["big_blind"]

    # Deal 2 each
    for _ in range(2):
        for p in players:
            p["hand"].append(state["deck"].pop())

    sb_idx = (state["dealer_idx"] + 1) % n
    bb_idx = (state["dealer_idx"] + 2) % n

    sb = min(state["small_blind"], players[sb_idx]["chips"])
    bb = min(state["big_blind"], players[bb_idx]["chips"])

    players[sb_idx]["chips"] -= sb
    players[sb_idx]["bet"] = sb
    if players[sb_idx]["chips"] == 0:
        players[sb_idx]["all_in"] = True

    players[bb_idx]["chips"] -= bb
    players[bb_idx]["bet"] = bb
    if players[bb_idx]["chips"] == 0:
        players[bb_idx]["all_in"] = True

    state["pot"] = sb + bb
    state["current_bet"] = bb
    state["min_raise"] = bb
    state["last_aggressor"] = bb_idx

    # UTG acts first pre-flop
    utg = (bb_idx + 1) % n
    state["current_player"] = _next_active(state, utg, exclude_allins=True)
    state["phase"] = "pre_flop"

    return {
        "dealer": players[state["dealer_idx"]]["name"],
        "sb": {"name": players[sb_idx]["name"], "amount": sb},
        "bb": {"name": players[bb_idx]["name"], "amount": bb},
        "hands": {p["name"]: p["hand"] for p in players},
        "pot": state["pot"]
    }


def _next_active(state, start_idx, exclude_allins=True):
    players = state["players"]
    n = len(players)
    for i in range(n):
        idx = (start_idx + i) % n
        p = players[idx]
        if p["folded"]:
            continue
        if exclude_allins and p["all_in"]:
            continue
        return idx
    return -1


def get_to_call(state):
    cur = state["players"][state["current_player"]]
    return state["current_bet"] - cur["bet"]


def process_action(state, action, amount=0):
    players = state["players"]
    cur_idx = state["current_player"]
    cur = players[cur_idx]

    to_call = get_to_call(state)

    if action == "fold":
        cur["folded"] = True
        cur["acted"] = True
        msg = f"{cur['name']} folds 🏳️"

    elif action == "check":
        if to_call > 0:
            return {"error": f"Can't check — need to call {to_call} or fold"}
        cur["acted"] = True
        msg = f"{cur['name']} checks ✓"

    elif action == "call":
        amt = min(to_call, cur["chips"])
        cur["chips"] -= amt
        cur["bet"] += amt
        state["pot"] += amt
        if cur["chips"] == 0:
            cur["all_in"] = True
            msg = f"{cur['name']} calls {amt} (all in) 🔥"
        else:
            msg = f"{cur['name']} calls {amt}"
        cur["acted"] = True

    elif action in ("raise", "bet"):
        total_raise = amount
        if total_raise < state["min_raise"]:
            total_raise = state["min_raise"]
        new_total_bet = state["current_bet"] + total_raise
        needed = new_total_bet - cur["bet"]
        actual = min(needed, cur["chips"])
        cur["chips"] -= actual
        cur["bet"] += actual
        state["pot"] += actual
        state["current_bet"] = cur["bet"]
        state["min_raise"] = actual - to_call  # raise amount
        state["last_aggressor"] = cur_idx
        if cur["chips"] == 0:
            cur["all_in"] = True
            msg = f"{cur['name']} raises to {cur['bet']} (all in) 🔥"
        else:
            msg = f"{cur['name']} raises to {cur['bet']} 💰"
        cur["acted"] = True
        # Reset others' acted
        for i, p in enumerate(players):
            if i != cur_idx and not p["folded"] and not p["all_in"]:
                p["acted"] = False

    elif action == "all_in":
        amt = cur["chips"]
        cur["bet"] += amt
        cur["chips"] = 0
        state["pot"] += amt
        cur["all_in"] = True
        cur["acted"] = True
        if cur["bet"] > state["current_bet"]:
            state["current_bet"] = cur["bet"]
            state["min_raise"] = amt - to_call
            state["last_aggressor"] = cur_idx
            for i, p in enumerate(players):
                if i != cur_idx and not p["folded"] and not p["all_in"]:
                    p["acted"] = False
        msg = f"{cur['name']} is ALL IN 🔥 ({amt})"

    else:
        return {"error": f"Unknown action: {action}"}

    # Advance turn
    transition = _advance(state)
    return {"message": msg, "pot": state["pot"], "transition": transition}


def _advance(state):
    """Move to next player or next phase. Returns phase change info if any."""
    players = state["players"]
    active = [p for p in players if not p["folded"]]

    # Only one left — they win
    if len(active) == 1:
        state["phase"] = "showdown"
        return {"phase": "showdown", "reason": "all_folded"}

    # Check if betting complete
    can_act = [p for p in active if not p["all_in"]]

    # If everyone remaining is all-in, or only one non-all-in player remains and
    # they've already matched the current bet, there is no further betting action.
    if len(can_act) == 0:
        betting_done = True
    elif len(can_act) == 1 and can_act[0]["bet"] == state["current_bet"]:
        betting_done = True
    else:
        betting_done = all(
            p["acted"] and p["bet"] == state["current_bet"]
            for p in can_act
        )

    if betting_done:
        return _next_phase(state)

    # Find next player
    n = len(players)
    cur = state["current_player"]
    nxt = _next_active(state, (cur + 1) % n)
    if nxt == -1:
        return _next_phase(state)
    state["current_player"] = nxt
    return None


def _next_phase(state):
    deck = state["deck"]
    for p in state["players"]:
        p["bet"] = 0
        p["acted"] = False
    state["current_bet"] = 0
    state["min_raise"] = state["big_blind"]

    phase_map = {
        "pre_flop": ("flop", 3),
        "flop": ("turn", 1),
        "turn": ("river", 1),
        "river": ("showdown", 0),
    }

    if state["phase"] not in phase_map:
        state["phase"] = "showdown"
        return {"phase": "showdown"}

    new_phase, draw = phase_map[state["phase"]]
    for _ in range(draw):
        state["community"].append(deck.pop())
    state["phase"] = new_phase

    if new_phase == "showdown":
        return {"phase": "showdown"}

    active = [p for p in state["players"] if not p["folded"]]
    can_act = [p for p in active if not p["all_in"]]

    # When there's no betting possible (everyone all-in, or only one non-all-in
    # player left), auto-run the board out to showdown.
    if len(active) > 1 and len(can_act) <= 1:
        return _next_phase(state)

    # First to act post-flop: left of dealer
    n = len(state["players"])
    start = (state["dealer_idx"] + 1) % n
    nxt = _next_active(state, start)
    if nxt == -1:
        state["phase"] = "showdown"
        return {"phase": "showdown"}
    state["current_player"] = nxt
    return {"phase": new_phase, "community": state["community"]}


def resolve(state):
    players = state["players"]
    community = state["community"]
    active = [p for p in players if not p["folded"]]

    if len(active) == 1:
        w = active[0]
        w["chips"] += state["pot"]
        state["phase"] = "ended"
        return [{"player": w["name"], "hand": "Last standing", "cards": [], "won": state["pot"]}]

    for p in active:
        p["_score"] = best_hand(p["hand"], community)
        p["_hand_name"] = HAND_NAMES[p["_score"][0]]

    best = max(p["_score"] for p in active)
    winners = [p for p in active if p["_score"] == best]
    split = state["pot"] // len(winners)
    remainder = state["pot"] % len(winners)

    results = []
    for i, w in enumerate(winners):
        won = split + (remainder if i == 0 else 0)
        w["chips"] += won
        results.append({
            "player": w["name"],
            "hand": w["_hand_name"],
            "cards": w["hand"],
            "won": won
        })

    state["phase"] = "ended"
    return results


def parse_action(text):
    """Parse natural language → (action, amount). Returns None if not a poker action.

    Strict mode: only match unambiguous poker commands to avoid false positives
    from regular group chat conversation.
    """
    import re
    t = text.lower().strip()

    # Bail out on messages over 10 characters — almost certainly conversation
    if len(t) > 10:
        return None

    # Fold — explicit poker language only; drop ambiguous "i'm out", "i pass", "not playing"
    fold_patterns = [
        r'\bfold\b',    # "fold", "i fold", "folding"
        r'\bmuck\b',    # "muck it"
    ]
    if any(re.search(p, t) for p in fold_patterns):
        return ('fold', 0)

    # All-in — require explicit "all in"/"all-in"/"allin"; drop "shove"/"push" (too ambiguous)
    allin_patterns = [r'\ball[\s-]?in\b', r'\ballin\b', r'\bgoing all\b']
    if any(re.search(p, t) for p in allin_patterns):
        return ('all_in', 0)

    # Call — explicit "call" only; exclude "call me/you/him/her/us/them/it" (non-poker)
    call_patterns = [
        r'\bcall(?!\s+(me|you|him|her|us|them|it|back|later|again|when))\b',
        r"\bi'?ll call\b",
        r'\bcalling\b',
        r'\bi call\b',
    ]
    if any(re.search(p, t) for p in call_patterns):
        return ('call', 0)

    # Check — must be the entire message or a very short phrase; not "check this out" etc.
    check_patterns = [r'^i?\s*check[\s.,!]*$', r'^checking[\s.,!]*$']
    if any(re.search(p, t) for p in check_patterns):
        return ('check', 0)

    # Raise / bet — "bet" requires a number to avoid "I bet he's bluffing" false-fires
    raise_patterns = [
        r'\braise\b',            # "raise", "raise 100", "i raise"
        r'\bbump\b',             # "bump it to 200"
        r'\bmake it\b',          # "make it 150"
        r'\bbet\s+\d+\b',        # "bet 100" only when a number follows
        r'\bi bet\s+\d+\b',      # "i bet 50"
    ]
    if any(re.search(p, t) for p in raise_patterns):
        nums = re.findall(r'\d+', t)
        amount = int(nums[-1]) if nums else 0
        return ('raise', amount)

    return None


def standings(state):
    return sorted(
        [{"name": p["name"], "chips": p["chips"]} for p in state["players"]],
        key=lambda x: -x["chips"]
    )
