"""The node's YAML configuration (config/node_sp100.yaml reproduces the forward test) and its CLI."""
from __future__ import annotations

import importlib
import json

import pytest

from _node_testkit import tiny_config_dict
from midfreq.core.paths import repo_root
from midfreq.core.risk_manager import EqualDollarSizer
from midfreq.trading import cli
from midfreq.trading.config.settings import (
    ConfigError, config_from_dict, instrument_for_symbol, load_config, strategy_params, validate,
)
from midfreq.trading.node.kernel import DEFAULT_COMPONENTS
from midfreq.trading.strategy.host import build_risk_manager

REPO = repo_root()
SP100 = REPO / "config" / "node_sp100.yaml"


def test_node_sp100_reproduces_the_forward_test():
    ft = json.loads((REPO / "config" / "sp100_forward_test.json").read_text())
    cfg = load_config(SP100)
    assert cfg.mode == "shadow"
    (s,) = cfg.strategies
    assert s.cls == "Cross-Sectional Reversal" and s.kind == "legacy"
    assert strategy_params(s) == ft["params"]
    assert cfg.universes[s.universe].symbols == ft["symbols"] and len(ft["symbols"]) == 98
    assert s.capital == ft["capital"] == 100_000.0
    assert s.commission_rate == 0.00005 and cfg.node.timeframe == "1h"
    assert (s.pricing.rule, s.pricing.slip) == ("last", 0.01)
    assert cfg.accounts[s.account].options == {"market": "US"} and cfg.accounts[s.account].environment == "paper"
    rm = build_risk_manager(s.risk_manager, strategy_params(s))
    assert rm.stop_loss_pct is None and rm.take_profit_pct is None and rm.max_drawdown_pct == 0.15
    assert isinstance(rm.position_sizer, EqualDollarSizer) and rm.position_sizer.n_positions == ft["params"]["top_n"]
    assert cfg.node.reconcile_s == 300 and cfg.node.stall_factor == 1.25
    assert cfg.oms["unfilled_order"]["wait_s"] == 30


def test_symbols_resolve_to_instruments():
    assert str(instrument_for_symbol("US.AAPL")) == "AAPL.XNAS"
    assert str(instrument_for_symbol("US.JPM")) == "JPM.XNYS"
    assert str(instrument_for_symbol("HK.00700")) == "00700.XHKG"
    assert str(instrument_for_symbol("BRK.B.XNYS")) == "BRK.B.XNYS"


@pytest.mark.parametrize("change,message", [
    (lambda d: d["node"].update(mode="yolo"), "node.mode"),
    (lambda d: d["strategies"][0]["params"].update(nonsense=1), "unknown params"),
    (lambda d: d["strategies"][0].update(account="nobody"), "account 'nobody'"),
    (lambda d: d["strategies"][0].update(universe="none"), "universe 'none'"),
    (lambda d: d["strategies"][0].update({"class": "No Such Strategy"}), "No Such Strategy"),
    (lambda d: (d["strategies"][0].update(state_name="crosssectionalreversal"),
                d["strategies"].append(dict(d["strategies"][0], id="second"))), "state_crosssectionalreversal"),
    (lambda d: (d["node"].update(mode="live")), "environment: live"),
    (lambda d: d["accounts"]["acct"].update(environment="live"), "live account but the mode is paper"),
    (lambda d: d["universes"]["u"]["symbols"].append("HK.00700"), "one cash balance"),
    (lambda d: d["strategies"][0].update(pricing={"rule": "guess"}), "pricing.rule"),
])
def test_validation_finds_the_problem(change, message):
    d = tiny_config_dict(mode="paper")
    change(d)
    problems = validate(config_from_dict(d))
    assert any(message in p for p in problems), problems


def test_structural_errors_raise():
    with pytest.raises(ConfigError, match="unknown sections"):
        config_from_dict({"nodes": {}})
    with pytest.raises(ConfigError, match="unknown settings"):
        config_from_dict({"node": {"speed": "fast"}})
    d = tiny_config_dict()
    d["strategies"][0]["risk_manager"]["stopp"] = 1
    with pytest.raises(ConfigError, match="risk_manager"):
        config_from_dict(d)


def test_load_config_raises_every_problem_at_once(tmp_path):
    import yaml
    d = tiny_config_dict(mode="paper")
    d["strategies"][0]["account"] = "nobody"
    d["node"]["heartbeat_s"] = 0
    path = tmp_path / "node.yaml"
    path.write_text(yaml.safe_dump(d))
    with pytest.raises(ConfigError) as e:
        load_config(path)
    assert "nobody" in str(e.value) and "heartbeat_s" in str(e.value)
    assert load_config(path, check=False).strategies[0].account == "nobody"


