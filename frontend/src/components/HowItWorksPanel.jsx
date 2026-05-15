// ── Helpers ───────────────────────────────────────────────────────────────────
function Section({ title, children }) {
  return (
    <section>
      <h3 className="text-blue-400 font-semibold text-base mb-2">{title}</h3>
      {children}
    </section>
  )
}

function Tag({ children, cls = 'text-white' }) {
  return <span className={`font-mono ${cls}`}>{children}</span>
}

function Row({ label, cls = 'text-md-on-surface', children }) {
  return (
    <li>
      <Tag cls={cls}>{label}</Tag>
      {children && <span className="text-md-on-surface-var"> — {children}</span>}
    </li>
  )
}

function ScoreRow({ engine, signal, pts }) {
  return (
    <tr className="border-b border-md-outline-var/50">
      <td className="py-0.5 pr-4 text-md-on-surface-var">{engine}</td>
      <td className="py-0.5 pr-4 text-md-on-surface">{signal}</td>
      <td className="py-0.5 text-lime-400">{pts}</td>
    </tr>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
export default function HowItWorksPanel() {
  return (
    <div className="bg-md-surface-con rounded-md-md border border-md-outline-var p-6 overflow-auto max-h-[700px]">

      <div className="flex items-baseline gap-3 mb-6">
        <h2 className="text-lg font-bold text-white">How It Works</h2>
        <span className="text-xs text-md-on-surface-var">Sachoki Screener — all signal engines explained</span>
      </div>

      <div className="space-y-8 text-sm text-md-on-surface">

        {/* ── Overview ── */}
        <Section title="Overview">
          <p>
            Sachoki Screener fetches OHLCV data and runs multiple independent signal engines on
            every ticker. Results are stored in SQLite and ranked through a unified scoring system.
            All engines share the same pipeline: fetch → compute → score → rank.
          </p>
          <ul className="list-disc list-inside mt-2 space-y-1">
            <li>Backend: <Tag>Python / FastAPI</Tag> — signal computation, SQLite persistence</li>
            <li>Frontend: <Tag>React / Tailwind</Tag> — real-time tables, chart, watchlist</li>
            <li>Primary data: <Tag>Massive API</Tag> (Polygon-compatible) — fast, no rate limits</li>
            <li>Fallback data: <Tag>yfinance</Tag> — Yahoo Finance OHLCV when Massive unavailable</li>
            <li>Timeframes: <Tag>1W / 1D / 4H / 1H</Tag></li>
            <li>State: watchlist, ticker, TF, active tab persisted in <Tag>localStorage</Tag></li>
          </ul>
        </Section>

        {/* ── TURBO Scanner ── */}
        <Section title="⚡ TURBO Scanner">
          <p className="mb-2">
            The flagship tab. Scans an entire universe (up to ~2200 tickers) through every engine
            simultaneously and assigns each ticker a composite <Tag cls="text-lime-300">turbo_score 0–100</Tag>.
          </p>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">Universes</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="S&P 500" cls="text-blue-300">~500 large-caps</Row>
            <Row label="NASDAQ" cls="text-cyan-300">~1100 NASDAQ stocks across all price ranges</Row>
            <Row label="Russell 2K" cls="text-orange-300">~2000 small-caps via iShares IWM CSV</Row>
            <Row label="All US" cls="text-md-on-surface">combined universe, capped at 2000 random sample</Row>
          </ul>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">Score Tiers</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="Fire ≥ 65" cls="text-lime-300">multiple strong signals aligning across engines</Row>
            <Row label="Strong ≥ 50" cls="text-yellow-300">solid multi-engine confirmation</Row>
            <Row label="Bull ≥ 35" cls="text-blue-300">base bullish setup</Row>
            <Row label="Base ≥ 20" cls="text-md-on-surface">sparse signals, worth watching</Row>
          </ul>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">Score Families (max ~100) — weights v3 (SP500 pooled stats, 500 tickers 2yr)</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Family (cap)</th>
                  <th className="text-left py-1 pr-4">Signal</th>
                  <th className="text-left py-1">Points</th>
                </tr>
              </thead>
              <tbody>
                {/* Backbone */}
                <ScoreRow engine="Backbone (18)" signal="conso_2809 (CON)" pts="+4" />
                <ScoreRow engine="Backbone (18)" signal="tz_bull (T/Z bull state)" pts="+6" />
                <ScoreRow engine="Backbone (18)" signal="conso + tz_bull + bf_buy — full chain bonus" pts="+8" />
                <ScoreRow engine="Backbone (18)" signal="conso + tz_bull — partial chain bonus" pts="+3" />
                {/* Vol/Accum */}
                <ScoreRow engine="Vol/Accum (22)" signal="ABS (absorption)  [Avg3 2.58%]" pts="+5" />
                <ScoreRow engine="Vol/Accum (22)" signal="L88 signal" pts="+5" />
                <ScoreRow engine="Vol/Accum (22)" signal="SQ  [Win% 57.5%, Avg3 2.63%]" pts="+5" />
                <ScoreRow engine="Vol/Accum (22)" signal="CLB (climb)  [Avg3 2.80%]" pts="+5" />
                <ScoreRow engine="Vol/Accum (22)" signal="LD (load)  [Avg3 2.69%]" pts="+5" />
                <ScoreRow engine="Vol/Accum (22)" signal="VBO↑  [Avg3 2.37%]" pts="+4" />
                <ScoreRow engine="Vol/Accum (22)" signal="NS (no supply)  [Avg3 2.35%]" pts="+4" />
                <ScoreRow engine="Vol/Accum (22)" signal="SVS 2809 (vol spike in conso)" pts="+3" />
                <ScoreRow engine="Vol/Accum (22)" signal="UM 2809 (upmove label)" pts="+3" />
                <ScoreRow engine="Vol/Accum (22)" signal="VA (vol/avg >2× crossover)" pts="+3" />
                <ScoreRow engine="Vol/Accum (22)" signal="260308" pts="+3" />
                <ScoreRow engine="Vol/Accum (22)" signal="SC (selling climax)" pts="+2" />
                {/* Breakout */}
                <ScoreRow engine="Breakout (18)" signal="BE↑ (full-body engulf)" pts="+10" />
                <ScoreRow engine="Breakout (18)" signal="4BF (bf_buy)" pts="+6" />
                <ScoreRow engine="Breakout (18)" signal="FBO↑ (bear trap)" pts="+5" />
                <ScoreRow engine="Breakout (18)" signal="RS+ (vs SPY+IWM)" pts="+5" />
                <ScoreRow engine="Breakout (18)" signal="BO↑ / BX↑" pts="+5" />
                <ScoreRow engine="Breakout (18)" signal="EB↑  [Avg3 2.39%]" pts="+4" />
                <ScoreRow engine="Breakout (18)" signal="RS (relative strength)" pts="+3" />
                <ScoreRow engine="Breakout (18)" signal="3↑  [Avg3 1.65%]" pts="+3" />
                {/* Combo */}
                <ScoreRow engine="Combo (14)" signal="🚀 ROCKET" pts="+12" />
                <ScoreRow engine="Combo (14)" signal="BUY 2809" pts="+8" />
                <ScoreRow engine="Combo (14)" signal="CD (Bull Dom + B)" pts="+5" />
                <ScoreRow engine="Combo (14)" signal="HILO↑" pts="+4" />
                <ScoreRow engine="Combo (14)" signal="3G gap" pts="+4" />
                <ScoreRow engine="Combo (14)" signal="CA (Bull Att + B)" pts="+3" />
                <ScoreRow engine="Combo (14)" signal="SBC (seqBContLite)" pts="+3" />
                <ScoreRow engine="Combo (14)" signal="RTV" pts="+3" />
                <ScoreRow engine="Combo (14)" signal="CW (Bear Weak + B)" pts="+2" />
                <ScoreRow engine="Combo (14)" signal="ATR↑ / BB↑" pts="+2" />
                {/* L-structure — T/Z weights are profile-dependent */}
                <ScoreRow engine="L-struct (17)" signal="T2G" pts="+8 (both)" />
                <ScoreRow engine="L-struct (17)" signal="T1  [SP500]" pts="+7" />
                <ScoreRow engine="L-struct (17)" signal="T1G  [both]" pts="+6" />
                <ScoreRow engine="L-struct (17)" signal="FRI34" pts="+6" />
                <ScoreRow engine="L-struct (17)" signal="T2 / T1 [NASDAQ]" pts="+5" />
                <ScoreRow engine="L-struct (17)" signal="L34 (without FRI34)" pts="+5" />
                <ScoreRow engine="L-struct (17)" signal="FRI43" pts="+4" />
                <ScoreRow engine="L-struct (17)" signal="T4 / T6  [NASDAQ]" pts="+7" />
                <ScoreRow engine="L-struct (17)" signal="T4 / T6  [SP500]" pts="+6" />
                <ScoreRow engine="L-struct (17)" signal="L43 (without FRI)  [Avg3 2.60%]" pts="+5" />
                <ScoreRow engine="L-struct (17)" signal="BL (blue trend)  [Avg3 2.76%]" pts="+5" />
                <ScoreRow engine="L-struct (17)" signal="RL (fuchsia)  [Avg3 2.80%]" pts="+5" />
                <ScoreRow engine="L-struct (17)" signal="T9 / T10  [SP500]  /  T10 [NASDAQ]" pts="+4" />
                <ScoreRow engine="L-struct (17)" signal="TZ→3 (Bull Dom flip) alone" pts="+4" />
                <ScoreRow engine="L-struct (17)" signal="TZ→3 (Bull Dom flip) + bf_buy" pts="+3" />
                <ScoreRow engine="L-struct (17)" signal="T9  [NASDAQ]" pts="+3" />
                <ScoreRow engine="L-struct (17)" signal="TZ→2 (Rev Attempt)" pts="+2" />
                <ScoreRow engine="L-struct (17)" signal="CCI ready" pts="+2" />
                <ScoreRow engine="L-struct (17)" signal="W (tz_weak_bull — BearWeak turn)" pts="+2" />
                <ScoreRow engine="L-struct (17)" signal="T3 / T11" pts="+2" />
                <ScoreRow engine="L-struct (17)" signal="T5" pts="+1" />
                {/* Delta */}
                <ScoreRow engine="Delta (12)" signal="dSPR — d_spring  [Avg3 3.36% 🥇 #1]" pts="+6" />
                <ScoreRow engine="Delta (12)" signal="Ab↑ — d_absorb_bull  [Avg3 2.99% 🥉 #3]" pts="+6" />
                <ScoreRow engine="Delta (12)" signal="ΔΔ↑ — d_blast_bull  [Avg3 2.46%]" pts="+5" />
                <ScoreRow engine="Delta (12)" signal="Δ↑ — d_surge_bull  [Avg3 2.43%]" pts="+5" />
                <ScoreRow engine="Delta (12)" signal="T↓ — d_div_bull  [Avg3 2.54%]" pts="+5" />
                <ScoreRow engine="Delta (12)" signal="VD↓ — d_vd_div_bull" pts="+3" />
                <ScoreRow engine="Delta (12)" signal="B/S↑ — d_strong_bull  [Avg3 2.03% Win%48.9%]" pts="+4" />
                <ScoreRow engine="Delta (12)" signal="CD↑ — d_cd_bull" pts="+2" />
                {/* EMA cross */}
                <ScoreRow engine="EMA-cross (10)" signal="P66 (cross EMA200+)" pts="+8" />
                <ScoreRow engine="EMA-cross (10)" signal="P55 (cross EMA89+)" pts="+6" />
                <ScoreRow engine="EMA-cross (10)" signal="P89 (cross EMA89)" pts="+5" />
                <ScoreRow engine="EMA-cross (10)" signal="P3  [Avg3 2.48%]" pts="+5" />
                <ScoreRow engine="EMA-cross (10)" signal="P2  [Avg3 2.40%]" pts="+4" />
                {/* G signals */}
                <ScoreRow engine="G signals (10)" signal="G2  [Avg3 2.64% Win%54.9% — best]" pts="+5" />
                <ScoreRow engine="G signals (10)" signal="G4 / G1" pts="+3 each" />
                <ScoreRow engine="G signals (10)" signal="G6 / G11" pts="+2 each" />
                {/* Context — uncapped, max ~18 */}
                <ScoreRow engine="Context (uncapped)" signal="WK↑ (wick_bull) — bull wick confirm" pts="+5" />
                <ScoreRow engine="Context (uncapped)" signal="X2G (gap continuation)" pts="+5" />
                <ScoreRow engine="Context (uncapped)" signal="X2 / X1G (wick reversal)" pts="+4 each" />
                <ScoreRow engine="Context (uncapped)" signal="FLY ABCD (full A→B→C→D)" pts="+4" />
                <ScoreRow engine="Context (uncapped)" signal="X1 (inside reversal)" pts="+3" />
                <ScoreRow engine="Context (uncapped)" signal="RETEST (PARA)  [False%6.3%]" pts="+3" />
                <ScoreRow engine="Context (uncapped)" signal="FLY CD / BD / AD" pts="+3" />
                <ScoreRow engine="Context (uncapped)" signal="Vol×10 spike  [Avg3 5.51% Win%61.8%]" pts="+10" />
                <ScoreRow engine="Context (uncapped)" signal="PARA / PARA+" pts="+2" />
                <ScoreRow engine="Context (uncapped)" signal="X3 (wick align)" pts="+2" />
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">Backtest Confluence Bonuses (cap 18) — Run 25, n=2254, Jan–Apr 2026</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Pattern</th>
                  <th className="text-left py-1 pr-4">Condition</th>
                  <th className="text-left py-1">Points</th>
                </tr>
              </thead>
              <tbody>
                <ScoreRow engine="L34_then_D4 (3B)" signal="L34 fired 1–3 bars ago, D4 now — +7.87% (n=31)" pts="+15" />
                <ScoreRow engine="D6 + BE↑ same bar" signal="+6.26% avg 5d, 71% win (n=32)" pts="+12" />
                <ScoreRow engine="D4_then_BE↑ (5B)" signal="D4 fired 1–5 bars ago, BE↑ now — +5.33% (n=54)" pts="+10" />
                <ScoreRow engine="D4 + L34 same bar" signal="+2.53% avg 5d, 70.8% win (n=24)" pts="+5" />
                <ScoreRow engine="D4 + BE↑ same bar" signal="+2.89% avg 5d, alpha +3.47% (n=52)" pts="+5" />
                <ScoreRow engine="L34_then_BE↑ (3B)" signal="L34 fired 1–3 bars ago, BE↑ now — +1.77% (n=55)" pts="+3" />
                <ScoreRow engine="NS + cons_atr + L34" signal="Accumulation-ready analog — 66.1% win" pts="+4" />
              </tbody>
            </table>
          </div>
          <p className="text-md-on-surface-var text-xs mt-1">
            D4 = d_absorb_bull | d_spring · D6 = d_surge_bull | d_blast_bull · L34 = l34 | fri34
          </p>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">SP500 Profile Combo Bonuses (cap 20)</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Combo</th>
                  <th className="text-left py-1 pr-4">Signals required</th>
                  <th className="text-left py-1">Points</th>
                </tr>
              </thead>
              <tbody>
                <ScoreRow engine="SQ + CLM + Ztrap₅" signal="accumulation surge inside trap context" pts="+12" />
                <ScoreRow engine="SQ + LOAD + Ztrap₅" signal="loading + squeeze in trap zone" pts="+12" />
                <ScoreRow engine="L64₅ + T1/T1G + SVS" signal="long structural base + turn + vol expansion" pts="+12" />
                <ScoreRow engine="NS + UM" signal="supply exhaustion + institutional ignition" pts="+10" />
                <ScoreRow engine="L43₅ + CLM + Ztrap₅" signal="structural reset + climber in trap" pts="+10" />
                <ScoreRow engine="(L22₅|L64₅) + SQ + Ztrap₅" signal="structural context + squeeze in trap" pts="+8" />
              </tbody>
            </table>
          </div>
          <p className="text-md-on-surface-var text-xs mt-1">Ztrap₅ / L64₅ / L43₅ / L22₅ = signal fired within last 5 bars</p>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">NASDAQ Profile Combo Bonuses (cap 25)</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Combo</th>
                  <th className="text-left py-1 pr-4">Signals required</th>
                  <th className="text-left py-1">Points</th>
                </tr>
              </thead>
              <tbody>
                <ScoreRow engine="ZTRAP_T6_C" signal="Ztrap₅ + T6 + RTB phase C" pts="+14" />
                <ScoreRow engine="L64_UM_T6" signal="L64₅ + UM_2809 + T6" pts="+14" />
                <ScoreRow engine="UM_BE" signal="UM_2809 + BE↑" pts="+12" />
                <ScoreRow engine="ZTRAP_UM_T4" signal="Ztrap₅ + UM_2809 + T4" pts="+12" />
                <ScoreRow engine="T6_BTOC" signal="T6 + RTB transition B→C" pts="+12" />
                <ScoreRow engine="SQ_BL_C" signal="SQ + (BLUE | blue₅) + RTB phase C" pts="+8" />
                <ScoreRow engine="LOAD_BL_C" signal="LOAD + (BLUE | blue₅) + RTB phase C" pts="+8" />
                <ScoreRow engine="UM_VBO" signal="UM_2809 + VBO↑" pts="+8" />
                <ScoreRow engine="UM_BX" signal="UM_2809 + BX↑" pts="+8" />
                <ScoreRow engine="L22/L64_SQ_LOAD" signal="(L22₅|L64₅) + SQ + LOAD" pts="+8" />
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">Kill / Penalty Conditions</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Condition</th>
                  <th className="text-left py-1 pr-4">Reason</th>
                  <th className="text-left py-1 text-red-400">Penalty</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface">RSI &gt; 80, no D4/D6</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">overheated, no absorption</td>
                  <td className="py-0.5 text-red-400">−6</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface">D6 + L34, no BE↑</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">avg 5d −2.52% (opposite of D6+BE↑)</td>
                  <td className="py-0.5 text-red-400">−5</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface">Isolated G4/G6 (no L34/BE↑/D4)</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">34.7% of all false positives</td>
                  <td className="py-0.5 text-red-400">−4</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface">RSI &gt; 75, no D4/D6/BE↑</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">extended without structure</td>
                  <td className="py-0.5 text-red-400">−3</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface">BC without BE↑</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">buying climax — distribution risk</td>
                  <td className="py-0.5 text-red-400">−3</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface">d_strong_bull alone (no structure)</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">IMPULSE_ONLY path avg −1.66%</td>
                  <td className="py-0.5 text-red-400">−3</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface italic">NASDAQ: RL without UM/BE↑/VBO↑/BX↑</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">weak on NASDAQ without activation</td>
                  <td className="py-0.5 text-red-400">−2</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface italic">NASDAQ: T4/T6 without context</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var">combos provide the uplift, not T alone</td>
                  <td className="py-0.5 text-red-400">−2</td>
                </tr>
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">Signal families — scores TBD</p>
          <p className="text-md-on-surface-var text-xs mb-2">
            Computed and shown as badges but not yet added to the turbo_score formula.
          </p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Family</th>
                  <th className="text-left py-1 pr-4">Signal</th>
                  <th className="text-left py-1 text-sky-400">Suggested range</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var">RGTI 260404</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">LL / UP / ↑↑ / ↑↑↑</td>
                  <td className="py-0.5 text-sky-400">+3 to +7 depending on tier</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var">RGTI 260404</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">ORG / GRN / GC</td>
                  <td className="py-0.5 text-sky-400">+2 to +6 depending on tier</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var">SMX 260402</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">SMX (near recent low in uptrend)</td>
                  <td className="py-0.5 text-sky-400">+4 to +6 — rare, high-conviction entry</td>
                </tr>
              </tbody>
            </table>
          </div>

          <p className="mt-2 text-md-on-surface-var text-xs">
            N= selector (1/3/5/10) controls how many recent bars are checked for each signal —
            wider window catches setups that fired a few bars ago.
          </p>
        </Section>

        {/* ── SIGNAL_SCORE ── */}
        <Section title="📐 SIGNAL_SCORE (0–160) — GOG + Context Scoring">
          <p className="mb-2">
            A structured score built on top of the GOG tier system. Unlike the TURBO score
            (which counts signal density), SIGNAL_SCORE quantifies <em>setup quality</em>:
            a strong GOG tier with the right context tokens can reach 160 pts.
            Visible in the Superchart SCORE row, the Turbo GOG column, and all CSV exports.
          </p>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">7.1 — GOG Base Score (highest active tier wins)</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Tier</th>
                  <th className="text-left py-1 pr-4">Meaning</th>
                  <th className="text-left py-1">Base pts</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['G1P', 'GOG-1 Premium — top-tier setup',           100, 'text-yellow-300'],
                  ['G1C', 'GOG-1 Classic — strong standard setup',      88, 'text-lime-300'],
                  ['G1L', 'GOG-1 Lite — valid but lighter context',      82, 'text-lime-400'],
                  ['GOG1','GOG-1 — basic Grail-Of-Grails tier 1',        70, 'text-green-300'],
                  ['G2P', 'GOG-2 Premium',                               62, 'text-teal-300'],
                  ['G2C', 'GOG-2 Classic',                               55, 'text-teal-400'],
                  ['G2L', 'GOG-2 Lite',                                  45, 'text-cyan-300'],
                  ['GOG2','GOG-2 base',                                  35, 'text-cyan-400'],
                  ['G3P', 'GOG-3 Premium',                               60, 'text-blue-300'],
                  ['G3C', 'GOG-3 Classic',                               52, 'text-blue-400'],
                  ['G3L', 'GOG-3 Lite',                                  48, 'text-sky-300'],
                  ['GOG3','GOG-3 base',                                  42, 'text-sky-400'],
                ].map(([tier, desc, pts, cls]) => (
                  <tr key={tier} className="border-b border-md-outline-var/50">
                    <td className={`py-0.5 pr-4 font-mono font-semibold ${cls}`}>{tier}</td>
                    <td className="py-0.5 pr-4 text-md-on-surface-var">{desc}</td>
                    <td className="py-0.5 text-lime-400">+{pts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">7.2–7.6 — Context Bonus Score</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">§</th>
                  <th className="text-left py-1 pr-4">Component</th>
                  <th className="text-left py-1 pr-4">Token(s)</th>
                  <th className="text-left py-1">Points</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.2</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">Premium Context</td>
                  <td className="py-0.5 pr-4 font-mono text-green-300">LDP</td>
                  <td className="py-0.5 text-lime-400">+22</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.2</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">Premium Context</td>
                  <td className="py-0.5 pr-4 font-mono text-green-300">LRP</td>
                  <td className="py-0.5 text-lime-400">+26</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.2</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var text-xs italic">both LDP + LRP</td>
                  <td className="py-0.5 pr-4 font-mono text-md-on-surface-var">LDP+LRP</td>
                  <td className="py-0.5 text-orange-400">capped at 35</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.3</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">Load Context <span className="text-md-on-surface-var">(max of)</span></td>
                  <td className="py-0.5 pr-4 font-mono text-cyan-300">LD</td>
                  <td className="py-0.5 text-lime-400">+8</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.3</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">Load Context</td>
                  <td className="py-0.5 pr-4 font-mono text-cyan-300">LDS</td>
                  <td className="py-0.5 text-lime-400">+11</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.3</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">Load Context</td>
                  <td className="py-0.5 pr-4 font-mono text-teal-300">LDC</td>
                  <td className="py-0.5 text-lime-400">+16</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.4</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">L-Reclaim <span className="text-md-on-surface-var">(skipped if LRP)</span></td>
                  <td className="py-0.5 pr-4 font-mono text-cyan-300">LRC</td>
                  <td className="py-0.5 text-lime-400">+12</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.5</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">Compression</td>
                  <td className="py-0.5 pr-4 font-mono text-slate-300">WRC</td>
                  <td className="py-0.5 text-lime-400">+10</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.5</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">Compression</td>
                  <td className="py-0.5 pr-4 font-mono text-slate-300">F8C</td>
                  <td className="py-0.5 text-lime-400">+12</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.5</td>
                  <td className="py-0.5 pr-4 text-md-on-surface-var text-xs italic">both WRC + F8C</td>
                  <td className="py-0.5 pr-4 font-mono text-md-on-surface-var">WRC+F8C</td>
                  <td className="py-0.5 text-orange-400">capped at 18</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.6</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">SQ/BCT (BCT supersedes SQB)</td>
                  <td className="py-0.5 pr-4 font-mono text-blue-300">BCT</td>
                  <td className="py-0.5 text-lime-400">+18 (+6 if SVS)</td>
                </tr>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface-var/70">7.6</td>
                  <td className="py-0.5 pr-4 text-md-on-surface">SQ/BCT</td>
                  <td className="py-0.5 pr-4 font-mono text-blue-300">SQB</td>
                  <td className="py-0.5 text-lime-400">+14 (+6 if SVS)</td>
                </tr>
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">7.7 — Base Setup Family Score</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Token</th>
                  <th className="text-left py-1 pr-4">Meaning</th>
                  <th className="text-left py-1">Points</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['A',   'AKAN setup',       '+10'],
                  ['SM',  'SMX setup',         '+10'],
                  ['N',   'NNN setup',          '+4'],
                  ['MX',  'MX setup',           '+5'],
                  ['A+SM+N+MX', 'all four active — full combo bonus', '+12 extra'],
                  ['(A|SM)+(N|MX)', 'partial combo', '+8 extra'],
                ].map(([tok, desc, pts]) => (
                  <tr key={tok} className="border-b border-md-outline-var/50">
                    <td className="py-0.5 pr-4 font-mono text-violet-300">{tok}</td>
                    <td className="py-0.5 pr-4 text-md-on-surface-var">{desc}</td>
                    <td className="py-0.5 text-lime-400">{pts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-md-on-surface-var text-xs mt-1">
            When a GOG tier is active, base setup total is capped at 8 (bonus points are not wasted — they still count within cap).
          </p>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">7.8 — Raw Support Score (hard cap: 25 pts)</p>
          <p className="text-md-on-surface-var text-xs mb-1">Individual signal booleans from the current bar. Highest-value signals shown:</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Signal</th>
                  <th className="text-left py-1 pr-4">Key</th>
                  <th className="text-left py-1">Pts</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['VBO↑',    'raw_vbo_up',    8],
                  ['L88',     'raw_l88',       8],
                  ['BUY HERE','raw_buy_here',  8],
                  ['ROCKET',  'raw_rocket',    8],
                  ['ATR Break','raw_atr_brk',  7],
                  ['BB Break', 'raw_bb_brk',   7],
                  ['260308',  'raw_sig260308', 7],
                  ['3G Gap',  'raw_three_g',   6],
                  ['LD Load', 'raw_load',      6],
                  ['BE↑',     'raw_be_up',     6],
                  ['F6',      'raw_f6',        6],
                  ['BO↑',     'raw_bo_up',     5],
                  ['SQ',      'raw_sq',        5],
                  ['F8',      'raw_f8',        5],
                  ['L22',     'raw_l22',       5],
                  ['SVS raw', 'raw_svs_raw',   5],
                  ['4BF',     'raw_bf4',       5],
                  ['F3',      'raw_f3',        5],
                  ['UM',      'raw_um',        4],
                  ['L34',     'raw_l34',       4],
                  ['BX↑',     'raw_bx_up',     4],
                  ['HILO↑',   'raw_hilo_buy',  4],
                  ['F4/F11',  'raw_f4/f11',    4],
                  ['Z10/Z11', 'raw_z10/z11',   4],
                  ['L43',     'raw_l43',       3],
                  ['Z12/Z4/Z6/Z9', 'raw_z…',  3],
                  ['W (wick)', 'raw_w',         3],
                  ['RTV',     'raw_rtv',       3],
                  ['T10/T11/T12','raw_t1x',    2],
                ].map(([sig, key, pts]) => (
                  <tr key={sig} className="border-b border-md-outline-var/50">
                    <td className="py-0.5 pr-4 font-mono text-md-on-surface">{sig}</td>
                    <td className="py-0.5 pr-4 text-md-on-surface-var/70 font-mono text-[10px]">{key}</td>
                    <td className="py-0.5 text-lime-400">+{pts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">7.9 — Research Forward Score (future data — CSV only)</p>
          <p className="text-md-on-surface-var text-xs mb-1">
            Added only in CSV exports, not in live scoring. Uses look-ahead data unavailable in real-time.
          </p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Condition</th>
                  <th className="text-left py-1">Pts</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['VBO fires within next 5 bars', '+10'],
                  ['VBO fires within next 6–10 bars', '+6'],
                  ['GOG fires within next 5 bars', '+12'],
                  ['GOG fires within next 6–10 bars', '+8'],
                ].map(([cond, pts]) => (
                  <tr key={cond} className="border-b border-md-outline-var/50">
                    <td className="py-0.5 pr-4 text-md-on-surface">{cond}</td>
                    <td className="py-0.5 text-sky-400">{pts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">7.10 — Risk Penalty</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse mt-1">
              <tbody>
                <tr className="border-b border-md-outline-var/50">
                  <td className="py-0.5 pr-4 text-md-on-surface">ALREADY_EXTENDED flag</td>
                  <td className="py-0.5 text-red-400">−15</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="text-md-on-surface-var text-xs mt-1">
            Buckets: the SIGNAL_SCORE bucket appends <span className="font-mono">_EXTENDED</span> when
            already_extended=1 and score ≥ 80 (e.g. <span className="font-mono text-orange-400">A_EXTENDED</span>).
          </p>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">Score Buckets</p>
          <div className="flex flex-wrap gap-2 text-xs mt-1">
            {[
              ['ELITE',       '≥ 120', 'bg-yellow-700 text-yellow-100 ring-1 ring-yellow-400'],
              ['A_PLUS',      '≥ 100', 'bg-lime-800 text-lime-100 ring-1 ring-lime-400'],
              ['A',           '≥ 80',  'bg-green-900 text-green-200'],
              ['B',           '≥ 60',  'bg-teal-900 text-teal-300'],
              ['WATCH',       '≥ 40',  'bg-md-surface-high text-md-on-surface'],
              ['LOW_WATCH',   '≥ 20',  'bg-md-surface-high text-md-on-surface-var'],
              ['NO_SIGNAL',   '< 20',  'bg-md-surface-con text-md-on-surface-var/70'],
            ].map(([bucket, range, cls]) => (
              <div key={bucket} className={`rounded px-2 py-1 font-mono font-semibold ${cls}`}>
                {bucket} <span className="font-normal opacity-70">{range}</span>
              </div>
            ))}
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">7.11 — Final Formula</p>
          <div className="bg-md-surface-high/60 rounded p-2 text-xs font-mono text-md-on-surface">
            SIGNAL_SCORE = GOG_BASE + PREMIUM_CTX + LOAD_CTX + L_RECLAIM<br/>
            {'             '}+ COMPRESSION + SQ_BCT + BASE_SETUP + RAW_SUPPORT(cap 25)<br/>
            {'             '}− RISK_PENALTY &nbsp; &nbsp; → clamped 0–160<br/>
            <br/>
            RESEARCH_SCORE = SIGNAL_SCORE + RESEARCH_FORWARD (CSV only)
          </div>
        </Section>

        {/* ── RTB SCORE ── */}
        <Section title="📈 RTB SCORE — Reversal-To-Breakout Phase Score">
          <p className="mb-2">
            A sequential phase model that tracks where a ticker sits in the journey from
            dead base / downtrend → accumulation → first reversal → breakout-ready state.
            Each phase contributes a sub-score; the phase is determined bar-by-bar with hysteresis.
          </p>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">Phases</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Phase</th>
                  <th className="text-left py-1 pr-4">Name</th>
                  <th className="text-left py-1 pr-4">What it captures</th>
                  <th className="text-left py-1">Score field (cap)</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['0',  'No signal',  'No structure detected yet',                                 '—'],
                  ['A',  'BUILD',      'Dead base / quiet accumulation — CONSO, L64/L22, W-dot, NS, LD, SQ/dSPR', 'rtb_build (12)'],
                  ['B',  'TURN',       'First reversal candle — L34/FRI34 reclaim, TZ→3, dSPR, CLM, L88, complex B-rules', 'rtb_turn (14)'],
                  ['C',  'READY',      'Pre-breakout readiness — SVS/UM, EMA/BB proximity, breakout candle cluster', 'rtb_ready (12)'],
                  ['D',  'LATE',       'Post-breakout — breakout signals have already fired; penalised', 'rtb_late (−12)'],
                ].map(([ph, name, desc, field]) => (
                  <tr key={ph} className="border-b border-md-outline-var/50">
                    <td className="py-0.5 pr-4 font-mono font-bold text-blue-300">{ph}</td>
                    <td className="py-0.5 pr-4 font-semibold text-md-on-surface">{name}</td>
                    <td className="py-0.5 pr-4 text-md-on-surface-var text-[11px]">{desc}</td>
                    <td className="py-0.5 font-mono text-lime-400 text-[11px]">{field}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">Score Components</p>
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse w-full mt-1">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Field</th>
                  <th className="text-left py-1 pr-4">Description</th>
                  <th className="text-left py-1">Cap</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['rtb_build',  'A-phase: base structure + dryness + Wyckoff context', 12],
                  ['rtb_turn',   'B-phase: reversal candle + reclaim level + flow signal', 14],
                  ['rtb_ready',  'C-phase: breakout triggers approaching or present',  12],
                  ['rtb_bonus3', '3-bar context: recent volume/signal cluster bonus',    8],
                  ['rtb_late',   'D-phase penalty: breakout already in progress',      -12],
                ].map(([field, desc, cap]) => (
                  <tr key={field} className="border-b border-md-outline-var/50">
                    <td className={`py-0.5 pr-4 font-mono ${cap < 0 ? 'text-red-400' : 'text-lime-300'}`}>{field}</td>
                    <td className="py-0.5 pr-4 text-md-on-surface-var">{desc}</td>
                    <td className={`py-0.5 font-semibold ${cap < 0 ? 'text-red-400' : 'text-md-on-surface'}`}>{cap > 0 ? `+${cap}` : cap}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="bg-md-surface-high/60 rounded p-2 text-xs font-mono text-md-on-surface mt-2">
            rtb_total = max(0, rtb_build + rtb_turn + rtb_ready + rtb_bonus3 − rtb_late)
          </div>

          <p className="text-md-on-surface-var mt-4 mb-1 font-medium">Phase Transitions</p>
          <div className="flex flex-wrap gap-2 text-xs mt-1">
            {[
              'A_START', 'A_HOLD', 'A_TO_B', 'B_HOLD', 'B_TO_C', 'C_HOLD', 'C_TO_D', 'RESET_HARD', 'RESET_SOFT'
            ].map(t => (
              <span key={t} className="bg-md-surface-high text-md-on-surface font-mono px-2 py-0.5 rounded">{t}</span>
            ))}
          </div>
          <p className="text-md-on-surface-var text-xs mt-2">
            Phase progression requires hysteresis confirmation — the new phase must score higher than
            the previous phase for at least 1 bar before a transition is committed.
            Hard reset on strong distribution signals; soft reset on extended runs without new evidence.
          </p>
        </Section>

        {/* ── Superchart ── */}
        <Section title="📊 Superchart — Signal Matrix + Statistics">
          <p className="mb-2">
            The Superchart shows a horizontal scrollable matrix: each <Tag cls="text-blue-300">column = one bar (date)</Tag>,
            each <Tag cls="text-blue-300">row = one signal group</Tag>. Every signal engine's output
            for the selected ticker and timeframe is visible at a glance — useful for studying
            confluences and reviewing historical setups.
          </p>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">Signal Rows</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="Z"    cls="text-red-300">Z-signals (T/Z bearish) + PREUP labels (P2, P3…)</Row>
            <Row label="T"    cls="text-green-300">T-signals (T/Z bullish — T4, T6, T1G, T2G…)</Row>
            <Row label="L"    cls="text-blue-300">WLNBB: L34, FRI34, BL, BLUE, CCI, BO↑, BX↑, BE…</Row>
            <Row label="F"    cls="text-orange-300">F-Builder signals (F1–F11, 260418)</Row>
            <Row label="FLY"  cls="text-purple-300">FLY ABCD pattern signals (260424)</Row>
            <Row label="G"    cls="text-violet-300">G-Builder signals (G1, G2, G4, G6, G11, 260410)</Row>
            <Row label="B"    cls="text-amber-300">B-Builder signals (B1–B11, 260321)</Row>
            <Row label="I"    cls="text-teal-300">2809 Phase labels (CONSO, UM, SVS, HILO↑, ROCKET, 3G…)</Row>
            <Row label="ULT"  cls="text-yellow-300">Ultra v2: 4BF, EB↑, FBO↑, 3↑, L88, 260308</Row>
            <Row label="VOL"  cls="text-pink-300">VABS volume signals: VBO↑, NS, ND, STRONG, ABS, LOAD, CLM</Row>
            <Row label="VABS" cls="text-lime-300">VABS pattern: BEST★, STRONG, ABS, CLM, VBO↑, SQ, BC, NS</Row>
            <Row label="WICK" cls="text-sky-300">Wick X confirmation signals: WP↑, WC↑, X2G, X2, X1G</Row>
            <Row label="turbo" cls="text-lime-300">Composite turbo score (0–100) for that bar</Row>
            <Row label="SCORE" cls="text-yellow-300">SIGNAL_SCORE (0–160) — shown only when ≥ 20; color-coded by bucket (yellow=ELITE, lime=A_PLUS…)</Row>
            <Row label="close" cls="text-md-on-surface">Closing price (green = up, red = down vs previous bar)</Row>
          </ul>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">📊 Stats Button — Signal Performance</p>
          <p className="mb-1">
            Clicking <Tag cls="text-violet-300">📊 Stats</Tag> fetches 2 years of history, runs
            all engines, and computes forward performance for every signal. Results are shown as
            a ranked table (default sort: <Tag cls="text-lime-300">max5</Tag> — highest average
            gain). Auto-refreshes when ticker or TF changes.
          </p>
          <div className="overflow-x-auto mt-1">
            <table className="text-xs border-collapse">
              <thead>
                <tr className="text-md-on-surface-var border-b border-md-outline-var">
                  <th className="text-left py-1 pr-4">Column</th>
                  <th className="text-left py-1">Meaning</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['N',      'number of times signal fired in the 2-year window'],
                  ['Bull%',  '% of fires where the next bar closed higher'],
                  ['+1bar',  "avg % return on the very next bar's close"],
                  ['max3',   'avg max-high reached within the next 3 bars (best exit opportunity)'],
                  ['max5',   'avg max-high reached within the next 5 bars — primary ranking metric'],
                  ['DD3',    'avg max drawdown (lowest low) over the next 3 bars — risk side'],
                  ['False%', '% of fires with zero gain over next 3 bars — false signal rate'],
                ].map(([col, desc]) => (
                  <tr key={col} className="border-b border-md-outline-var/40">
                    <td className="py-0.5 pr-4 font-mono text-violet-300">{col}</td>
                    <td className="py-0.5 text-md-on-surface-var">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-md-on-surface-var text-xs">
            Click any column header to re-sort. Signals with fewer than 3 historical occurrences
            are hidden. The stats use only the selected ticker's own price history — not pooled SP500 data.
          </p>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">⬇ CSV Export</p>
          <p>
            Clicking <Tag cls="text-md-on-surface">⬇ CSV</Tag> downloads the entire visible matrix as
            a spreadsheet-ready file named <Tag>{`{TICKER}_{tf}_signals.csv`}</Tag>. Each row is one bar.
            Signal groups are space-joined within their cell so the file opens cleanly in Excel or
            Google Sheets for custom filtering and back-testing.
          </p>
          <ul className="list-disc list-inside mt-1 space-y-1 text-md-on-surface-var">
            <li>~100 columns: date, OHLCV, scores, GOG_TIER, CONTEXT, SETUP, signal groups (Z/T/L/F/FLY/G/B/Combo/ULT/VOL/VABS/WICK), RTB fields, SIGNAL_SCORE sub-components, and ~90 individual SIG_* boolean columns</li>
            <li>Multiple signals per cell are space-separated (e.g. <Tag cls="text-orange-300">"F3 F4 F7"</Tag>)</li>
            <li>SIG_* boolean columns enable combo analysis — filter rows where <Tag cls="text-lime-300">SIG_BEST=1</Tag> AND <Tag cls="text-cyan-300">SIG_L34=1</Tag> etc.</li>
          </ul>
        </Section>

        {/* ── T/Z Signals ── */}
        <Section title="T/Z Signals (signal_engine)">
          <p className="mb-2">
            Core candlestick pattern engine. Every bar gets exactly ONE signal (or NONE) based on
            open/close/high/low relationships vs the previous bar. A priority system prevents
            double-counting.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="T4 / T6" cls="text-lime-300">bullish engulfing patterns — strongest reversal signals</Row>
            <Row label="T1G / T2G" cls="text-lime-300">golden variants — gap-up reversals and continuation</Row>
            <Row label="T1 / T2">classic bullish setups</Row>
            <Row label="T9 / T10">inside-bar bullish setups</Row>
            <Row label="T3 / T11 / T5">moderate or early-stage bullish reads</Row>
            <Row label="Z4 / Z6" cls="text-red-400">bearish engulfing — strongest reversal</Row>
            <Row label="Z1G / Z2G" cls="text-red-400">bearish golden variants</Row>
            <Row label="Z7" cls="text-md-on-surface-var">doji — indecision, only when no T or Z fires</Row>
          </ul>
        </Section>

        {/* ── B Signals ── */}
        <Section title="B1–B11 Signals (260321_B_BUILDER)">
          <p className="mb-2">
            Multi-bar pattern sequences built on top of T/Z codes. Each B signal describes a
            specific 2–3 bar sequence (e.g. "Z10 two bars ago, Z2G one bar ago, T1 now") that
            historically precedes reversals. <span className="text-yellow-300">No RSI filter applied</span> — raw pattern logic only.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="B1" cls="text-orange-400">T3→T4 sequences and Z2G/Z6 absorption into T4</Row>
            <Row label="B2" cls="text-md-on-surface">Z2-led pullback into T2/T2G continuation</Row>
            <Row label="B3" cls="text-sky-300">T6/T3 momentum with Z3/Z4 absorption sequences</Row>
            <Row label="B4" cls="text-md-on-surface">Z4 trap into T1G / T2G recovery</Row>
            <Row label="B5" cls="text-cyan-400">T5 pivot into T2G/T6 — classic spring</Row>
            <Row label="B6" cls="text-md-on-surface">Z1G/Z3 multi-bar base into T1G/T9</Row>
            <Row label="B7" cls="text-green-400">T9/T2 momentum stacking into T2G/T4</Row>
            <Row label="B8" cls="text-blue-400">repeated T1G or Z2G→T1G recovery</Row>
            <Row label="B9" cls="text-md-on-surface">Z9 trap / Z10 sequence into T4</Row>
            <Row label="B10" cls="text-lime-400">Z10 multi-bar base with Z2G→T1/T6 breakout</Row>
            <Row label="B11" cls="text-fuchsia-400">Z11/Z9/Z6 into Z10 base then reversal</Row>
          </ul>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">TZ State Machine — regime filter</p>
          <p className="mb-2 text-md-on-surface-var text-xs">
            Before surfacing B signals, the engine computes the current market regime using rolling
            dominance scores (T/Z pattern weights over 3 and 7 bars).
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="State 3: Bull Dom" cls="text-lime-300">bull patterns dominate both fast+slow windows, price above EMA20+50 + swing high</Row>
            <Row label="State 2: Bull Att" cls="text-cyan-300">bull patterns dominate fast window, price above EMA20</Row>
            <Row label="State 1: Bear Weak" cls="text-yellow-300">bears dominated slow window but bulls picking up in fast window</Row>
            <Row label="State 4: Bull Weak" cls="text-yellow-400">bulls dominated slow but bears picking up</Row>
            <Row label="State 5: Bear Att" cls="text-orange-400">bear patterns dominate fast window</Row>
            <Row label="State 0: Bear Dom" cls="text-red-400">bear patterns dominate both windows, price below EMA20+50</Row>
          </ul>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">Confluence signals (B × State)</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="CD" cls="text-lime-300">any B signal in Bull Dominance state — highest conviction</Row>
            <Row label="CA" cls="text-cyan-300">any B signal in Bull Attempt state</Row>
            <Row label="CW" cls="text-yellow-300">any B signal in Bear Weakening state — early reversal</Row>
          </ul>
        </Section>

        {/* ── G Signals ── */}
        <Section title="G1 / G2 / G4 / G6 / G11 Signals (260410_G_BUILDER)">
          <p className="mb-2">
            Stateful setup signals that require a bearish trigger bar first, then fire on the
            first matching bullish confirmation. <span className="text-yellow-300">No RSI filter.</span>
          </p>
          <p className="text-md-on-surface-var mb-1 font-medium">G1 / G2 / G4 / G6 — armed by Z10, Z11 or Z12</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="G1" cls="text-lime-300">first T1 after Z10 / Z11 / Z12 — inside-bar base then reversal</Row>
            <Row label="G2" cls="text-cyan-300">first T1G after Z10 / Z11 / Z12 — gap-up reversal after bearish inside bar</Row>
            <Row label="G4" cls="text-fuchsia-300">first T4 after Z10 / Z11 / Z12 — engulfing reversal after base</Row>
            <Row label="G6" cls="text-orange-300">first T6 after Z10 / Z11 / Z12 — bull-on-bull engulfing after base</Row>
          </ul>
          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">G11 — armed by T10 or T11</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="G11" cls="text-yellow-300">first T1 after T10 or T11 — reversal after bullish inside bars</Row>
          </ul>
          <p className="mt-2 text-md-on-surface-var text-xs">
            State resets after the G signal fires — each new Z10/Z11/Z12 (or T10/T11 for G11) starts a fresh setup.
          </p>
        </Section>

        {/* ── VA + SBC + TZ Transitions ── */}
        <Section title="VA / SBC / TZ Transitions (260402_COMBO_OSC + 260412_TZ_SHIFT)">
          <p className="mb-2">
            Two additional engines contribute new signals that are not part of the core T/Z or B/G families.
          </p>

          <p className="text-md-on-surface-var mb-1 font-medium">VA — ATR Volume Confirm (260402_COMBO_OSC)</p>
          <p className="mb-2 text-md-on-surface-var text-xs">
            Fires when the current bar's volume ratio (volume ÷ 20-bar avg) crosses above 2.0 — i.e.,
            the bar's volume is the first to exceed twice the recent average. Uses a Pine-style
            <span className="font-mono"> ta.crossover</span> condition.
          </p>
          <ul className="list-disc list-inside space-y-1 mb-3">
            <Row label="VA" cls="text-lime-300">vol/avg_vol just crossed above 2× — sudden volume surge</Row>
          </ul>

          <p className="text-md-on-surface-var mb-1 font-medium">SBC — seqBContLite (260412_TZ_SHIFT)</p>
          <p className="mb-2 text-md-on-surface-var text-xs">
            Continuation-lite sequences using T/Z priority codes. Three pattern families:
          </p>
          <ul className="list-disc list-inside space-y-1 mb-3">
            <Row label="SBC" cls="text-violet-300">T1/T1G/T2/T2G/T9 two bars ago → T4 now; or T3/T11/T5 one bar ago → T4/T6; or T4/T2G/T3 one bar ago → T6</Row>
          </ul>

          <p className="text-md-on-surface-var mb-1 font-medium">TZ Transition Signals (260412_TZ_SHIFT)</p>
          <p className="mb-2 text-md-on-surface-var text-xs">
            Detects the bar where the TZ state machine transitions into a new regime. Fires only on
            the first bar of that state — unlike tz_state which persists.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="TZ→3" cls="text-lime-300">TZ state just flipped to Bull Dominance (state 3 ≠ prev state)</Row>
            <Row label="TZ→2" cls="text-cyan-300">TZ state just entered Bull Attempt / Reversal (state 2 ≠ prev state)</Row>
          </ul>
        </Section>

        {/* ── Delta Engine ── */}
        <Section title="Delta V2 Engine (260403 — delta_engine)">
          <p className="mb-2">
            Order-flow proxy using Open-Adjusted CLV (Close Location Value). Separates each bar
            into wick and body components relative to the open price to estimate buy vs sell volume.
          </p>
          <div className="bg-md-surface-high/50 rounded p-2 text-xs font-mono mb-3 text-md-on-surface">
            <div>body_top  = max(open, close)</div>
            <div>body_bot  = min(open, close)</div>
            <div>bull_body = body_size  if close ≥ open  else 0</div>
            <div>buy_vol   = volume × (lower_wick + bull_body) / range</div>
            <div>delta     = buy_vol − sell_vol</div>
          </div>
          <ul className="list-disc list-inside space-y-1">
            <Row label="Strong Bull/Bear" cls="text-lime-300">delta strongly positive/negative relative to volume</Row>
            <Row label="Absorb Bull/Bear">buy/sell volume absorption — large opposing volume absorbed</Row>
            <Row label="Div Bull/Bear">delta divergence — price makes new extreme but delta doesn't</Row>
            <Row label="CD Bull/Bear">cumulative delta trend</Row>
            <Row label="Surge/Blast Bull">escalating delta intensity</Row>
            <Row label="vd_div (NS)" cls="text-teal-300">volume↓ + delta↑ on same bar — No Supply signal</Row>
            <Row label="Spring" cls="text-lime-300">Wyckoff Spring — divergence + absorption together (bear trap)</Row>
            <Row label="Upthrust" cls="text-red-400">Wyckoff Upthrust — bull trap version of spring</Row>
          </ul>
          <p className="mt-2 text-md-on-surface-var text-xs">
            Note: delta is computed from OHLCV only (no bid/ask). Accuracy ±15-25% vs real order flow.
            Wick-heavy bars benefit most from V2 vs V1.
          </p>
        </Section>

        {/* ── PREUP / PREDN ── */}
        <Section title="PREUP / PREDN — EMA Cross Signals (260331_TZ_OSC)">
          <p className="mb-2">
            Fires on the bar where price crosses an EMA: bar opens on one side and closes on the
            other. Priority chain ensures only the strongest EMA crossed is labelled.
          </p>
          <p className="text-md-on-surface-var mb-1 font-medium">PREUP (EMA cross ↑)</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="P66" cls="text-lime-300">crossed EMA200 + at least one smaller EMA — very strong</Row>
            <Row label="P55" cls="text-emerald-300">crossed EMA89 + another EMA</Row>
            <Row label="P89" cls="text-teal-300">crossed EMA89 alone</Row>
            <Row label="P3 / P2 / P50">crossed EMA9+20+50 / EMA9+20 / EMA50</Row>
          </ul>
          <p className="text-md-on-surface-var mt-2 mb-1 font-medium">PREDN (EMA drop ↓)</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="D66" cls="text-red-300">dropped below EMA200 + another EMA</Row>
            <Row label="D55" cls="text-red-400">dropped below EMA89 + another</Row>
            <Row label="D89 / D3 / D2 / D50" cls="text-orange-400">progressively weaker EMA breaks</Row>
          </ul>
        </Section>

        {/* ── 2809 Phase Labels ── */}
        <Section title="2809 Phase Labels (260402_COMBO_OSC)">
          <p className="mb-2">
            Three phase markers from the 2809 setup cycle:
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="UM" cls="text-teal-300">Upmove — first bar where ROC(40) ≥ 8% with EMA trend and volume spike</Row>
            <Row label="SVS" cls="text-orange-300">Strong Volume Spike — volume ratio crosses 1.4× avg on a green bar</Row>
            <Row label="CON" cls="text-yellow-300">Consolidation — first bar where ATR/range/EMA-gap tightens enough</Row>
            <Row label="BUY" cls="text-lime-300">Buy Here — upmove + consolidation + breakout + cooldown all met simultaneously</Row>
          </ul>
        </Section>

        {/* ── RS / RS+ ── */}
        <Section title="RS / RS+ — Relative Strength">
          <p className="mb-2">
            Identifies tickers that are outperforming the broad market on a given bar.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="RS" cls="text-green-400">ticker up ≥ 0.5% while SPY AND IWM are both down ≥ 0.3% on the same bar</Row>
            <Row label="RS+" cls="text-lime-300">RS condition AND ticker is in the high-volume bucket (B or VB)</Row>
          </ul>
        </Section>

        {/* ── Wyckoff Accumulation / Distribution ── */}
        <Section title="Wyckoff Accumulation & Distribution (260225 — wyckoff_engine)">
          <p className="mb-2">
            Two independent state machines that track where a ticker sits in the classic Wyckoff cycle.
            Each carries forward bar-by-bar; cycles reset after completion or after{' '}
            <Tag>cycleMaxBars=160</Tag> bars without completing.
          </p>

          <p className="text-md-on-surface-var mt-2 mb-1 font-medium">Accumulation state machine (bullish)</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="wSC" cls="text-orange-300">
              Selling Climax — wide-spread down bar, high volume ({'>'}1.8× avg), close near low,
              EMA14 below EMA50. Start of base.
            </Row>
            <Row label="AR" cls="text-yellow-300">
              Automatic Rally — first up bar closing above SC high after the climax.
            </Row>
            <Row label="ST" cls="text-amber-300">
              Secondary Test — pullback into SC zone on lower volume; holds above SC low.
            </Row>
            <Row label="SPR" cls="text-lime-300">
              Spring — brief dip below SC low that recovers immediately. Classic bear trap.
              Scored <Tag cls="text-lime-300">+7</Tag> (highest atomic signal in Vol/Accum family).
            </Row>
            <Row label="SOS" cls="text-lime-400">
              Sign of Strength / JAC — strong close above AR high on rising volume. Breakout.
              Scored <Tag cls="text-lime-300">+5</Tag>.
            </Row>
            <Row label="LPS" cls="text-green-300">
              Last Point of Support — minor dip after SOS on low volume; holds support.
              Buy-the-dip confirmation. Scored <Tag cls="text-lime-300">+5</Tag>.
            </Row>
            <Row label="ACC" cls="text-cyan-300">Accumulation active (states 1–4, pre-breakout). Context +2.</Row>
            <Row label="MKP" cls="text-lime-300">Markup phase (states 5–6, post-SOS). Context +3.</Row>
          </ul>

          <p className="text-md-on-surface-var mt-3 mb-1 font-medium">Distribution state machine (bearish)</p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="wBC" cls="text-red-300">
              Buying Climax — wide-spread up bar, high volume, close near high, EMA14 above EMA50. Potential top.
            </Row>
            <Row label="ARD" cls="text-pink-300">Automatic Reaction — sharp decline below BC low.</Row>
            <Row label="STD" cls="text-rose-300">Secondary Test (dist) — rally back toward BC zone, lower volume, stalls.</Row>
            <Row label="UTAD" cls="text-orange-400">
              Upthrust After Distribution — brief push above BC high that closes back below. Bull trap.
            </Row>
            <Row label="SOW" cls="text-red-400">
              Sign of Weakness — strong close below ARD low. Breakdown.
              Reduces bull score by <Tag cls="text-red-400">−4</Tag>.
            </Row>
            <Row label="LPSY" cls="text-rose-400">Last Point of Supply — minor rally on low volume that fails.</Row>
            <Row label="DST" cls="text-red-400">Distribution active (states 1–4). Context −1.</Row>
            <Row label="MKD" cls="text-red-500">Markdown phase (states 5–6, post-SOW). Context −3.</Row>
          </ul>

          <p className="mt-2 text-md-on-surface-var text-xs">
            Parameters: cycleMaxBars=160, emaFast=14, emaSlow=50, hiVolMult=1.8, wideSpreadATR=1.2.
          </p>
        </Section>

        {/* ── VABS ── */}
        <Section title="VABS Engine (vabs_engine) — Volume Action & Wyckoff">
          <p className="mb-2">
            Detects high-conviction volume events. Combines raw volume analysis with Wyckoff
            price–volume theory to identify professional accumulation and markup phases.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="BEST★" cls="text-lime-300">highest-confidence long setup — multiple volume + structure conditions met</Row>
            <Row label="STRONG">strong accumulation signal, 1 condition below BEST</Row>
            <Row label="VBO↑">Volume Breakout — price breaks above range on high volume</Row>
            <Row label="ABS">Absorption — selling absorbed by strong buying</Row>
            <Row label="CLB">Climb — steady high-volume uptrend</Row>
            <Row label="LD">Load — volume loading / accumulation in progress</Row>
            <Row label="NS">No Supply — down bar on very low volume</Row>
            <Row label="SQ">Stopping Quantity — high volume spike reversal</Row>
            <Row label="SC">Selling Climax — exhaustion of sellers</Row>
            <Row label="ND">No Demand — weak up bar on low volume</Row>
          </ul>
        </Section>

        {/* ── ULTRA v2 ── */}
        <Section title="260315 ULTRA v2 (ultra_engine)">
          <ul className="list-disc list-inside space-y-1 mb-2">
            <Row label="EB↑" cls="text-amber-300">Extended Bar — big body, tiny wicks. Strong directional move.</Row>
            <Row label="FBO↑" cls="text-sky-300">Failed Breakout — broke below N-bar low then closed back above. Bear trap.</Row>
            <Row label="4BF" cls="text-pink-300">Four-Bar Fractal — close above both prior-bar high and 3-bars-ago high.</Row>
            <Row label="BEST↑" cls="text-yellow-300">FBO↑ + 4BF together — highest ULTRA signal.</Row>
            <Row label="3↑">Three Drives Up — effort → test → confirmation accumulation sequence.</Row>
          </ul>
        </Section>

        {/* ── 260308 + L88 ── */}
        <Section title="260308 + L88 (ultra_engine)">
          <ul className="list-disc list-inside space-y-1">
            <Row label="260308" cls="text-purple-300">
              Volume jump — bar volume ≥ 2× previous AND bullish candle AND delta ≥ 1.5× previous delta
            </Row>
            <Row label="L88" cls="text-violet-300">
              260308 signal occurring at an active L34/L43 level — institutional activity at a known support
            </Row>
          </ul>
        </Section>

        {/* ── WLNBB ── */}
        <Section title="WLNBB / L-Signals (wlnbb_engine)">
          <ul className="list-disc list-inside space-y-1">
            <Row label="L34 / L43" cls="text-blue-300">key support levels based on EMA crossover proximity</Row>
            <Row label="FRI34 / FRI43" cls="text-cyan-300">Friday close at L34/L43 — strongest weekly confirmation</Row>
            <Row label="L555">stateful multi-bar escalating L-signal (7-bar window)</Row>
            <Row label="L2L4">ONLY_L2 or ONLY_L4 — clean signal without higher-L interference</Row>
            <Row label="BO↑ / BX↑">breakout / breakout extension above L-level body high</Row>
            <Row label="BE↑ / BE↓">bar engulfs prior L-level body (candle opens below body, closes above)</Row>
            <Row label="BLUE">trend quality — price action in bullish structure</Row>
            <Row label="CCI">CCI about to cross signal line from below — early entry signal</Row>
          </ul>
        </Section>

        {/* ── BR / Wick / CISD ── */}
        <Section title="BR Score + Wick + CISD">
          <ul className="list-disc list-inside space-y-1">
            <Row label="BR 0–100">Breakout Readiness — consolidation tightness + volume build-up + trend + proximity to resistance</Row>
            <Row label="WK↑" cls="text-emerald-300">Long lower wick (≥ 2× body) on above-average volume — strong buyer rejection (legacy)</Row>
            <Row label="C+- / C+--">Change In State of Delivery — shift from distribution to accumulation</Row>
          </ul>
        </Section>

        {/* ── Wick X signals ── */}
        <Section title="260402_WICK — X2G / X2 / X1G / X1 / X3 (wick_engine)">
          <p className="mb-2">
            Wick-filtered reversal signals. Each checks the current bar's lower-wick dominance
            (lower wick ≥ 2× upper wick) combined with the wick shape of the prior bar(s).
            Four distinct patterns, ranked by strength:
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="X2G" cls="text-cyan-300">
              T2G continuation: prev bullish, current <em>gap-opens</em> above prev close
              and closes above prev close. Both bars have aligned wick shape.
              Scored <Tag cls="text-lime-300">+5</Tag>.
            </Row>
            <Row label="X2" cls="text-sky-300">
              T2 continuation: prev bullish, current opens <em>inside</em> prev body
              (at/below prev close) and closes above prev close. Wicks aligned.
              Scored <Tag cls="text-lime-300">+4</Tag>.
            </Row>
            <Row label="X1G" cls="text-lime-300">
              T1G reversal: prev bearish, current <em>gap-opens</em> above prev close and
              closes above prev open. Prev bar had dominant upper wick (exhaustion).
              Scored <Tag cls="text-lime-300">+4</Tag>.
            </Row>
            <Row label="X1" cls="text-green-300">
              T1 reversal: prev bearish, current opens at/above prev close (prev open ≥ curr open)
              and closes above prev open. Prev bar had dominant upper wick.
              Scored <Tag cls="text-lime-300">+3</Tag>.
            </Row>
            <Row label="X3" cls="text-yellow-300">
              Generic bullish wick alignment across 2–3 bars. Fires only when X2G/X2/X1G/X1 absent.
              Scored <Tag cls="text-lime-300">+2</Tag>.
            </Row>
          </ul>
          <p className="mt-2 text-md-on-surface-var text-xs">
            wickMult=2.0: dominant wick must be ≥ 2× the opposing wick to qualify.
          </p>
        </Section>

        {/* ── Signal Tier Guide ── */}
        <Section title="Signal Tier Guide — How to Read Setups">
          <p className="mb-3 text-md-on-surface-var">
            Not all signals are equal. The key principle: <span className="text-white font-semibold">cross-engine confluence &gt; same-engine cluster</span>.
            Multiple independent engines agreeing on direction is stronger than many signals from the same family.
            The TURBO score reason line shows <Tag cls="text-lime-300">⚡×N</Tag> where N = number of distinct engine families active.
          </p>

          {/* A-tier */}
          <div className="mb-4">
            <p className="text-white font-semibold mb-1">Tier A — Strong surfaced signals</p>
            <p className="text-md-on-surface-var text-xs mb-2">
              Already high-conviction on their own. Note: BEST★ and BUY/🚀 are themselves composite — they already passed multiple sub-conditions internally.
            </p>
            <div className="flex flex-wrap gap-2 text-xs">
              {[
                ['BEST★', 'text-lime-300',   'VABS composite — highest vol+structure conditions met'],
                ['wSPR',  'text-lime-300',   'Wyckoff Spring — Phase C reversal below support'],
                ['T4/T6', 'text-violet-300', 'Candlestick T4/T6 state — strongest T/Z pattern'],
                ['VBO↑',  'text-green-300',  'Volume Breakout Up — breakout confirmed by volume'],
                ['P66',   'text-lime-300',   'EMA66 cross ↑ — trend regime confirmed'],
                ['🚀/BUY','text-red-300',    'Rocket / Buy 2809 — multi-condition combo trigger'],
              ].map(([lbl, cls, desc]) => (
                <div key={lbl} className="bg-md-surface-high rounded px-2 py-1">
                  <span className={`font-mono font-semibold ${cls}`}>{lbl}</span>
                  <span className="text-md-on-surface-var ml-1">{desc}</span>
                </div>
              ))}
            </div>
          </div>

          {/* B-tier */}
          <div className="mb-4">
            <p className="text-white font-semibold mb-1">Tier B — Context amplifiers</p>
            <p className="text-md-on-surface-var text-xs mb-2">
              Rarely sufficient alone, but significantly strengthen a Tier A signal.
              These represent regime confirmation, structural re-test, or readiness context.
            </p>
            <div className="flex flex-wrap gap-2 text-xs">
              {[
                ['TZ→3', 'text-lime-300',   'T/Z just flipped bullish — fresh regime change'],
                ['LPS',  'text-green-300',  'Wyckoff Last Point of Support — structure re-test before markup'],
                ['FRI34','text-cyan-400',   'WLNBB weekly L-structure confirmation'],
                ['RS+',  'text-lime-300',   'Relative strength vs SPY+IWM — sector leadership'],
                ['BR%',  'text-lime-400',   'Break-readiness context score ≥ 71'],
                ['CA/CD','text-lime-300',   'T/Z confluence context signals'],
              ].map(([lbl, cls, desc]) => (
                <div key={lbl} className="bg-md-surface-high rounded px-2 py-1">
                  <span className={`font-mono font-semibold ${cls}`}>{lbl}</span>
                  <span className="text-md-on-surface-var ml-1">{desc}</span>
                </div>
              ))}
            </div>
          </div>

          {/* C-tier combos */}
          <div className="mb-4">
            <p className="text-white font-semibold mb-1">Tier C — Best cross-engine combos (with timing)</p>
            <p className="text-md-on-surface-var text-xs mb-2">
              These pairs/triples draw from genuinely different observation models — structure, state, flow, breakout.
              <Tag cls="text-sky-300"> [E]</Tag> = early phase (Phase C / fresh flip, best R/R, less confirmation).
              <Tag cls="text-orange-300"> [L]</Tag> = late/confirmed (strong conviction, possibly extended entry).
            </p>
            <table className="text-xs w-full max-w-2xl">
              <thead>
                <tr className="text-md-on-surface-var text-left border-b border-md-outline-var">
                  <th className="pb-1 pr-4">Combo</th>
                  <th className="pb-1 pr-4">Engines</th>
                  <th className="pb-1 pr-4">Phase</th>
                  <th className="pb-1">Why it works</th>
                </tr>
              </thead>
              <tbody className="space-y-1">
                {[
                  ['wSPR + VBO↑',         'Wyk + VABS',         'Early',   'Structure spring confirmed by volume breakout'],
                  ['T4/T6 + TZ→3',        'T/Z + T/Z regime',   'Early→Mid','Strongest T/Z pattern into fresh bullish flip'],
                  ['FRI34 + VBO↑ + RS+',  'L + VABS + Brk',     'Mid',     'Weekly structure + volume + sector leadership'],
                  ['wLPS + T4 + BR%',     'Wyk + T/Z + Context','Mid',     'Re-test confirmed by state + readiness context'],
                  ['wSPR + dSPR + Ab↑',  'Wyk + Δ + Δ',        'Early',   'Same theme cluster — high conviction, less diversity'],
                  ['B1/B10 + CD + VBO↑',  'B/G + Cmb + VABS',   'Late [L]','Confirmed breakout — conviction high, entry may be extended'],
                ].map(([combo, engines, phase, why]) => (
                  <tr key={combo} className="border-b border-md-outline-var/40">
                    <td className="py-1 pr-4 font-mono text-white">{combo}</td>
                    <td className="py-1 pr-4 text-md-on-surface-var">{engines}</td>
                    <td className={`py-1 pr-4 ${phase.includes('[L]') ? 'text-orange-400' : phase === 'Early' ? 'text-sky-300' : 'text-md-on-surface-var'}`}>{phase}</td>
                    <td className="py-1 text-md-on-surface-var">{why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Caution box */}
          <div className="bg-md-surface-high/50 border border-md-outline-var rounded p-3 text-xs text-md-on-surface-var mt-2">
            <span className="text-yellow-300 font-semibold">Caution:</span> The Corr panel shows co-occurrence frequency, not forward expectancy.
            High co-occurrence ≠ high win rate. Use Corr to <span className="text-white">discover</span> candidate combos,
            then validate with Predictor bull% and actual risk/reward.
            Missing dimension: drawdown and timing in the price cycle.
          </div>
        </Section>

        {/* ── FLY ABCD ── */}
        <Section title="FLY ABCD EMA DP (260424 — fly_engine)">
          <p className="mb-2">
            Sequence detector using T/Z role codes to find ABCD patterns, confirmed by an EMA
            drop-then-recovery context at the anchor bar. D must fire on the current bar.
          </p>
          <p className="text-md-on-surface-var mb-1 font-medium">Role assignments (by T/Z code)</p>
          <ul className="list-disc list-inside space-y-1 mb-3">
            <Row label="A (anchor)" cls="text-red-400">ZC {'{3,4}'} — Z1G, Z2G: strong bearish reversal bar</Row>
            <Row label="B (base)"   cls="text-orange-300">ZC {'{9,1,2,5,10,8,12,7}'} — various bearish codes: continuation or trap bar</Row>
            <Row label="C (coil)"   cls="text-cyan-300">BC {'{9,10,12,7,5}'} — T3,T11,T12,T9,T1: moderate bullish recovery bar</Row>
            <Row label="D (drive)"  cls="text-lime-300">BC {'{1,2,4,6}'} — T4,T6,T2G,T2: strong bullish breakout bar (fires on current bar)</Row>
          </ul>
          <p className="text-md-on-surface-var mb-1 font-medium">EMA context filter (checked at each anchor bar)</p>
          <p className="text-md-on-surface-var text-xs mb-2">
            Requires: an EMA drop (E1) followed by an EMA cross-up (E2), both within 30 bars of the
            anchor. E1 must be older than E2. This ensures the setup had a dip-and-recovery structure.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="ABCD" cls="text-lime-300">Full sequence A→B→C on current D, EMA seq at A, all within 30 bars</Row>
            <Row label="CD"   cls="text-cyan-300">C within 20 bars before D, EMA seq at C</Row>
            <Row label="BD"   cls="text-blue-300">B within 20 bars before D, EMA seq at B</Row>
            <Row label="AD"   cls="text-violet-300">A within 20 bars before D, EMA seq at A</Row>
          </ul>
        </Section>

        {/* ── RGTI + SMX ── */}
        <Section title="RGTI 260404 + SMX 260402 — Multi-TF EMA Alignment (rgti_engine)">
          <p className="mb-2">
            Multi-timeframe EMA alignment patterns. Requires 4H, 1H, and 15m data simultaneously.
            For daily/weekly scans, 1H is fetched and resampled to 4H count-based.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="LL"  cls="text-purple-300">Lower-Low: price above EMA50/20 on 4H but EMAs showing deceleration; 1H+15m aligned</Row>
            <Row label="UP"  cls="text-blue-300">Uptrend: 4H EMA stack bullish + EMA9 above EMA200; 1H/15m price/EMA confirmed</Row>
            <Row label="↑↑"  cls="text-fuchsia-300">UPUP: 4H+1H+15m all show aligned bull stack; 1H bullish candle</Row>
            <Row label="↑↑↑" cls="text-sky-300">UPUPUP: EMA200 driving above EMA50 across timeframes</Row>
            <Row label="ORG" cls="text-orange-300">Orange: bear EMA stack on 4H/1H but 15m crossing up — early reversal</Row>
            <Row label="GRN" cls="text-green-300">Green: 4H+1H EMA deeply stacked bull; 15m breakout candle</Row>
            <Row label="GC"  cls="text-emerald-300">Green Circle: 4H bull stack but price dipping toward EMA — pullback entry</Row>
            <Row label="SMX" cls="text-lime-300">SMX: 4H bear stack + 1H recovery + 15m reversal + near 20-bar low (high R/R entry)</Row>
          </ul>
        </Section>

        {/* ── PARA ── */}
        <Section title="PARA 260420 — Parabola Start Detector v3.6 (para_engine)">
          <p className="mb-2">
            Stateful campaign system that detects the start of parabolic moves following base compression.
            Four sequential signal types from setup → entry → retest.
          </p>
          <ul className="list-disc list-inside space-y-1 mb-2">
            <Row label="PREP"  cls="text-green-300">Pre-parabola zone: base compressed + EMA aligned + price below breakout level. First bar entering zone.</Row>
            <Row label="PARA"  cls="text-lime-300">Parabola Start: base compressed → seed bar (breakout + volume ignition + candle quality) → follow-through. Campaign opens.</Row>
            <Row label="PARA+" cls="text-cyan-300">PARA with dry volume or V+E (volume+EMA) coincidence — highest conviction breakout.</Row>
            <Row label="RETEST" cls="text-emerald-300">Pullback re-entry within open campaign: price dips near EMA, holds, bullish candle with volume. Up to 2 retests per campaign.</Row>
          </ul>
          <p className="text-md-on-surface-var text-xs">
            Campaign resets on: close below EMA50, consecutive closes below EMA20, or 18%+ drawdown from campaign high below EMA9.
            Daily chart uses relaxed parameters (wider base tolerance, more memory).
          </p>
        </Section>

        {/* ── Predictor ── */}
        <Section title="Predictor">
          <p className="mb-2">
            Pattern-match prediction engine. Looks at the last 2–3 signal bars (the "pattern")
            and finds every previous occurrence of the same sequence, then shows next-bar statistics.
          </p>
          <ul className="list-disc list-inside space-y-1">
            <Row label="This Ticker">per-ticker history (~5–20 matches for common patterns)</Row>
            <Row label="SP500 Pooled" cls="text-blue-300">cross-universe statistics built from ~500 tickers — thousands of pattern instances for high-confidence stats</Row>
            <Row label="Bull %" cls="text-green-400">% of matches where next bar was bullish</Row>
            <Row label="Avg gain">average next-bar move after the pattern</Row>
          </ul>
        </Section>

        {/* ── Data Flow ── */}
        <Section title="Data Flow">
          <ul className="list-disc list-inside space-y-1">
            <li>Primary: <Tag>Massive API</Tag> (Polygon-compatible) — set <Tag>MASSIVE_API_KEY</Tag> env var</li>
            <li>Fallback: <Tag>yfinance</Tag> — used automatically when Massive unavailable</li>
            <li>Scan results: <Tag>SQLite</Tag> at <Tag>/tmp/scanner.db</Tag> — last 3 runs per universe kept</li>
            <li>TURBO scan: <Tag>ThreadPoolExecutor</Tag> (8 workers), ~3–6 min per 2000-ticker universe</li>
            <li>Market-open check: uses <Tag>America/New_York</Tag> timezone — bars kept after 16:15 ET</li>
            <li>All user state: <Tag>localStorage</Tag> (watchlist, ticker, TF, active tab)</li>
          </ul>
        </Section>

      </div>
    </div>
  )
}
