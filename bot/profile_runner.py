from __future__ import annotations

import os
import sys

from bot.profile import load_profile


def _usage() -> int:
    print(
        "Usage: python -m bot.profile_runner <paper|live> "
        "<trade|monitor|daily|research|optimize|validate|connectivity> [spy|options]"
    )
    return 2


def _is_options(market: str | None) -> bool:
    return market == "options" or os.getenv("IS_OPTIONS", "").strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) not in {2, 3}:
        return _usage()

    profile, action = args[:2]
    market = args[2] if len(args) == 3 else None
    load_profile(profile, market)
    options_market = _is_options(market)

    if action == "trade":
        if options_market:
            from bot.options_engine import main as trade_main
        else:
            from bot.main import main as trade_main

        trade_main()
        return 0

    if action == "monitor":
        # Options positions/orders/events land in the same SQLite tables
        # (runs, orders, closed_trades, events, position_state) via
        # bot/store.py, so the generic monitor report works unmodified.
        from bot.report_monitor import main as monitor_main

        monitor_main()
        return 0

    if action == "daily":
        # Real (non-synthetic) daily Markdown report built from this
        # profile's runtime database — see bot/report_daily.py. Distinct
        # from `validate`, which also calls report_daily but only against
        # synthetic sample data.
        from bot.report_daily import main as daily_main

        daily_main()
        return 0

    if action == "research":
        if options_market:
            from bot.options_research import main as research_main
        else:
            from bot.research import main as research_main

        research_main()
        return 0

    if action == "optimize":
        if options_market:
            print("optimize is not implemented for the options market yet; use research to generate a report.", file=sys.stderr)
            return 2
        from bot.optimize_strategy import main as optimize_main

        optimize_main()
        return 0

    if action == "validate":
        from bot.validate_runtime import main as validate_main

        return int(validate_main())

    if action == "connectivity":
        if options_market:
            from bot.options_broker_alpaca import validate_options_connectivity

            symbols = [s.strip().upper() for s in os.getenv("OPTION_SYMBOLS", "NVDA,TSLA").split(",") if s.strip()]
            try:
                result = validate_options_connectivity(symbols)
            except RuntimeError as exc:
                print(f"connectivity failed: {exc}", file=sys.stderr)
                return 1
            mode = "paper" if result.paper else "live"
            print(
                f"connectivity ok: mode={mode} symbols={result.symbols} equity={result.equity:.2f} "
                f"chain_counts={result.chain_counts}"
            )
            return 0

        from bot.validate_connectivity import main as connectivity_main

        return int(connectivity_main())

    return _usage()


if __name__ == "__main__":
    raise SystemExit(main())
