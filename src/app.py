import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path
import io, contextlib

# Add project root to path so we can import modules when running via Streamlit
import sys
sys.path.append(str(Path(__file__).parent))

from core.config import ConfigLoader
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.strategy import STRATEGY_REGISTRY
from core.backtester import Backtester
from core.models import Timeframe
from core.risk_manager import RiskManager, SizingMethod, create_position_sizer
from core.optimizer import grid_search, walk_forward, generate_param_grid, OBJECTIVES

# Set page config
st.set_page_config(page_title="Quantitative Trading Dashboard", layout="wide", page_icon="📈")

st.title("📈 Mid-Frequency Trading Dashboard")

# Initialize core services
@st.cache_resource
def get_services():
    config = ConfigLoader()
    storage = DataStorage()
    return config, storage

config, storage = get_services()

# --- SIDEBAR (Control Panel) ---
st.sidebar.header("🕹️ Strategy Control Panel")

# 1. Market & Symbol Selection
market = st.sidebar.selectbox("Market", ["HK"])
available_symbols = config.get_live_symbols(market=market)
symbol = st.sidebar.selectbox("Symbol", available_symbols if available_symbols else ["HK.00700"])

# 2. Strategy Selection (dynamic from registry)
strategy_names = list(STRATEGY_REGISTRY.keys())
default_strategy_idx = strategy_names.index("Z-Score Mean Reversion") if "Z-Score Mean Reversion" in strategy_names else 0
selected_strategy = st.sidebar.selectbox("Strategy", strategy_names, index=default_strategy_idx)
strategy_info = STRATEGY_REGISTRY[selected_strategy]

st.sidebar.subheader(f"{selected_strategy} Parameters")

# 3. Dynamic parameter controls based on selected strategy
strategy_params = {}
for param_key, param_meta in strategy_info["params"].items():
    if isinstance(param_meta["default"], float):
        strategy_params[param_key] = st.sidebar.number_input(
            param_meta["label"],
            min_value=float(param_meta["min"]),
            max_value=float(param_meta["max"]),
            value=float(param_meta["default"]),
            step=float(param_meta["step"])
        )
    else:
        strategy_params[param_key] = st.sidebar.number_input(
            param_meta["label"],
            min_value=int(param_meta["min"]),
            max_value=int(param_meta["max"]),
            value=int(param_meta["default"]),
            step=int(param_meta["step"])
        )

# Pairs Trading: second symbol selector
pair_symbol = None
if selected_strategy == "Pairs Trading":
    st.sidebar.markdown("---")
    st.sidebar.subheader("Pair Stock")
    other_symbols = [s for s in available_symbols if s != symbol]
    pair_symbol = st.sidebar.selectbox("Second Symbol (Pair)", other_symbols if other_symbols else ["HK.00005"])

# 4. Timeframe
timeframe_opts = {
    "1 Minute": Timeframe.MIN_1,
    "5 Minute": Timeframe.MIN_5,
    "1 Hour": Timeframe.HOUR_1,
    "1 Day": Timeframe.DAY_1
}
selected_tf_label = st.sidebar.selectbox("Timeframe", list(timeframe_opts.keys()), index=3)
timeframe = timeframe_opts[selected_tf_label]

st.sidebar.markdown("---")
st.sidebar.subheader("Backtest Settings")

# Date range selection
from datetime import datetime, timedelta
default_end = datetime.now().date()
default_start = default_end - timedelta(days=365) # 1 year default

col1, col2 = st.sidebar.columns(2)
start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)

# Initial Capital
initial_capital = st.sidebar.number_input("Starting Capital (HKD)", min_value=1000.0, value=100000.0, step=10000.0)

st.sidebar.markdown("---")

# ─── Risk Management Controls ────────────────────────────────────
st.sidebar.subheader("🛡️ Risk Management")

with st.sidebar.expander("Stop-Loss & Take-Profit", expanded=False):
    enable_stop_loss = st.checkbox("Enable Fixed Stop-Loss", value=False)
    stop_loss_pct = st.slider("Stop-Loss %", min_value=1.0, max_value=15.0, value=3.0, step=0.5,
                              disabled=not enable_stop_loss) / 100.0 if enable_stop_loss else None

    enable_trailing = st.checkbox("Enable Trailing Stop", value=False)
    trailing_stop_pct = st.slider("Trailing Stop %", min_value=1.0, max_value=15.0, value=5.0, step=0.5,
                                  disabled=not enable_trailing) / 100.0 if enable_trailing else None

    enable_take_profit = st.checkbox("Enable Take-Profit", value=False)
    take_profit_pct = st.slider("Take-Profit %", min_value=1.0, max_value=30.0, value=5.0, step=0.5,
                                disabled=not enable_take_profit) / 100.0 if enable_take_profit else None

