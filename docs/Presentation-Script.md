# Algo-Trade — Presentation Speaker Script
### Course: Simulation and Modeling

**Deck:** `docs/Algo-Trade-Simulation-and-Modeling.pptx` (14 slides)
**Total target time:** ~12–14 minutes + Q&A

| Presenter | Reg. No. | Slides | Topic |
|-----------|----------|--------|-------|
| **Muhammad Ahmad Saleem** | F2022065202 | 1–5 | Intro, motivation, overview, course mapping, architecture |
| **Danish Salman** | F2022065114 | 6–9 | The model, the simulation engine, risk modeling, the UI |
| **Irha Shoaib** | F2021065136 | 10–14 | Tech stack, live demo, results, takeaways, close |

> **Tip:** Speak to the *ideas*, not the slide text. The lines below are a guide — say them in your own words. Bracketed `[...]` notes are stage directions, not spoken.

---

## 👤 PRESENTER 1 — Muhammad Ahmad Saleem (Slides 1–5)

### Slide 1 — Title  *(~45 sec)*
> "Good [morning/afternoon] everyone. We're group presenting **Algo-Trade** — an algorithmic options-trading system. But for this course, the interesting part isn't the trading — it's that this project is really an exercise in **modeling a real-world system and then simulating it safely**.
>
> I'm Muhammad Ahmad Saleem, and with me are Danish Salman and Irha Shoaib. I'll cover the problem and the architecture, Danish will walk through the model and the simulation engine, and Irha will show the tech, a demo, and our results."

[Advance.]

### Slide 2 — Motivation  *(~1 min)*
> "So why build this? Human trading is **slow, emotional, and inconsistent**. A person can't watch hundreds of stocks at once, and fear and greed make us break our own rules.
>
> That gives us a classic Simulation & Modeling question: **can we capture a trading decision as a precise model, then simulate that model against the market — before risking any real money?**
>
> Our answer has three parts: a **model** that turns price action into math, a **simulation** that runs it on historical and live data, and a **safety net** of risk rules so it can't blow up."

### Slide 3 — What is Algo-Trade?  *(~1 min)*
> "At a high level, Algo-Trade is an event-driven Python system, built strictly for **education and paper trading** — no real money involved.
>
> It does six things in a loop: it **scans** the market for the biggest movers, **filters** for tradable options, **generates a signal** using momentum indicators, **plans the trade** with entry, stop-loss and take-profit levels, **checks risk**, and finally **simulates or executes** the order. Each of those is a separate, testable component."

### Slide 4 — Mapping to Simulation & Modeling  *(~1.5 min — KEY slide)*
> "This is the slide that ties it to our course. Almost every concept we studied shows up here:
>
> - The indicators — RSI, MACD, ATR — are our **mathematical model** of price.
> - The system is a **discrete-event simulation**: an event bus drives it — a price tick triggers a signal, which triggers an order, which triggers a fill.
> - The strategy rules are the **system under test**.
> - The **backtester** is a historical simulation; **paper mode** is a real-time simulation.
> - The mock market gives us **stochastic input** — synthetic price paths.
> - And we get **validation metrics** out the other end: win rate, P&L, signal counts.
>
> So this isn't just a trading app — it's a full modeling-and-simulation pipeline."

### Slide 5 — Architecture  *(~1 min)*
> "Here's how it's wired. It's an **event-driven pipeline** — each stage emits a typed event onto a shared bus, and the next stage reacts. That decoupling means every block is independently testable.
>
> Data flows left to right: market data → screener → options fetcher → indicators → strategy engine → risk manager → execution. Underneath, we persist everything to a database, expose it through an API and dashboard, and log it all as structured JSON.
>
> **[Handoff]** Now Danish will take you inside the two most important blocks — the model and the simulation engine."

[Hand the clicker to Danish.]

---

## 👤 PRESENTER 2 — Danish Salman (Slides 6–9)

### Slide 6 — The modeling layer  *(~1.5 min)*
> "Thanks, Ahmad. Let's open up the model. The core question is: how do you turn messy price data into a decision? With three indicators.
>
> - **RSI** — the Relative Strength Index — is a 0-to-100 momentum gauge. Above 70 the stock is overbought, below 30 it's oversold. It tells us if a move is stretched.
> - **MACD** compares two moving averages — we use it as a **confirmation filter** so we don't act on noise.
> - **ATR** measures volatility, and we use it to size the stop-loss and take-profit — so the trade plan adapts to how wildly the market is moving.
>
> The important modeling point: these are all **pure functions** — same input, same output, fully unit-tested. That makes the model deterministic and reproducible, which is exactly what you want when you're going to simulate it thousands of times."

### Slide 7 — The simulation engine  *(~1.5 min)*
> "Now the simulation itself. The same model can run in four modes:
>
> - **Backtest** — a historical simulation. We replay recorded minute-by-minute data through the whole pipeline and print a P&L report. Fast and repeatable.
> - **Paper** — a real-time simulation. Live data flows in, but a **mock broker** simulates the fills. Full behavior, zero real money. This is what we'll demo.
> - **Manual** — it only logs recommendations; a human decides.
> - **Automated** — real orders through a real broker. We keep this **disabled** — we mention it only to show the deployment path.
>
> So the same model, swappable simulation backends — that separation is the heart of the design."

