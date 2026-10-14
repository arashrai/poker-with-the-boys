# poker-with-the-boys

How to add a graph:

1. Add CSV file to the `logs/` folder and give it a name like `poker_night_20230413.csv`.
2. Run `python3 regex_based_graph_night.py --date 20230413`.
3. Push everything.


For a PokerLater game (instead of PokerNow):

1. At the end of the session, click **Download JSON log** on the results screen.
2. Save it to `logs/` as `poker_night_YYYYMMDD.json` (or pass PokerLater's own file name,
   `pokerlater-2026-10-21-<slug>-log.json`, straight to `--date`).
3. Run the same commands: `python3 regex_based_graph_night.py --date 20261021` or
   `cargo run -- --date 20261021`. `--all` picks up `.json` nights alongside the `.csv` ones.

`pokerlater_log.py` (and its Rust twin, `src/pokerlater.rs`) turns the PokerLater log into
PokerNow-style lines, so the stats, graphs and Splitwise steps are unchanged. Buy-ins and
rebuys are inferred from stacks between hands. Each player's final result for the night
comes from PokerLater's balanced ledger, in cents: it uses the host's chip value when one
was set, otherwise 1 chip = 1 cent, as on PokerNow. Names still go through
`KNOWN_NAME_FIX_UPS`.

Or use the rust version:
2. Run `cargo run -- --date 20230413`.
3. For Splitwise, export `SPLITWISE_API_TOKEN` and run
   `cargo run -- --date 20230413 --splitwise`.

To append the single game night to Splitwise, set an API token and opt in:

1. Export `SPLITWISE_API_TOKEN` with a Splitwise API bearer token.
2. Run `python3 regex_based_graph_night.py --date 20230413 --splitwise`.

The script lists `Poker Night` groups, uses normalized player names in the member
mapping prompts, and creates one CAD expense dated to the CSV game night.
If that dated expense already exists in the group, it logs a warning and skips it.
When multiple groups start with `Poker Night`, the script prompts you to choose one.
It then lists that group's member emails and prompts for the email corresponding to
each normalized poker player. Known players are mapped automatically; unmapped
players are prompted interactively.
