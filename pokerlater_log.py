"""Read a PokerLater session log (the "Download JSON log" file) as PokerNow log lines.

PokerLater exports `pokerlater-<date>-<slug>-log.json` with `format:
"pokerlater-session-log"`. This module turns it into the exact `entry,at,order`
CSV lines PokerNow writes, so regex_based_graph_night.py (and the Rust port,
which mirrors this file) parse a PokerLater night with the same code path as a
PokerNow one: rounds, profit history, stats and Splitwise.

What PokerLater does not log, and how it is filled in:

* Buy-ins, rebuys and stack changes between hands are not events in the
  export. They are inferred from each player's starting stack against where
  they ended the last hand they played: a first appearance is a buy-in, a
  larger stack is a rebuy of the difference, a smaller one is an admin
  adjustment (a host correction).
* Cash-outs are not hands either, so the night's final result per player is
  taken from the export's ledger (`net`, or `netCents` when the host set a
  chip value), which PokerLater already balances to zero.
* Winning hand names ("Two Pair", ...) are computed here from the shown cards
  and the board, because a hand tabled at an all-in carries no rank.
"""

import json
from datetime import datetime, timezone
from itertools import combinations

FORMAT = "pokerlater-session-log"

RANK_TEXT = {11: "J", 12: "Q", 13: "K", 14: "A"}
SUIT_TEXT = {0: "♣", 1: "♦", 2: "♥", 3: "♠"}
CATEGORY_NAMES = [
    "High Card",
    "Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
]
FORCED_BET_TEXT = {
    "smallBlind": "posts a small blind of",
    "bigBlind": "posts a big blind of",
    "straddle": "posts a straddle of",
    "ante": "posts an ante of",
}


def is_pokerlater_export(data):
    return isinstance(data, dict) and data.get("format") == FORMAT


def load_export(file):
    data = json.load(file)
    if not is_pokerlater_export(data):
        raise ValueError("Not a PokerLater session log (format is not pokerlater-session-log)")
    if data.get("version") != 1:
        raise ValueError(f"Unsupported PokerLater session log version {data.get('version')}")
    return data


def card_text(card):
    rank = card["rank"]
    return RANK_TEXT.get(rank, str(rank)) + SUIT_TEXT[card["suit"]]


def cards_text(cards):
    return ", ".join(card_text(card) for card in cards)


def _five_card_rank(cards):
    """(category, tiebreaks) for exactly five cards; larger is better."""
    ranks = sorted((card["rank"] for card in cards), reverse=True)
    counts = {}
    for rank in ranks:
        counts[rank] = counts.get(rank, 0) + 1
    by_count = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    flush = len({card["suit"] for card in cards}) == 1
    distinct = sorted(set(ranks), reverse=True)
    straight_high = None
    if len(distinct) == 5:
        if distinct[0] - distinct[4] == 4:
            straight_high = distinct[0]
        elif distinct == [14, 5, 4, 3, 2]:
            straight_high = 5
    if straight_high and flush:
        return (8, [straight_high])
    if by_count[0][1] == 4:
        return (7, [by_count[0][0], by_count[1][0]])
    if by_count[0][1] == 3 and by_count[1][1] == 2:
        return (6, [by_count[0][0], by_count[1][0]])
    if flush:
        return (5, ranks)
    if straight_high:
        return (4, [straight_high])
    if by_count[0][1] == 3:
        return (3, [rank for rank, _ in by_count])
    if by_count[0][1] == 2 and by_count[1][1] == 2:
        return (2, [rank for rank, _ in by_count])
    if by_count[0][1] == 2:
        return (1, [rank for rank, _ in by_count])
    return (0, ranks)


def best_hand(cards):
    """The best five of up to seven cards: (category, tiebreaks, five cards)."""
    best = None
    for five in combinations(cards, 5):
        category, tiebreaks = _five_card_rank(five)
        if best is None or (category, tiebreaks) > (best[0], best[1]):
            best = (category, tiebreaks, list(five))
    return best


def hand_description(cards):
    """PokerNow's winning-hand text: "Flush (combination: ...)", "A High (...)"."""
    category, tiebreaks, five = best_hand(cards)
    if category == 0:
        name = RANK_TEXT.get(tiebreaks[0], str(tiebreaks[0])) + " High"
    elif category == 8 and tiebreaks[0] == 14:
        name = "Royal Flush"
    else:
        name = CATEGORY_NAMES[category]
    five.sort(key=lambda card: card["rank"], reverse=True)
    return f"{name} (combination: {cards_text(five)})"