### Slide 8 — Risk modeling  *(~1 min)*
> "A model that can blow up is useless, so risk is a **first-class part of the simulation**, not an afterthought.
>
> - **Position sizing** caps every trade at a small percent of equity — by default 5% — so one bad bet can't sink the account.
> - The **daily circuit breaker** halts all trading once we hit either a profit target or a loss limit. It's basically a 'know when to walk away' rule, encoded.
> - And we monitor the **Pattern-Day-Trader rule** to keep the simulated account inside realistic constraints."

### Slide 9 — The dashboard  *(~1 min)*
> "All of this is visible in a live dashboard. You get top-line stats — market status, open positions, signals fired, uptime — a price chart, and the circuit-breaker banner. There are dedicated pages for positions, signals, backtests, strategies, history, and settings, where you can actually tune the model's parameters.
>
> It's built with Next.js, React and Tailwind, and updates in real time.
>
> **[Handoff]** Irha will now show the tech behind it, give a quick demo, and walk through our results."

[Hand the clicker to Irha.]

---

## 👤 PRESENTER 3 — Irha Shoaib (Slides 10–14)

### Slide 10 — Tech stack  *(~1 min)*
> "Thanks, Danish. Quick tour of what it's built on. The **backend** is Python with asyncio and an aiohttp API, SQLAlchemy for storage, and a pytest suite. The **modeling** layer is our pure-function indicators and the strategy engine. The **frontend** is Next.js, React and Tailwind with live updates. And on **ops**, it's containerized with Docker, has structured logging with secret redaction, and supports both a mock and a real broker adapter.
>
> So it's a genuine full-stack system, but every piece is testable in isolation."

### Slide 11 — Live demo  *(~2 min)*
> "Let me show it running. **[Switch to terminal / pre-recorded clip.]**
>
> First I start the backend in paper mode — notice it needs **no API keys**, it runs fully offline on mock data. Watch the logs: you can literally see the pipeline — it scans, the model computes RSI and MACD, it fires a signal, risk approves the size, and the mock broker fills it.
>
> Then I start the dashboard — here are the live stats, the chart, and the circuit-breaker banner updating.
>
> And finally a backtest — one command replays historical data and prints a performance report."
>
> *(If the live demo fails: "In the interest of time, here's a screenshot of the same run" — have a backup screenshot/GIF ready.)*

### Slide 12 — Results  *(~1 min)*
> "Every run gives us measurable output — which is exactly what a modeling project needs. We get the **signal count**, the **number of trades** taken after filtering, the **win rate**, and the **net P&L**.
>
> And here's why that matters: because the model is deterministic and the simulation is repeatable, we can change **one parameter** — say the RSI threshold or the position size — re-run, and directly compare. That experiment-and-compare loop is the core of modeling."

### Slide 13 — Key takeaways  *(~1 min)*
> "To wrap up, five takeaways:
> - We **modeled a real-world system** — market behavior as testable math.
> - We took a **simulation-first** approach, so we could evaluate safely and repeatably.
> - **Risk is built into the model**, keeping the simulation realistic.
> - The engineering is **clean and modular** — event-driven and unit-tested.
> - And it's **full-stack and demoable**, running entirely offline."

### Slide 14 — Thank you / Q&A  *(~30 sec)*
> "That's Algo-Trade — modeling the market, simulating the strategy, and keeping it safe, all for educational use. Thank you for listening — we'd be happy to take any questions."

[All three presenters step forward for Q&A.]

---

## 🎯 Anticipated Q&A — quick answers for all three

- **"Does it use real money?"** No — it's strictly paper trading and backtesting. The live-broker mode exists but is disabled.
- **"How accurate are the predictions?"** It's not predicting the future — it's a rule-based momentum model. We measure it by win rate and P&L on simulated runs, not by claiming forecasts.
- **"Why RSI and MACD specifically?"** They're standard, well-understood momentum indicators — RSI for overbought/oversold, MACD as a trend confirmation. Easy to model as pure functions and easy to reason about.
- **"What's the 'simulation' part exactly?"** Two things: backtesting replays historical data through the model, and paper mode runs live data through a mock broker. Both let us observe the system's behavior without real execution.
- **"Could you add more indicators / a different strategy?"** Yes — the strategy engine is modular, so a new indicator or rule set plugs in without touching the rest of the pipeline.
- **"What did each of you build?"** [Be ready to say who worked on backend/model, frontend, and testing/risk.]

---

## ⏱️ Timing cheat-sheet

| Section | Presenter | Time |
|---------|-----------|------|
| Slides 1–5 | Ahmad | ~5 min |
| Slides 6–9 | Danish | ~5 min |
| Slides 10–14 | Irha | ~4.5 min |
| Q&A | All | ~3 min |