with st.sidebar.expander("Position Sizing", expanded=False):
    sizing_options = {
        "Fixed Quantity (100 shares)": SizingMethod.FIXED_QUANTITY,
        "Fixed Fractional (% of equity)": SizingMethod.FIXED_FRACTIONAL,
        "Kelly Criterion (optimal)": SizingMethod.KELLY,
    }
    selected_sizing = st.selectbox("Sizing Method", list(sizing_options.keys()))
    sizing_method = sizing_options[selected_sizing]

    sizing_kwargs = {}
    if sizing_method == SizingMethod.FIXED_QUANTITY:
        sizing_kwargs['qty'] = st.number_input("Shares per Trade", min_value=1, value=100, step=10)
    elif sizing_method == SizingMethod.FIXED_FRACTIONAL:
        sizing_kwargs['risk_pct'] = st.slider("Risk % per Trade", min_value=0.5, max_value=10.0, value=2.0, step=0.5) / 100.0
    elif sizing_method == SizingMethod.KELLY:
        sizing_kwargs['fraction'] = st.slider("Kelly Fraction", min_value=0.1, max_value=1.0, value=0.5, step=0.1)
        sizing_kwargs['max_pct'] = st.slider("Max Position %", min_value=5.0, max_value=50.0, value=25.0, step=5.0) / 100.0

with st.sidebar.expander("Circuit Breaker", expanded=False):
    max_drawdown_pct = st.slider("Max Drawdown Halt %", min_value=5.0, max_value=50.0, value=10.0, step=1.0) / 100.0


def build_risk_manager():
    """Build a RiskManager from the current sidebar settings."""
    sizer = create_position_sizer(sizing_method, **sizing_kwargs)
    return RiskManager(
        stop_loss_pct=stop_loss_pct if enable_stop_loss else None,
        trailing_stop_pct=trailing_stop_pct if enable_trailing else None,
        take_profit_pct=take_profit_pct if enable_take_profit else None,
        max_drawdown_pct=max_drawdown_pct,
        position_sizer=sizer,
    )

# Slippage setting
st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Execution Model")
slippage_bps = st.sidebar.slider("Slippage (basis points)", min_value=0.0, max_value=50.0, value=0.0, step=1.0,
                                  help="Simulated slippage: buys pay more, sells receive less. 1 bps = 0.01%.")

st.sidebar.markdown("---")

# ─── Strategy Optimization Controls ──────────────────────────────
st.sidebar.subheader("🔬 Strategy Optimization")

with st.sidebar.expander("Grid Search / Walk-Forward", expanded=True):
    opt_mode = st.radio("Mode", ["Grid Search", "Walk-Forward"], horizontal=True, index=1)

    objective_labels = {v: k for k, v in OBJECTIVES.items()}
    selected_objective_label = st.selectbox("Objective", list(OBJECTIVES.values()))
    objective = objective_labels[selected_objective_label]

    grid_size = len(generate_param_grid(selected_strategy))
    st.caption(f"Full parameter grid for **{selected_strategy}**: {grid_size} combinations")

    if opt_mode == "Grid Search":
        max_combinations = st.number_input("Max Combinations", min_value=5, max_value=2000, value=min(200, max(grid_size, 5)), step=5)
    else:
        n_splits = st.number_input("Rolling Windows", min_value=2, max_value=10, value=3, step=1)
        train_pct = st.slider("Train Fraction", min_value=0.5, max_value=0.9, value=0.6, step=0.05)

st.sidebar.markdown("---")

# 5. Execution Actions
run_sim = st.sidebar.button("🚀 Run Backtest", type="primary", use_container_width=True)
run_compare = st.sidebar.button("⚡ Compare All Strategies", use_container_width=True)
run_optimize = st.sidebar.button("🔬 Run Optimization", use_container_width=True)

st.sidebar.markdown("---")
st.sidebar.subheader("📡 Live Paper Trading")
run_live_view = st.sidebar.button("Show Live Account & Sessions", use_container_width=True)


# ─── Chart Plotting Functions ───────────────────────────────────────