def _iso(at_ms):
    return (
        datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class _Lines:
    """Collects (entry, at_ms) pairs and stamps PokerNow's `order` column.

    PokerNow's order is in 1e-5 s units. Several PokerLater entries can share
    one millisecond (an all-in run-out deals the flop, turn and river at
    once), so each gets the next order value instead, keeping log order and
    keeping "before the flop" strictly before the flop's own timestamp.
    """

    def __init__(self):
        self.rows = []
        self.last_order = 0

    def add(self, entry, at_ms):
        order = max(at_ms * 100, self.last_order + 1)
        self.last_order = order
        self.rows.append((entry, _iso(order // 100), order))


def pokerlater_log_lines(data, normalize_name=lambda name: name):
    """The export as PokerNow CSV lines, newest first, without the header.

    `normalize_name` maps a display name to the group's canonical name; it is
    applied to names containing whitespace, which PokerNow's name regex
    cannot see (the line-based fix-up handles every other name).
    """
    merged_into = {}
    for line in data["ledger"]["lines"]:
        for merged in line.get("mergedNames", []):
            merged_into[merged] = line["name"]

    def resolve(name):
        name = merged_into.get(name, name)
        if any(ch.isspace() for ch in name):
            name = normalize_name(name)
        return name

    ids = {}

    def player(name):
        resolved = resolve(name)
        if resolved not in ids:
            ids[resolved] = f"PL{len(ids) + 1}"
        return f'"{resolved} @ {ids[resolved]}"'

    out = _Lines()
    last_stack = {}  # resolved name -> stack at the end of the last hand played
    hands = [hand for hand in data["hands"] if hand["final"]["phase"] == "complete"]

    for index, hand in enumerate(hands):
        started = hand["startedAt"]
        final = hand["final"]
        seat_name = {seat["seat"]: seat["name"] for seat in hand["seats"] if seat["name"]}

        movements = []
        for seat in hand["seats"]:
            if not seat["name"]:
                continue
            name = resolve(seat["name"])
            stack = seat["startingStack"]
            before = last_stack.get(name)
            if before is None:
                movements.append(f"The player {player(seat['name'])} joined the game with a stack of {stack}.")
            elif stack > before:
                movements.append(f"The player {player(seat['name'])} rebought. New stack {stack - before}.")
            elif stack < before:
                movements.append(
                    f"The admin updated the player {player(seat['name'])} stack from {before} to {stack}."
                )

        if index == 0:
            # The first hand has no round before it: its buy-ins go inside it,
            # ahead of the stacks line, where the parser books them first.
            first_movements = movements
        else:
            # Joins, rebuys and adjustments belong to the round BEFORE the hand
            # they first show in: the parser books those after that round's
            # profits, so they count from this hand on.
            first_movements = []
            for movement in movements:
                out.add(movement, started - 1)

        dealer = seat_name.get(final["button"])
        dealer_text = f" (dealer: {player(dealer)})" if dealer else ""
        out.add(
            f"-- starting hand #{hand['handNo']} (id: {hand['handId']})  No Limit Texas Hold'em{dealer_text} --",
            started,
        )
        for movement in first_movements:
            out.add(movement, started)
        stacks = " | ".join(
            f"#{seat['seat'] + 1} {player(seat['name'])} ({seat['startingStack']})"
            for seat in hand["seats"]
            if seat["name"]
        )
        out.add(f"Player stacks: {stacks}", started)

        shown = {}
        board = []
        live = set(seat_name)
        for entry in final["entries"]:
            kind = entry["kind"]
            at = entry["at"]
            name = seat_name.get(entry.get("seat"))
            if kind == "forcedBet" and name:
                out.add(f"{player(name)} {FORCED_BET_TEXT[entry['forced']]} {entry['amount']}", at)
            # `timeout` and `hostForceFold` are markers: the fold or check they
            # caused is logged right after them, so they add no line here.
            elif kind == "fold" and name:
                live.discard(entry["seat"])
                out.add(f"{player(name)} folds", at)
            elif kind == "check" and name:
                out.add(f"{player(name)} checks", at)
            elif kind in ("call", "bet", "raise") and name:
                verb = {"call": "calls", "bet": "bets", "raise": "raises to"}[kind]
                all_in = " and go all in" if entry["allIn"] else ""
                out.add(f"{player(name)} {verb} {entry['to']}{all_in}", at)
            elif kind == "uncalledReturn" and name:
                out.add(f"Uncalled bet of {entry['amount']} returned to {player(name)}", at)
            elif kind == "dealBoard" and entry.get("boardIndex", 0) == 0:
                new = entry["cards"]
                if entry["street"] == "flop":
                    out.add(f"Flop:  [{cards_text(new)}]", at)
                else:
                    label = "Turn" if entry["street"] == "turn" else "River"
                    out.add(f"{label}: {cards_text(board)} [{cards_text(new)}]", at)
                board = board + new
            elif kind == "showdownReveal" and name:
                shown[entry["seat"]] = (entry["cards"], entry["cause"])
                out.add(f"{player(name)} shows a {cards_text(entry['cards'])}.", at)
            elif kind == "potAward" and name:
                seat = entry["seat"]
                contested = len(live) > 1
                with_hand = ""
                if contested and seat in shown and len(board) == 5:
                    with_hand = f" with {hand_description(shown[seat][0] + board)}"
                out.add(f"{player(name)} collected {entry['amount']} from pot{with_hand}", at)
            elif kind == "handComplete":
                out.add(f"-- ending hand #{hand['handNo']} --", at)

        for seat in final["seats"]:
            name = seat_name.get(seat["seat"])
            if name:
                last_stack[resolve(name)] = seat["stack"]

    out.rows.reverse()
    return [
        '"{}",{},{}'.format(entry.replace('"', '""'), at, order)
        for entry, at, order in out.rows
    ]


def pokerlater_final_profits(data, normalize_name=lambda name: name):
    """Each player's result for the night, in cents, from the export's ledger.

    With a chip value set the ledger's own `netCents` is used, already balanced
    to zero. Without one, one chip is one cent, PokerNow's convention.
    Players whose names normalize to the same person are summed.
    """
    profits = {}
    for line in data["ledger"]["lines"]:
        cents = line.get("netCents")
        if cents is None:
            cents = line["net"]
        name = normalize_name(line["name"])
        profits[name] = profits.get(name, 0) + cents
    return profits


def cents_per_chip(data):
    rate = data.get("chipRate")
    if not rate:
        return 1.0
    return rate["cents"] / rate["chips"]


def session_end_ms(data):
    """One millisecond after the night's last logged moment."""
    last = data.get("startedAt") or 0
    for hand in data["hands"]:
        last = max(last, hand["startedAt"])
        for entry in hand["final"]["entries"]:
            last = max(last, entry["at"])
    return last + 1
