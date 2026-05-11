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
selected_strategy = st.sidebar.selectbox("Strategy", strategy_names)
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

# 5. Execution Actions
run_sim = st.sidebar.button("🚀 Run Backtest", type="primary", use_container_width=True)
run_compare = st.sidebar.button("⚡ Compare All Strategies", use_container_width=True)


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


# Map strategy name → plot function
PLOT_FUNCTIONS = {
    "SMA Crossover": plot_sma_crossover,
    "RSI": plot_rsi,
    "MACD": plot_macd,
    "Bollinger Bands": plot_bollinger,
    "Z-Score Mean Reversion": plot_zscore,
    "Pairs Trading": plot_pairs,
    "Ensemble Voting": plot_ensemble,
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
        portfolio = Portfolio(initial_cash=initial_capital, commission_rate=0.001)
        backtester = Backtester(storage=storage, portfolio=portfolio)

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
            col1, col2, col3, col4, col5, col6 = st.columns(6)

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

        # --- CHART ---
        folder = symbol.replace('.', '_')
        raw_df = storage.load_data(folder, timeframe.value)

        if not raw_df.empty:
            st.markdown(f"### 📊 {selected_strategy} — Market View & Indicator Overlay")
            plot_fn = PLOT_FUNCTIONS[selected_strategy]
            fig = plot_fn(raw_df, strategy_params)
            st.plotly_chart(fig, use_container_width=True)

            # --- EQUITY CURVE ---
            if portfolio.equity_curve:
                st.markdown("### 📈 Equity Curve")
                eq_fig = plot_equity_curve(portfolio.equity_curve)
                st.plotly_chart(eq_fig, use_container_width=True)

            # --- TRADE LOG ---
            st.markdown("### 📋 Execution Trade Log")
            trade_df = pd.DataFrame(portfolio.trade_history)

            if not trade_df.empty:
                trade_df['timestamp'] = trade_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
                trade_df['price'] = trade_df['price'].apply(lambda x: f"HKD {x:.2f}")
                trade_df['commission'] = trade_df['commission'].apply(lambda x: f"HKD {x:.2f}")
                trade_df['cash_after'] = trade_df['cash_after'].apply(lambda x: f"HKD {x:.2f}")

                def highlight_action(val):
                    color = '#4CAF50' if val.upper() == 'BUY' else '#F44336'
                    return f'color: white; background-color: {color}; padding: 4px; border-radius: 4px; text-align: center; font-weight: bold;'

                st.dataframe(
                    trade_df.style.map(highlight_action, subset=['action']),
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
        portfolio = Portfolio(initial_cash=initial_capital, commission_rate=0.001)
        bt = Backtester(storage=storage, portfolio=portfolio)
        defaults = {k: v['default'] for k, v in info['params'].items()}

        f_buf = io.StringIO()
        with contextlib.redirect_stdout(f_buf):
            metrics = bt.run(info['class'], symbols=[symbol], timeframe=timeframe, 
                             start_date=start_date, end_date=end_date, **defaults)

        if metrics:
            results.append({
                'Strategy': strat_name,
                'Return %': f"{metrics['return_pct']:+.2f}%",
                'Trades': metrics['total_trades'],
                'Win Rate': f"{metrics['win_rate']:.1f}%",
                'Sharpe': f"{metrics['sharpe_ratio']:.2f}",
                'Max DD': f"{metrics['max_drawdown']:.2f}%",
                'Final Equity': f"HKD {metrics['final_equity']:,.2f}",
                '_return': metrics['return_pct'],  # for sorting
            })
            if portfolio.equity_curve:
                equity_curves[strat_name] = portfolio.equity_curve

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

else:
    st.info("👆 Select a **strategy** and adjust parameters on the sidebar, then click **Run Backtest** or **Compare All Strategies**.")