def plot_sma_crossover(df, params):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='Price'))
    fast_series = df['close'].rolling(window=params['fast_period']).mean()
    slow_series = df['close'].rolling(window=params['slow_period']).mean()
    fig.add_trace(go.Scatter(x=df.index, y=fast_series, line=dict(color='orange', width=2), name=f"Fast MA ({params['fast_period']})"))
    fig.add_trace(go.Scatter(x=df.index, y=slow_series, line=dict(color='royalblue', width=2), name=f"Slow MA ({params['slow_period']})"))
    fig.update_layout(title=f'{symbol} — SMA Crossover', yaxis_title='Price', xaxis_rangeslider_visible=False, height=500, margin=dict(l=0, r=0, t=40, b=0))
    return fig


def plot_rsi(df, params):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3],
                        subplot_titles=[f'{symbol} — Price', 'RSI'])
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='Price'), row=1, col=1)
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.rolling(window=params['rsi_period']).mean()
    avg_loss = loss.rolling(window=params['rsi_period']).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    fig.add_trace(go.Scatter(x=df.index, y=rsi, line=dict(color='mediumpurple', width=2), name='RSI'), row=2, col=1)
    fig.add_hline(y=params['overbought'], line_dash="dash", line_color="red", annotation_text="Overbought", row=2, col=1)
    fig.add_hline(y=params['oversold'], line_dash="dash", line_color="green", annotation_text="Oversold", row=2, col=1)
    fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=40, b=0))
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    return fig


def plot_macd(df, params):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3],
                        subplot_titles=[f'{symbol} — Price', 'MACD'])
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='Price'), row=1, col=1)
    fast_ema = df['close'].ewm(span=params['fast_ema'], adjust=False).mean()
    slow_ema = df['close'].ewm(span=params['slow_ema'], adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=params['signal_period'], adjust=False).mean()
    histogram = macd_line - signal_line
    colors = ['green' if v >= 0 else 'red' for v in histogram]
    fig.add_trace(go.Bar(x=df.index, y=histogram, marker_color=colors, name='Histogram', opacity=0.5), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=macd_line, line=dict(color='dodgerblue', width=2), name='MACD'), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=signal_line, line=dict(color='orange', width=2), name='Signal'), row=2, col=1)
    fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=40, b=0))
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="MACD", row=2, col=1)
    return fig


def plot_bollinger(df, params):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='Price'))
    middle = df['close'].rolling(window=params['bb_period']).mean()
    std = df['close'].rolling(window=params['bb_period']).std()
    upper = middle + params['num_std'] * std
    lower = middle - params['num_std'] * std
    fig.add_trace(go.Scatter(x=df.index, y=upper, line=dict(color='rgba(255,107,107,0.6)', width=1, dash='dash'), name='Upper Band'))
    fig.add_trace(go.Scatter(x=df.index, y=lower, line=dict(color='rgba(78,203,113,0.6)', width=1, dash='dash'), name='Lower Band', fill='tonexty', fillcolor='rgba(173,216,230,0.1)'))
    fig.add_trace(go.Scatter(x=df.index, y=middle, line=dict(color='royalblue', width=1.5), name=f"SMA ({params['bb_period']})"))
    fig.update_layout(title=f'{symbol} — Bollinger Bands', yaxis_title='Price', xaxis_rangeslider_visible=False, height=500, margin=dict(l=0, r=0, t=40, b=0))
    return fig


def plot_zscore(df, params):
    """Candlestick + Z-Score subplot with entry/exit bands"""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3],
                        subplot_titles=[f'{symbol} — Price', 'Z-Score'])
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='Price'), row=1, col=1)

    # Compute rolling Z-score
    rolling_mean = df['close'].rolling(window=params['lookback']).mean()
    rolling_std = df['close'].rolling(window=params['lookback']).std()
    z_score = (df['close'] - rolling_mean) / rolling_std

    # Add mean line on price chart
    fig.add_trace(go.Scatter(x=df.index, y=rolling_mean, line=dict(color='royalblue', width=1.5, dash='dash'), name=f"Rolling Mean ({params['lookback']})"), row=1, col=1)

    # Z-Score plot
    fig.add_trace(go.Scatter(x=df.index, y=z_score, line=dict(color='mediumpurple', width=2), name='Z-Score'), row=2, col=1)
    fig.add_hline(y=params['entry_z'], line_dash="dash", line_color="red", annotation_text=f"+{params['entry_z']}σ", row=2, col=1)
    fig.add_hline(y=-params['entry_z'], line_dash="dash", line_color="green", annotation_text=f"-{params['entry_z']}σ", row=2, col=1)
    fig.add_hline(y=0, line_dash="dot", line_color="gray", row=2, col=1)

    fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=40, b=0))
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Z-Score", row=2, col=1)
    return fig


