"""
The trading node's command line, `midfreq-node` (or `python -m midfreq.trading.cli`).

    midfreq-node run --config config/node_sp100.yaml [--mode shadow|sim|paper|live] [--duration MIN]
    midfreq-node replay --config config/node_sp100.yaml [--data-dir DIR] [--repeat 3] ...
    midfreq-node check-config --config config/node_sp100.yaml

`run` starts the node and blocks until the duration ends, SIGTERM/SIGINT arrives, or the stream
stalls; the exit code is 0 after a clean stop. `--mode live` is refused unless the configuration's
account also says `environment: live` (the node checks the execution client agrees).
`check-config` reads and checks the configuration and says which components can be imported,
without connecting to anything. Exit codes: 0 ok, 1 failed, 2 invalid configuration.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from typing import List, Optional, Sequence

from midfreq.trading.config.settings import ConfigError, MODES, load_config, resolve_strategy_class, strategy_params


def _cmd_run(args: argparse.Namespace) -> int:
    from midfreq.trading.node.kernel import TradingNode
    try:
        cfg = load_config(args.config, mode=args.mode)
    except ConfigError as e:
        print(f"invalid configuration: {e}", file=sys.stderr)
        return 2
    if args.log_level:
        cfg.node.log_level = args.log_level
    node = TradingNode(cfg, configure_logging=True, install_signal_handlers=True)
    try:
        return node.run(duration_min=args.duration)
    except Exception as e:
        print(f"the node failed: {e}", file=sys.stderr)
        return 1


def _cmd_check(args: argparse.Namespace) -> int:
    from midfreq.trading.node.kernel import DEFAULT_COMPONENTS
    try:
        cfg = load_config(args.config, mode=args.mode)
    except ConfigError as e:
        print(f"INVALID: {e}")
        return 2
    print(f"configuration {cfg.source_path}: valid")
    print(f"  node      {cfg.node.name}, mode {cfg.mode}, timeframe {cfg.node.timeframe}")
    for aid, acc in cfg.accounts.items():
        print(f"  account   {aid}: connector {acc.connector}, environment {acc.environment}, options {acc.options}")
    for s in cfg.strategies:
        cls, _ = resolve_strategy_class(s.cls)
        params = strategy_params(s) if s.kind == "legacy" else s.params
        n = len(cfg.universes[s.universe].symbols)
        cap = f"{s.capital:,.0f}" if s.capital is not None else "the whole account"
        print(f"  strategy  {s.id}: {cls.__name__} ({s.kind}) on {s.universe} ({n} symbols), account {s.account}, "
              f"capital {cap}, params {params}, pricing {s.pricing.rule} slip {s.pricing.slip}")
    missing = 0
    for name, path in {**DEFAULT_COMPONENTS, **cfg.components}.items():
        if cfg.mode != "sim" and name.startswith("sim_"):
            continue
        module, _, attr = path.partition(":")
        try:
            getattr(importlib.import_module(module), attr)
            status = "ok"
        except (ImportError, AttributeError) as e:
            status = f"NOT AVAILABLE ({e})"
            missing += 1
        print(f"  component {name:<13} {path}: {status}")
    if missing:
        print(f"{missing} component(s) cannot be imported: the node cannot start until they exist")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="midfreq-node", description="The live trading node (docs/LIVE_ARCHITECTURE.md).")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the node")
    run.add_argument("--config", required=True)
    run.add_argument("--mode", choices=MODES, default=None, help="overrides node.mode")
    run.add_argument("--duration", type=float, default=None, help="minutes to run (default: node.duration_min, or until stopped)")
    run.add_argument("--log-level", default=None)
    run.set_defaults(func=_cmd_run)
    check = sub.add_parser("check-config", help="read and check a configuration; connects to nothing")
    check.add_argument("--config", required=True)
    check.add_argument("--mode", choices=MODES, default=None)
    check.set_defaults(func=_cmd_check)
    sub.add_parser("replay", help="replay recorded sessions in sim mode (see `midfreq-node replay -h`)",
                   add_help=False)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "replay":
        from midfreq.trading.modes.replay import main as replay_main
        return replay_main(argv[1:])
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