def _importable(path):
    module, _, attr = path.partition(":")
    try:
        getattr(importlib.import_module(module), attr)
        return True
    except (ImportError, AttributeError):
        return False


def test_cli_check_config(capsys):
    code = cli.main(["check-config", "--config", str(SP100)])
    out = capsys.readouterr().out
    assert "valid" in out and "sp100-xsr: CrossSectionalReversal (legacy) on sp100-xsr (98 symbols)" in out
    needed = [p for n, p in DEFAULT_COMPONENTS.items() if not n.startswith("sim_")]
    assert code == (0 if all(_importable(p) for p in needed) else 1)


def test_cli_rejects_an_invalid_configuration(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("node: {mode: yolo}\nstrategies: []\n")
    assert cli.main(["check-config", "--config", str(bad)]) == 2
    assert cli.main(["run", "--config", str(bad)]) == 2
    assert cli.main(["check-config", "--config", str(tmp_path / "missing.yaml")]) == 2


def test_cli_parses_run_and_replay(monkeypatch):
    ns = cli.build_parser().parse_args(["run", "--config", "x.yaml", "--mode", "paper", "--duration", "5"])
    assert (ns.mode, ns.duration) == ("paper", 5.0)
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "--config", "x.yaml", "--mode", "yolo"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(["replay", "--help"])
    assert e.value.code == 0


def test_cli_run_starts_a_node_with_logging_and_signals(monkeypatch, tmp_path):
    seen = {}

    class FakeNode:
        def __init__(self, cfg, **kw):
            seen["cfg"], seen["kw"] = cfg, kw

        def run(self, duration_min=None):
            seen["duration"] = duration_min
            return 0
    import midfreq.trading.node.kernel as node_mod
    monkeypatch.setattr(node_mod, "TradingNode", FakeNode)
    assert cli.main(["run", "--config", str(SP100), "--mode", "sim", "--duration", "3"]) == 0
    assert seen["cfg"].mode == "sim" and seen["duration"] == 3.0
    assert seen["kw"] == {"configure_logging": True, "install_signal_handlers": True}


def test_launcher_dry_run(tmp_path):
    """ops/run_node.sh resolves its configuration and prints the command (no session starts),
    run against a copy of the configuration so nothing is written in the repository."""
    import os
    import shutil
    import subprocess
    import sys
    (tmp_path / "config").mkdir()
    for name in ("node_sp100.yaml", "sp100_forward_test.json", "venues.json", "connections.yaml"):
        shutil.copy2(REPO / "config" / name, tmp_path / "config" / name)
    env = dict(os.environ, DRY_RUN="1", PYTHON=sys.executable, FAKE_ET="2026-10-14T09:35",
               MIDFREQ_ROOT=str(tmp_path))
    r = subprocess.run(["bash", str(REPO / "ops" / "run_node.sh")], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=120)
    assert "duration : 387 min" in r.stdout, r.stdout + r.stderr
    assert "mode     : shadow" in r.stdout
    assert "would run:" in r.stdout or "configuration check failed" in r.stdout
    gate = subprocess.run(["bash", str(REPO / "ops" / "run_node.sh"), "--scheduled"], cwd=str(tmp_path),
                          env=dict(env, FAKE_ET="2026-10-14T12:00"), capture_output=True, text=True, timeout=120)
    assert gate.returncode == 0 and "not the open, skipping" in gate.stdout
    assert not (tmp_path / "live_sessions").exists()


def test_paper_and_live_need_an_explicit_state_name():
    """H3: in paper and live a candle strategy must name its state file; in shadow and sim the
    class name is the default (the original engine's file, read only there)."""
    from midfreq.trading.config.settings import resolve_strategy_class, state_slug
    for mode in ("paper", "live"):
        d = tiny_config_dict(mode=mode)
        if mode == "live":
            d["accounts"]["acct"]["environment"] = "live"
        assert any("explicit `state_name`" in p for p in validate(config_from_dict(d)))
        d["strategies"][0]["state_name"] = "xsr-test"
        assert not any("state_name" in p for p in validate(config_from_dict(d)))
    cfg = config_from_dict(tiny_config_dict(mode="shadow"))
    assert validate(cfg) == []
    cls = resolve_strategy_class(cfg.strategies[0].cls)[0]
    assert state_slug(cfg.strategies[0], cls, "shadow") == "crosssectionalreversal"