def plot_pairs(df, params):
    """Simple price chart for the primary symbol in pairs trading"""
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name=f'{symbol} Price'))
    fig.update_layout(title=f'{symbol} — Pairs Trading (Primary)', yaxis_title='Price', xaxis_rangeslider_visible=False, height=500, margin=dict(l=0, r=0, t=40, b=0))
    return fig


def plot_ensemble(df, params):
    """Simple price chart for Ensemble Strategy"""
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name=f'{symbol} Price'))
    fig.update_layout(title=f'{symbol} — Ensemble Voting Strategy', yaxis_title='Price', xaxis_rangeslider_visible=False, height=500, margin=dict(l=0, r=0, t=40, b=0))
    return fig


def plot_regime(df, params):
    """Candlestick + Efficiency Ratio subplot with the trend/range threshold"""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3],
                        subplot_titles=[f'{symbol} — Price', 'Efficiency Ratio (trend vs range)'])
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name='Price'), row=1, col=1)

    lookback = params['regime_lookback']
    net_move = (df['close'] - df['close'].shift(lookback)).abs()
    path = df['close'].diff().abs().rolling(lookback).sum()
    er = (net_move / path).clip(0, 1)

    fig.add_trace(go.Scatter(x=df.index, y=er, line=dict(color='mediumpurple', width=2), name='Efficiency Ratio'), row=2, col=1)
    fig.add_hline(y=params['er_threshold'], line_dash="dash", line_color="orange",
                  annotation_text=f"threshold {params['er_threshold']} (above = trend leg)", row=2, col=1)
    fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=40, b=0))
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="ER", range=[0, 1], row=2, col=1)
    return fig


# Map strategy name → plot function
PLOT_FUNCTIONS = {
    "SMA Crossover": plot_sma_crossover,
    "RSI": plot_rsi,
    "MACD": plot_macd,
    "Bollinger Bands": plot_bollinger,
    "Z-Score Mean Reversion": plot_zscore,
    "Pairs Trading": plot_pairs,
    "Ensemble Voting": plot_ensemble,
    "Regime Switch": plot_regime,
    "HMM Regime Switch": plot_ensemble,  # plain price chart; states live in the model
}


def plot_equity_curve(equity_data):
    """Plot the equity curve from portfolio snapshots"""
    eq_df = pd.DataFrame(equity_data)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=eq_df['timestamp'], y=eq_df['equity'],
        mode='lines+markers', line=dict(color='dodgerblue', width=2),
        marker=dict(size=4), name='Equity'
    ))
    fig.add_hline(y=initial_capital, line_dash="dash", line_color="gray", annotation_text="Initial Capital")
    fig.update_layout(title='Equity Curve', yaxis_title='Portfolio Value (HKD)', height=350, margin=dict(l=0, r=0, t=40, b=0))
    return fig


# ─── Main Execution ────────────────────────────────────────────────

