# Week 9 Recording Script
**Estimated duration: ~3–4 minutes**

---

### 📄 SLIDE 1 — Title
> Hi Professor, this is my Week 9 progress update for the Mid-Frequency Market Data Service project. In this update, I'll cover the challenges I encountered with Interactive Brokers, my research into alternative data sources, and the successful integration of a new platform called Moomoo.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 2 — Recap
> Just to recap, in my last update I had successfully designed a modular provider architecture with an abstract BaseDataProvider interface. I implemented the Interactive Brokers provider and was able to retrieve historical candlestick data by connecting to the TWS Gateway. However, Tests 2 and 3 — getting the latest live quote and live data streaming — both failed because they require a paid market data subscription.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 3 — IBKR Cost
> After investigating further, I found that IBKR's live market data subscription only costs about fourteen fifty US dollars per month, which is actually very affordable compared to institutional-grade data feeds. The real barrier is that IBKR requires a minimum five hundred dollar USD deposit into the brokerage account before they will even start deducting subscription fees. Now, IBKR does offer free delayed data, but it is 15 minutes behind real-time, which completely defeats the purpose of a live trading bot. So I had to look for alternative broker platforms.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 4 — Why Not Yahoo
> Now, you might ask why I don't just use free sources like Yahoo Finance. There are three main problems. First, these platforms aggregate data across multiple exchanges — the prices may differ slightly from the actual exchange order book, which matters for precision trading. Second, their live data is also 15 minutes delayed. And third — we need both historical data for backtesting AND live streaming data from the same source. If we mix providers, there will be price discrepancies between our backtest results and actual live execution, which makes the strategy unreliable.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 5 — Moomoo Discovery
> I researched alternative broker platforms and found Moomoo, operated by Futu Holdings — a Hong Kong-based brokerage. They offer a Python SDK called moomoo-api and a local gateway application called OpenD, which is essentially their equivalent of IBKR's TWS Gateway. The key discovery is that while US equity live data costs ninety-nine dollars per month, live market data for all Hong Kong and China A-share stocks is completely free. This includes both historical AND live streaming data, directly from the exchange — which solves all three problems I mentioned earlier.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 6 — Architecture
> For the implementation, I wrote two new scripts. The first is moomoo_provider.py — this is the adapter layer that implements the same BaseDataProvider interface as our IBKR provider, meaning it provides the same standardized functions like get_historical_data and start_live_streaming. The second script is moomoo_demo.py, which is the test runner that actually executes the connection and runs our tests. The data flows over a persistent TCP socket connection using Google Protobuf for binary serialization. This is the same approach used by IBKR's TWS — it's lower latency than REST APIs because the server pushes data directly to the client.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 7 — Test Results
> And here are the test results. Test 1, historical candlestick data — passed successfully. I was able to retrieve 22 daily candles covering a 30-day history for both Tencent and HSBC, stored locally in Parquet format. Test 2, live data streaming — also passed. The system received real-time one-minute candles with live price and volume updates being pushed to the script on every tick. I tested this for both Tencent and HSBC, and was able to capture live data right up until the Hong Kong market closed at 4 PM. Importantly, both historical and live data come from the same source, which is exactly what we need.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 8 — Live Output Evidence
> Here you can see the actual console output from the live streaming test. Each row represents a one-minute OHLCV candle that was streamed to us in real-time. You can see the timestamp, open, high, low, close, and volume fields. The last candle at 16:00 in green captures the exact market close. This data is automatically saved to local Parquet storage, which we will use later for backtesting.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 9 — OpenD Quotas
> OpenD has a quota system with two limits. First, live subscriptions — we can have up to 100 concurrent streams. Each symbol and data type combination uses one slot. Right now we've used 2 out of 100. These are freed when you disconnect and are NOT billing-based — they are entirely free capacity limits. Second, the historical kline quota allows up to 100 requests per minute, which resets every 60 seconds. This gives us more than enough capacity to monitor around 20 to 33 stocks simultaneously, which is sufficient for our mid-frequency trading strategy. One caveat to note — since we're using HK market data, live streaming tests must be conducted during Hong Kong trading hours, which is 9:30 AM to 4 PM.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 10 — Next Steps
> Looking ahead, my next step is to build a skeleton for a GUI dashboard using the Streamlit framework. This will include live candlestick charting, strategy parameter controls, and PnL metrics display. Beyond that, I plan to develop a Strategy Engine with a plug-and-play design so I can easily test different algorithms, starting with a simple Moving Average Crossover strategy. And finally, a backtesting module that replays our stored historical data through these strategies to generate performance metrics like return percentage, win rate, and maximum drawdown.

**[ADVANCE SLIDE]**

---

### 📄 SLIDE 11 — Summary
> To summarize — IBKR historical data works but live streaming requires a five hundred dollar deposit. Free aggregators like Yahoo Finance are unsuitable due to delayed and aggregated data. I successfully integrated Moomoo, which provides free live and historical data for Hong Kong stocks. Both tests — historical retrieval and live streaming — passed, with data stored in Parquet format ready for backtesting. Thank you, Professor.

---

**🎬 END OF RECORDING**
