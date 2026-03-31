import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path

# Add project root to path so we can import modules when running via Streamlit
import sys
sys.path.append(str(Path(__file__).parent))

from core.config import ConfigLoader
from core.storage import DataStorage
from core.portfolio import Portfolio
from core.strategy import MovingAverageCrossover
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

# 2. Strategy Parameters
st.sidebar.subheader("SMA Crossover Parameters")
fast_ma = st.sidebar.number_input("Fast MA Period", min_value=2, max_value=50, value=5, step=1)
slow_ma = st.sidebar.number_input("Slow MA Period", min_value=5, max_value=200, value=20, step=1)

timeframe_opts = {
    "1 Minute": Timeframe.MIN_1,
    "5 Minute": Timeframe.MIN_5,
    "1 Hour": Timeframe.HOUR_1,
    "1 Day": Timeframe.DAY_1
}
selected_tf_label = st.sidebar.selectbox("Timeframe", list(timeframe_opts.keys()))
timeframe = timeframe_opts[selected_tf_label]

st.sidebar.markdown("---")

# 3. Execution Action
run_sim = st.sidebar.button("🚀 Run Backtest Simulation", type="primary", use_container_width=True)

# Define helper for plotting Candlestick + MA
def plot_candlestick(df: pd.DataFrame, fast_p: int, slow_p: int):
    fig = go.Figure()

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name='Price'
    ))

    # Calculate MAs
    fast_series = df['close'].rolling(window=fast_p).mean()
    slow_series = df['close'].rolling(window=slow_p).mean()

    # Fast MA line
    fig.add_trace(go.Scatter(
        x=df.index, 
        y=fast_series, 
        line=dict(color='orange', width=2), 
        name=f'Fast MA ({fast_p})'
    ))

    # Slow MA line
    fig.add_trace(go.Scatter(
        x=df.index, 
        y=slow_series, 
        line=dict(color='blue', width=2), 
        name=f'Slow MA ({slow_p})'
    ))

    fig.update_layout(
        title=f'{symbol} ({selected_tf_label}) - Price & Moving Averages',
        yaxis_title='Price',
        xaxis_rangeslider_visible=False,
        height=600,
        margin=dict(l=0, r=0, t=40, b=0)
    )
    return fig


if run_sim:
    with st.spinner(f'Running Strategy Engine for {symbol}...'):
        # Initialize fresh portfolio 
        portfolio = Portfolio(initial_cash=100000.0, commission_rate=0.001)
        backtester = Backtester(storage=storage, portfolio=portfolio)

        # Run Backend Engine
        metrics = backtester.run(
            MovingAverageCrossover,
            symbols=[symbol],
            timeframe=timeframe,
            fast_period=fast_ma,
            slow_period=slow_ma
        )

        st.success("Simulation Complete!")

        # --- TOP ROW: KPIs ---
        if metrics:
            col1, col2, col3 = st.columns(3)
            
            # Format numbers
            equity = metrics['final_equity']
            ret_pct = metrics['return_pct']
            
            # Color coding for PnL
            delta_color = "normal" if ret_pct >= 0 else "inverse"
            
            with col1:
                st.metric("Final Equity", f"${equity:,.2f}", f"{ret_pct:+.2f}%", delta_color=delta_color)
            with col2:
                st.metric("Total Trades", f"{metrics['total_trades']}")
            with col3:
                cash = metrics['cash_balance']
                st.metric("Remaining Cash", f"${cash:,.2f}")

        # --- CENTER STAGE: Chart Overlay ---
        # Fetch the raw data that was just backtested to render the chart
        folder = symbol.replace('.', '_')
        raw_df = storage.load_data(folder, timeframe.value)
        
        if not raw_df.empty:
            st.markdown("### 📊 Market View & Strategy Overlay")
            fig = plot_candlestick(raw_df, fast_ma, slow_ma)
            st.plotly_chart(fig, use_container_width=True)
            
            # --- BOTTOM AREA: Trade Log ---
            st.markdown("### 📋 Execution Trade Log")
            trade_df = pd.DataFrame(portfolio.trade_history)
            
            if not trade_df.empty:
                # Format the table for nicer display
                trade_df['timestamp'] = trade_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
                trade_df['price'] = trade_df['price'].apply(lambda x: f"${x:.2f}")
                trade_df['commission'] = trade_df['commission'].apply(lambda x: f"${x:.2f}")
                trade_df['cash_after'] = trade_df['cash_after'].apply(lambda x: f"${x:.2f}")
                
                # Color code Action column
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

else:
    # Default waiting view
    st.info("👆 Adjust your parameters on the sidebar and click **Run Backtest Simulation** to process data.")