if run_sim:
    with st.spinner(f'Running {selected_strategy} on {symbol}...'):
        portfolio = Portfolio(initial_cash=initial_capital)
        risk_mgr = build_risk_manager()
        backtester = Backtester(storage=storage, portfolio=portfolio, risk_manager=risk_mgr,
                                slippage_bps=slippage_bps)

        # Determine symbols list (pairs trading needs 2)
        sim_symbols = [symbol, pair_symbol] if selected_strategy == "Pairs Trading" and pair_symbol else [symbol]

        # Suppress backtester console output
        f_buf = io.StringIO()
        with contextlib.redirect_stdout(f_buf):
            metrics = backtester.run(
                strategy_info["class"],
                symbols=sim_symbols,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                **strategy_params
            )

        st.success(f"✅ {selected_strategy} Simulation Complete!")

        # --- TOP ROW: KPIs ---
        if metrics:
            col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)

            equity = metrics['final_equity']
            ret_pct = metrics['return_pct']
            delta_color = "normal" if ret_pct >= 0 else "inverse"

            with col1:
                st.metric("Final Equity", f"HKD {equity:,.2f}", f"{ret_pct:+.2f}%", delta_color=delta_color)
            with col2:
                st.metric("Total Trades", f"{metrics['total_trades']}")
            with col3:
                st.metric("Win Rate", f"{metrics['win_rate']:.1f}%")
            with col4:
                sr = metrics['sharpe_ratio']
                st.metric("Sharpe Ratio", f"{sr:.2f}")
            with col5:
                st.metric("Max Drawdown", f"{metrics['max_drawdown']:.2f}%")
            with col6:
                pf = metrics['profit_factor']
                pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
                st.metric("Profit Factor", pf_str)
            with col7:
                bench = metrics.get('benchmark_return_pct', 0.0)
                st.metric("Benchmark", f"{bench:+.2f}%")
            with col8:
                alpha = metrics.get('alpha', 0.0)
                alpha_color = "normal" if alpha >= 0 else "inverse"
                st.metric("Alpha", f"{alpha:+.2f}%", delta_color=alpha_color)

        # --- CHART ---
        folder = symbol.replace('.', '_')
        raw_df = storage.load_data(folder, timeframe.value)

        if not raw_df.empty:
            st.markdown(f"### 📊 {selected_strategy} — Market View & Indicator Overlay")
            plot_fn = PLOT_FUNCTIONS[selected_strategy]
            fig = plot_fn(raw_df, strategy_params)
            st.plotly_chart(fig, use_container_width=True)

            # --- EQUITY CURVE (use detailed per-candle data) ---
            equity_data = portfolio.equity_curve_detailed if portfolio.equity_curve_detailed else portfolio.equity_curve
            if equity_data:
                st.markdown("### 📈 Equity Curve vs. Buy-and-Hold Benchmark")
                eq_fig = plot_equity_curve(equity_data)
                
                # Add benchmark line if available
                bench_ret = metrics.get('benchmark_return_pct', 0.0)
                if equity_data and bench_ret != 0.0:
                    eq_df_tmp = pd.DataFrame(equity_data)
                    # Linear interpolation of benchmark
                    bench_start = initial_capital
                    bench_end = initial_capital * (1 + bench_ret / 100)
                    n = len(eq_df_tmp)
                    bench_values = [bench_start + (bench_end - bench_start) * i / (n - 1) for i in range(n)]
                    eq_fig.add_trace(go.Scatter(
                        x=eq_df_tmp['timestamp'], y=bench_values,
                        mode='lines', line=dict(color='rgba(255,165,0,0.7)', width=2, dash='dot'),
                        name=f'Buy & Hold ({bench_ret:+.2f}%)'
                    ))
                
                st.plotly_chart(eq_fig, use_container_width=True)

            # --- TRADE LOG ---
            st.markdown("### 📋 Execution Trade Log")
            trade_df = pd.DataFrame(portfolio.trade_history)

            if not trade_df.empty:
                trade_df['timestamp'] = trade_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
                trade_df['price'] = trade_df['price'].apply(lambda x: f"HKD {x:.2f}")
                trade_df['commission'] = trade_df['commission'].apply(lambda x: f"HKD {x:.2f}")
                trade_df['cash_after'] = trade_df['cash_after'].apply(lambda x: f"HKD {x:.2f}")
                
                # Add exit reason column if present
                if 'exit_reason' not in trade_df.columns:
                    trade_df['exit_reason'] = ''

                def highlight_action(val):
                    color = '#4CAF50' if val.upper() == 'BUY' else '#F44336'
                    return f'color: white; background-color: {color}; padding: 4px; border-radius: 4px; text-align: center; font-weight: bold;'

                def highlight_exit(val):
                    if val == 'stop_loss':
                        return 'color: white; background-color: #F44336; padding: 2px 4px; border-radius: 4px; font-weight: bold;'
                    elif val == 'trailing_stop':
                        return 'color: white; background-color: #FF9800; padding: 2px 4px; border-radius: 4px; font-weight: bold;'
                    elif val == 'take_profit':
                        return 'color: white; background-color: #4CAF50; padding: 2px 4px; border-radius: 4px; font-weight: bold;'
                    return ''

                st.dataframe(
                    trade_df.style
                        .map(highlight_action, subset=['action'])
                        .map(highlight_exit, subset=['exit_reason']),
                    use_container_width=True,
                    height=300
                )
            else:
                st.info("No trades were executed by the strategy during this period.")
        else:
            st.warning(f"No historical Parquet data found for {symbol} on {selected_tf_label}. Please run data collector first.")

