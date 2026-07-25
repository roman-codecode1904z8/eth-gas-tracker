import argparse
import sys
import time
from eth_gas_tracker.storage import GasDatabase
from eth_gas_tracker.rpc import EthereumClient
from eth_gas_tracker.analyzer import ActionCostEstimator

# Quick ANSI colors without pulling in click/rich
GREEN = "\032[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def parse_args(args=None):
    """Parse CLI options for tracking, watching, and gas reporting."""
    parser = argparse.ArgumentParser(
        prog="eth-gas-tracker",
        description="Track, profile, and time transaction costs for on-chain game contracts",
    )
    parser.add_argument("--db", default="gas_tracker.db", help="Path to sqlite db")
    parser.add_argument("--rpc", default="http://127.0.0.1:8545", help="RPC endpoint URL")
    parser.add_argument("--no-color", action="store_true", help="Disable colored terminal output")

    subs = parser.add_subparsers(dest="command", required=True)

    # record
    rec = subs.add_parser("record", help="Record gas used by a transaction receipt")
    rec.add_argument("tx_hash", help="Transaction hash")
    rec.add_argument("--game", required=True, help="Game name or contract label")
    rec.add_argument("--action", default="unknown", help="In-game action (e.g. mint, craft, attack)")
    rec.add_argument("--notes", default="", help="Optional run notes / target account")

    # summary
    sum_p = subs.add_parser("summary", help="Print aggregated gas burn breakdown per action")
    sum_p.add_argument("--game", help="Filter by game label")
    sum_p.add_argument("--action", help="Filter by action name")

    # estimate
    est = subs.add_parser("estimate", help="Estimate current action cost in USD and ETH")
    est.add_argument("--game", required=True, help="Game label")
    est.add_argument("--action", required=True, help="Action to estimate")
    est.add_argument("--eth-price", type=float, default=0.0, help="ETH price in USD for fiat conversion")

    # watch
    watch = subs.add_parser("watch", help="Poll pending base fee and flag cheap execution windows")
    watch.add_argument("--interval", type=float, default=4.0, help="Poll interval in seconds")
    watch.add_argument("--target-gwei", type=float, default=20.0, help="Threshold for green alert")
    watch.add_argument("--game", help="Optional game to calculate live action price against")
    watch.add_argument("--action", help="Action name when game is specified")

    return parser.parse_args(args)


def _format_gwei(val: float, target: float, no_color: bool) -> str:
    txt = f"{val:6.2f} gwei"
    if no_color:
        return txt
    if val <= target:
        return f"{GREEN}{BOLD}{txt}{RESET}"
    elif val <= target * 1.5:
        return f"{YELLOW}{txt}{RESET}"
    return f"{RED}{txt}{RESET}"


def run_watch(client: EthereumClient, db: GasDatabase, args):
    target = args.target_gwei
    no_color = args.no_color
    avg_gas = 0

    if args.game and args.action:
        est = ActionCostEstimator(db)
        stats = est.get_action_stats(args.game, args.action)
        if stats:
            avg_gas = stats["avg_gas"]
            print(f"Tracking target action {CYAN}{args.game}:{args.action}{RESET} (~{avg_gas:,} gas)")

    print(f"Polling base fee every {args.interval}s (alert target <= {target:.1f} gwei). Ctrl+C to stop.\n")
    print(f"{'Time':<10} {'Block':<10} {'Base Fee':<18} {'Priority (rec)':<16} {'Est Action Cost'}")
    print("-" * 72)

    # TODO: add ws/subscription mode so we don't spam getBlockByNumber
    while True:
        try:
            block = client.get_latest_block()
            # print(f"DEBUG: raw block -> {block}")
            base_fee = int(block.get("baseFeePerGas", "0x0"), 16) / 1e9
            block_num = int(block["number"], 16)
            curr_time = time.strftime("%H:%M:%S")

            # heuristic for priority fee tip on mainnet/l2
            priority_fee = 1.5 if base_fee > 10 else 0.5
            total_gas_price = base_fee + priority_fee

            fee_str = _format_gwei(total_gas_price, target, no_color)

            cost_str = "-"
            if avg_gas > 0:
                cost_eth = (avg_gas * total_gas_price * 1e9) / 1e18
                cost_str = f"{cost_eth:.6f} ETH"
                if total_gas_price <= target and not no_color:
                    cost_str = f"{GREEN}{BOLD}{cost_str}{RESET}"

            print(f"{curr_time:<10} {block_num:<10} {fee_str:<27} {priority_fee:6.2f} gwei      {cost_str}")
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped watching.")
            break
        except Exception as e:
            print(f"Error fetching block: {e}", file=sys.stderr)
            time.sleep(args.interval)


def main(argv=None):
    args = parse_args(argv)
    db = GasDatabase(args.db)

    if args.command == "record":
        client = EthereumClient(args.rpc)
        receipt = client.get_transaction_receipt(args.tx_hash)
        if not receipt:
            print(f"Error: transaction {args.tx_hash} not found or still pending", file=sys.stderr)
            return 1

        gas_used = int(receipt["gasUsed"], 16)
        effective_price = int(receipt.get("effectiveGasPrice", "0x0"), 16)
        
        # FIXME: handle Arbitrum / Optimism L1 rollup data fee calldata breakdown
        cost_eth = (gas_used * effective_price) / 1e18

        db.insert_action(
            game=args.game,
            action=args.action,
            tx_hash=args.tx_hash,
            gas_used=gas_used,
            gas_price_gwei=effective_price / 1e9,
            total_eth=cost_eth,
            notes=args.notes,
        )
        print(f"Recorded {args.action} on {args.game}: {gas_used:,} gas ({cost_eth:.6f} ETH)")
        return 0

    elif args.command == "summary":
        rows = db.get_summary(game=args.game, action=args.action)
        if not rows:
            print("No records found.")
            return 0

        print(f"{'Game':<16} {'Action':<18} {'Calls':<8} {'Avg Gas':<12} {'Min Gas':<10} {'Max Gas':<10} {'Total ETH':<12}")
        print("-" * 90)
        for r in rows:
            print(
                f"{r['game']:<16} {r['action']:<18} "
                f"{r['count']:<8} {r['avg_gas']:<12,.0f} {r['min_gas']:<10,} {r['max_gas']:<10,} {r['total_eth']:<12.5f}"
            )
        return 0

    elif args.command == "estimate":
        client = EthereumClient(args.rpc)
        estimator = ActionCostEstimator(db)
        stats = estimator.get_action_stats(args.game, args.action)
        if not stats:
            print(f"No historical data for {args.game}:{args.action}. Record some transactions first.", file=sys.stderr)
            return 1

        block = client.get_latest_block()
        base_fee = int(block.get("baseFeePerGas", "0x0"), 16) / 1e9
        gas_price = base_fee + 1.0  # assume standard tip
        avg_gas = stats["avg_gas"]
        cost_eth = (avg_gas * gas_price * 1e9) / 1e18

        print(f"Estimate for {args.game} -> {args.action}:")
        print(f"  Avg Gas Used:   {avg_gas:,.0f} (sample size: {stats['count']})")
        print(f"  Current Base:   {base_fee:.2f} gwei (est total: {gas_price:.2f} gwei)")
        print(f"  Estimated Cost: {cost_eth:.6f} ETH")
        if args.eth_price > 0:
            print(f"  Fiat Value:     ${cost_eth * args.eth_price:.2f} USD (@ ${args.eth_price:.2f}/ETH)")
        return 0

    elif args.command == "watch":
        client = EthereumClient(args.rpc)
        run_watch(client, db, args)
        return 0

    return 0