elif run_compare:
    # ─── Strategy Comparison Mode ──────────────────────────────────
    st.markdown("### ⚡ Strategy Comparison")
    st.markdown(f"Running all strategies on **{symbol}** ({selected_tf_label})...")

    # Exclude Pairs Trading from single-stock comparison
    compare_strategies = {k: v for k, v in STRATEGY_REGISTRY.items() if k != "Pairs Trading"}

    results = []
    equity_curves = {}

    progress = st.progress(0)
    for i, (strat_name, info) in enumerate(compare_strategies.items()):
        portfolio = Portfolio(initial_cash=initial_capital)
        risk_mgr = build_risk_manager()
        bt = Backtester(storage=storage, portfolio=portfolio, risk_manager=risk_mgr,
                        slippage_bps=slippage_bps)
        defaults = {k: v['default'] for k, v in info['params'].items()}

        f_buf = io.StringIO()
        with contextlib.redirect_stdout(f_buf):
            metrics = bt.run(info['class'], symbols=[symbol], timeframe=timeframe, 
                             start_date=start_date, end_date=end_date, **defaults)

        if metrics:
            alpha = metrics.get('alpha', 0.0)
            results.append({
                'Strategy': strat_name,
                'Return %': f"{metrics['return_pct']:+.2f}%",
                'Alpha': f"{alpha:+.2f}%",
                'Trades': metrics['total_trades'],
                'Win Rate': f"{metrics['win_rate']:.1f}%",
                'Sharpe': f"{metrics['sharpe_ratio']:.2f}",
                'Max DD': f"{metrics['max_drawdown']:.2f}%",
                'Final Equity': f"HKD {metrics['final_equity']:,.2f}",
                '_return': metrics['return_pct'],  # for sorting
            })
            # Use detailed equity curve for comparison
            eq_data = portfolio.equity_curve_detailed if portfolio.equity_curve_detailed else portfolio.equity_curve
            if eq_data:
                equity_curves[strat_name] = eq_data

        progress.progress((i + 1) / len(compare_strategies))

    progress.empty()

    if results:
        # Sort by return descending
        results.sort(key=lambda x: x['_return'], reverse=True)

        # Display comparison table
        display_df = pd.DataFrame(results).drop(columns=['_return'])

        def color_return(val):
            if val.startswith('+'):
                return 'color: #4CAF50; font-weight: bold;'
            elif val.startswith('-'):
                return 'color: #F44336; font-weight: bold;'
            return ''

        st.dataframe(
            display_df.style.map(color_return, subset=['Return %']),
            use_container_width=True,
            height=250
        )

        # Overlay equity curves
        if equity_curves:
            st.markdown("### 📈 Equity Curves Comparison")
            fig = go.Figure()
            colors = ['dodgerblue', 'orange', 'mediumpurple', 'green', 'red', 'cyan']
            for i, (name, curve) in enumerate(equity_curves.items()):
                eq_df = pd.DataFrame(curve)
                fig.add_trace(go.Scatter(
                    x=eq_df['timestamp'], y=eq_df['equity'],
                    mode='lines', line=dict(width=2, color=colors[i % len(colors)]),
                    name=name
                ))
            fig.add_hline(y=initial_capital, line_dash="dash", line_color="gray", annotation_text="Initial Capital")
            fig.update_layout(yaxis_title='Portfolio Value (HKD)', height=400, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No results to compare. Ensure data exists for the selected symbol and timeframe.")

elif run_optimize:
    # ─── Strategy Optimization Mode ────────────────────────────────
    sim_symbols = [symbol, pair_symbol] if selected_strategy == "Pairs Trading" and pair_symbol else [symbol]
    risk_mgr = build_risk_manager()

    progress_bar = st.progress(0.0)
    status_text = st.empty()

    def _progress(pct, msg):
        progress_bar.progress(min(pct, 1.0))
        status_text.text(msg)

    if opt_mode == "Grid Search":
        st.markdown(f"### 🔬 Grid Search — {selected_strategy} on {symbol}")
        st.markdown(f"Optimizing for **{selected_objective_label}**")

        results_df = grid_search(
            strategy_name=selected_strategy,
            symbols=sim_symbols,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            storage=storage,
            objective=objective,
            initial_capital=initial_capital,
            risk_manager=risk_mgr,
            slippage_bps=slippage_bps,
            max_combinations=max_combinations,
            progress_callback=_progress,
        )

        progress_bar.empty()
        status_text.empty()

        if results_df.empty:
            st.warning("No results. Ensure data exists for the selected symbol and timeframe.")
        else:
            best = results_df.iloc[0]
            st.success(f"✅ Tested {len(results_df)} parameter combinations")

            param_cols = [c for c in results_df.columns if c not in
                          ('return_pct', 'sharpe_ratio', 'max_drawdown', 'win_rate',
                           'profit_factor', 'total_trades', 'alpha', '_objective')]

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Best Params", ", ".join(f"{k}={best[k]}" for k in param_cols))
            with col2:
                st.metric("Return", f"{best['return_pct']:+.2f}%")
            with col3:
                st.metric("Sharpe Ratio", f"{best['sharpe_ratio']:.2f}")
            with col4:
                st.metric("Max Drawdown", f"{best['max_drawdown']:.2f}%")

            st.markdown("### 📋 Top Results")
            display_cols = param_cols + ['return_pct', 'sharpe_ratio', 'max_drawdown', 'win_rate', 'profit_factor', 'total_trades', 'alpha']
            top_df = results_df[display_cols].head(50).copy()
            for c in ['return_pct', 'max_drawdown', 'win_rate', 'alpha']:
                top_df[c] = top_df[c].apply(lambda x: f"{x:+.2f}%")
            top_df['sharpe_ratio'] = top_df['sharpe_ratio'].apply(lambda x: f"{x:.2f}")
            top_df['profit_factor'] = top_df['profit_factor'].apply(lambda x: f"{x:.2f}" if x != float('inf') else "∞")
            st.dataframe(top_df, use_container_width=True, height=350)

            st.markdown(f"### 📊 {selected_objective_label} by Parameter Combination")
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=[", ".join(f"{k}={r[k]}" for k in param_cols) for _, r in results_df.head(30).iterrows()],
                y=results_df.head(30)['_objective'],
                marker_color='dodgerblue',
            ))
            fig.update_layout(height=400, margin=dict(l=0, r=0, t=20, b=100), xaxis_tickangle=-45,
                              yaxis_title=selected_objective_label)
            st.plotly_chart(fig, use_container_width=True)

    else:
        # ─── Walk-Forward ───────────────────────────────────────────
        st.markdown(f"### 🔬 Walk-Forward Optimization — {selected_strategy} on {symbol}")
        st.markdown(f"**{n_splits}** rolling windows, **{train_pct:.0%}** train / **{1-train_pct:.0%}** test, optimizing for **{selected_objective_label}**")

        wf_result = walk_forward(
            strategy_name=selected_strategy,
            symbols=sim_symbols,
            timeframe=timeframe,
            start_date=datetime.combine(start_date, datetime.min.time()),
            end_date=datetime.combine(end_date, datetime.min.time()),
            storage=storage,
            n_splits=n_splits,
            train_pct=train_pct,
            objective=objective,
            initial_capital=initial_capital,
            risk_manager=risk_mgr,
            slippage_bps=slippage_bps,
            progress_callback=_progress,
        )

        progress_bar.empty()
        status_text.empty()

        summary = wf_result.get('summary', {})
        windows = wf_result.get('windows', [])

        if summary.get('error'):
            st.warning(summary['error'])
        elif not windows:
            st.warning("No results. Ensure data exists for the selected symbol and timeframe.")
        else:
            st.success(f"✅ Walk-forward complete — {summary['total_windows']} windows")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Avg Out-of-Sample Return", f"{summary['avg_oos_return']:+.2f}%")
            with col2:
                st.metric("Avg In-Sample Return", f"{summary['avg_train_return']:+.2f}%")
            with col3:
                st.metric("Consistency (Positive OOS Windows)", f"{summary['consistency_pct']:.0f}%")

            st.markdown("### 📋 Window Detail")
            window_rows = []
            for w in windows:
                window_rows.append({
                    'Window': w.window_id,
                    'Train': f"{w.train_start.date()} → {w.train_end.date()}",
                    'Test': f"{w.test_start.date()} → {w.test_end.date()}",
                    'Best Params': ", ".join(f"{k}={v}" for k, v in w.best_params.items()),
                    'Train Return': f"{w.train_metrics.get('return_pct', 0.0):+.2f}%",
                    'Test (OOS) Return': f"{w.test_metrics.get('return_pct', 0.0):+.2f}%",
                    'Test Sharpe': f"{w.test_metrics.get('sharpe_ratio', 0.0):.2f}",
                })
            st.dataframe(pd.DataFrame(window_rows), use_container_width=True, height=250)

            st.markdown("### 📊 In-Sample vs. Out-of-Sample Return by Window")
            fig = go.Figure()
            fig.add_trace(go.Bar(x=[f"W{w.window_id}" for w in windows], y=summary['train_returns'],
                                 name='Train (In-Sample)', marker_color='rgba(100,149,237,0.6)'))
            fig.add_trace(go.Bar(x=[f"W{w.window_id}" for w in windows], y=summary['oos_returns'],
                                 name='Test (Out-of-Sample)', marker_color='dodgerblue'))
            fig.add_hline(y=0, line_dash="dot", line_color="gray")
            fig.update_layout(barmode='group', height=400, margin=dict(l=0, r=0, t=20, b=0), yaxis_title='Return %')
            st.plotly_chart(fig, use_container_width=True)

            st.caption("If out-of-sample returns are consistently much worse than in-sample (train) returns, "
                       "the strategy is likely overfitting to historical noise rather than a real edge.")

elif run_live_view:
    # ─── Live Paper Trading View ───────────────────────────────────
    st.markdown("### 📡 Live Paper Trading — Account & Session History")

    # Broker state (requires OpenD)
    try:
        from core.order_gateway import MoomooPaperGateway
        gw = MoomooPaperGateway()
        acc = gw.get_account_info() or {}
        positions = gw.get_positions() or {}
        orders = gw.list_recent_orders(days=14)
        gw.close()

        col1, col2, col3, col4 = st.columns(4)
        initial = 1_000_000.0  # Moomoo paper account starting balance
        pnl_pct = (acc.get('total_assets', initial) - initial) / initial * 100
        with col1:
            st.metric("Total Assets", f"HKD {acc.get('total_assets', 0):,.2f}",
                      f"{pnl_pct:+.3f}% all-time", delta_color="normal" if pnl_pct >= 0 else "inverse")
        with col2:
            st.metric("Cash", f"HKD {acc.get('cash', 0):,.2f}")
        with col3:
            st.metric("Market Value", f"HKD {acc.get('market_value', 0):,.2f}")
        with col4:
            st.metric("Open Positions", len(positions))

        if positions:
            st.markdown("#### Open Positions")
            st.dataframe(pd.DataFrame([
                {'Symbol': s, 'Qty': p['qty'], 'Avg Cost': f"HKD {p['entry_price']:.2f}"}
                for s, p in positions.items()
            ]), use_container_width=True)

        if orders:
            st.markdown("#### Recent Orders (14 days)")
            odf = pd.DataFrame(orders)
            cols = [c for c in ['code', 'trd_side', 'qty', 'price', 'order_status', 'create_time'] if c in odf.columns]
            st.dataframe(odf[cols], use_container_width=True, height=220)
        else:
            st.info("No orders in the last 14 days.")
    except Exception as e:
        st.warning(f"OpenD not reachable — broker state unavailable ({e}). Session history below.")

    # Session history from live_sessions/*.jsonl
    import json
    session_dir = Path(__file__).parent.parent / 'live_sessions'
    session_files = sorted(session_dir.glob('session_*.jsonl'), reverse=True)

    if session_files:
        st.markdown("#### Forward-Test Session History")
        rows = []
        for f in session_files:
            events = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
            start = next((e for e in events if e['type'] == 'session_start'), {})
            end = next((e for e in events if e['type'] == 'session_end'), {})
            candles = sum(1 for e in events if e['type'] == 'candle_close')
            rows.append({
                'Session': f.stem.replace('session_', ''),
                'Strategy': start.get('strategy', '?'),
                'Symbols': ', '.join(start.get('symbols', [])),
                'TF': start.get('timeframe', '?'),
                'Candles': candles,
                'Trades': end.get('trades', 'running' if not end else 0),
                'End Assets': f"HKD {end['account'].get('total_assets', 0):,.0f}" if end.get('account') else '—',
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=280)

        # Equity timeline of the most recent session with data
        for f in session_files:
            events = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
            closes = [e for e in events if e['type'] == 'candle_close']
            if len(closes) >= 2:
                eq_df = pd.DataFrame(closes)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=eq_df['timestamp'], y=eq_df['equity'],
                                         mode='lines+markers', line=dict(color='dodgerblue', width=2)))
                fig.update_layout(title=f'Equity — session {f.stem.replace("session_", "")}',
                                  yaxis_title='HKD', height=300, margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig, use_container_width=True)
                break
    else:
        st.info("No live sessions recorded yet. Run `./scripts/run_daily_candidates.sh` during market hours.")

else:
    st.info("👆 Select a **strategy** and adjust parameters on the sidebar, then click **Run Backtest**, **Compare All Strategies**, or **Run Optimization**.")
