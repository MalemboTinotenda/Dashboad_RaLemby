import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from supabase import create_client, Client
import numpy as np

# ─────────────────────────────────────────────
# 1. Page Config
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Live MT5 Lembo Analytics Dashboar",
    page_icon="📈",
    layout="wide"
)

st.title("📊 Trading Dashboard raLemby")
st.caption("Real-time performance tracking")

# ─────────────────────────────────────────────
# 2. Database Connection
# ─────────────────────────────────────────────
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@st.cache_data(ttl=30)
def load_cloud_data():
    try:
        response = supabase.table("trades").select("*").order("trade_time", desc=True).execute()
        if response.data:
            return pd.DataFrame(response.data)
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Failed to connect to Supabase Cloud Storage: {e}")
        return pd.DataFrame()

df_raw = load_cloud_data()

if df_raw.empty:
    st.info("⏳ Waiting for data... Run your local 'mt5_to_supabase.py' script to push data into your pipeline.")
    st.stop()

# ─────────────────────────────────────────────
# 3. Filter Engine
# Discount wins < $1.00, keep all losses
# ─────────────────────────────────────────────
df_all = df_raw[~((df_raw['profit'] >= 0) & (df_raw['profit'] < 1.00))].copy()
df_all['trade_time'] = pd.to_datetime(df_all['trade_time'])
df_all['trade_date'] = df_all['trade_time'].dt.date
df_all['trade_hour'] = df_all['trade_time'].dt.hour
df_all['Direction']  = df_all['symbol'].apply(lambda s: 'Short' if 'Boom' in str(s) else 'Long')

# ─────────────────────────────────────────────
# 3b. Date Range Slider
# ─────────────────────────────────────────────
min_date = df_all['trade_date'].min()
max_date = df_all['trade_date'].max()
all_dates = sorted(df_all['trade_date'].unique())

st.sidebar.markdown("## 📅 Date Range Filter")

if len(all_dates) > 1:
    start_idx, end_idx = st.sidebar.select_slider(
        "Select trading period",
        options=list(range(len(all_dates))),
        value=(0, len(all_dates) - 1),
        format_func=lambda i: str(all_dates[i])
    )
    selected_start = all_dates[start_idx]
    selected_end   = all_dates[end_idx]
else:
    selected_start = min_date
    selected_end   = max_date

st.sidebar.caption(f"Showing **{selected_start}** → **{selected_end}**")

# Quick preset buttons
st.sidebar.markdown("**Quick Presets**")
preset_cols = st.sidebar.columns(2)
if preset_cols[0].button("Last 7 days"):
    selected_start = max_date - pd.Timedelta(days=6)
    selected_end   = max_date
if preset_cols[1].button("All time"):
    selected_start = min_date
    selected_end   = max_date

# Instrument filter
st.sidebar.markdown("## 🎯 Instrument Filter")
all_symbols = sorted(df_all['symbol'].unique().tolist())
selected_symbols = st.sidebar.multiselect(
    "Select instruments",
    options=all_symbols,
    default=all_symbols
)

# Direction filter
st.sidebar.markdown("## ↕️ Direction Filter")
selected_directions = st.sidebar.multiselect(
    "Select directions",
    options=['Long', 'Short'],
    default=['Long', 'Short']
)

# Apply all filters
df = df_all[
    (df_all['trade_date'] >= selected_start) &
    (df_all['trade_date'] <= selected_end) &
    (df_all['symbol'].isin(selected_symbols)) &
    (df_all['Direction'].isin(selected_directions))
].copy()

if df.empty:
    st.warning("⚠️ No trades match the current filters. Adjust the date range or instrument selection.")
    st.stop()

trade_count_all = len(df_all)
trade_count_filtered = len(df)
st.caption(f"Showing **{trade_count_filtered}** of **{trade_count_all}** total trades · {selected_start} → {selected_end}")

# ─────────────────────────────────────────────
# 4. Core Calculations
# ─────────────────────────────────────────────
STARTING_BALANCE = 105.54
wins   = df[df['profit'] > 0]
losses = df[df['profit'] < 0]
be     = df[df['profit'] == 0]

total_trades  = len(df)
win_rate      = (len(wins) / total_trades * 100) if total_trades > 0 else 0
net_profit    = df['profit'].sum()
gross_profit  = wins['profit'].sum()
gross_loss    = losses['profit'].sum()
profit_factor = gross_profit / abs(gross_loss) if gross_loss != 0 else gross_profit

total_swap       = -0.35 if any(df['symbol'].str.contains('DEX', na=False)) else 0.00
account_balance  = STARTING_BALANCE + net_profit
current_equity   = account_balance + total_swap

# Running balance for drawdown calc
df_sorted = df.sort_values('trade_time')
df_sorted['cumulative_pnl'] = df_sorted['profit'].cumsum()
df_sorted['running_balance'] = STARTING_BALANCE + df_sorted['cumulative_pnl']
df_sorted['peak']            = df_sorted['running_balance'].cummax()
df_sorted['drawdown']        = df_sorted['running_balance'] - df_sorted['peak']
df_sorted['drawdown_pct']    = (df_sorted['drawdown'] / df_sorted['peak']) * 100

max_drawdown     = df_sorted['drawdown'].min()
max_drawdown_pct = df_sorted['drawdown_pct'].min()

# Per-trade stats
avg_win  = wins['profit'].mean()   if len(wins)   > 0 else 0
avg_loss = losses['profit'].mean() if len(losses) > 0 else 0
best_trade  = df['profit'].max()
worst_trade = df['profit'].min()
risk_reward = abs(avg_win / avg_loss) if avg_loss != 0 else 0

# Expectancy = (win_rate * avg_win) + (loss_rate * avg_loss)
loss_rate   = len(losses) / total_trades if total_trades > 0 else 0
expectancy  = (win_rate / 100 * avg_win) + (loss_rate * avg_loss)

# Streak calculation
streaks = []
current_streak = 0
streak_type = None
for p in df_sorted['profit']:
    t = 'W' if p > 0 else ('L' if p < 0 else 'BE')
    if t == streak_type:
        current_streak += 1
    else:
        if streak_type:
            streaks.append((streak_type, current_streak))
        streak_type    = t
        current_streak = 1
if streak_type:
    streaks.append((streak_type, current_streak))

win_streaks  = [s[1] for s in streaks if s[0] == 'W']
loss_streaks = [s[1] for s in streaks if s[0] == 'L']
max_win_streak  = max(win_streaks)  if win_streaks  else 0
max_loss_streak = max(loss_streaks) if loss_streaks else 0

# Daily P&L
daily = df_sorted.groupby('trade_date')['profit'].sum().reset_index()
daily.columns = ['Date', 'Daily P&L']
best_day  = daily['Daily P&L'].max()
worst_day = daily['Daily P&L'].min()
positive_days = (daily['Daily P&L'] > 0).sum()
negative_days = (daily['Daily P&L'] < 0).sum()

# Instrument breakdown
instrument_stats = df.groupby('symbol').agg(
    trades=('profit', 'count'),
    net_pnl=('profit', 'sum'),
    avg_pnl=('profit', 'mean'),
    win_count=('profit', lambda x: (x > 0).sum()),
).reset_index()
instrument_stats['win_rate'] = (instrument_stats['win_count'] / instrument_stats['trades'] * 100).round(1)
instrument_stats['pnl_per_lot'] = instrument_stats['net_pnl'] / instrument_stats['trades']

# Hour of day analysis
hour_stats = df.groupby('trade_hour')['profit'].agg(['sum', 'count', 'mean']).reset_index()
hour_stats.columns = ['Hour', 'Net P&L', 'Trades', 'Avg P&L']

# Direction stats
dir_stats = df.groupby('Direction').agg(
    trades=('profit', 'count'),
    net_pnl=('profit', 'sum'),
    wins=('profit', lambda x: (x > 0).sum()),
    avg_pnl=('profit', 'mean'),
).reset_index()
dir_stats['win_rate'] = (dir_stats['wins'] / dir_stats['trades'] * 100).round(1)

DARK = 'plotly_dark'
GREEN = '#10b981'
RED   = '#ef4444'
BLUE  = '#3b82f6'
AMBER = '#f59e0b'

# ─────────────────────────────────────────────
# 5. SECTION A — Core KPIs
# ─────────────────────────────────────────────
st.markdown("### Performance Overview")
c1, c2, c3, c4, c5 = st.columns(5)

c1.metric("Net P&L",
    f"+${net_profit:.2f}" if net_profit >= 0 else f"-${abs(net_profit):.2f}",
    f"{total_trades} closed trades")
c2.metric("Win Rate",
    f"{win_rate:.1f}%",
    f"{len(wins)}W · {len(losses)}L · {len(be)}BE")
c3.metric("Profit Factor",
    f"{profit_factor:.2f}×",
    f"${gross_profit:.2f} / ${abs(gross_loss):.2f}")
c4.metric("Account Balance",
    f"${account_balance:.2f}",
    f"Equity ${current_equity:.2f}")
c5.metric("Swap Charges",
    f"-${abs(total_swap):.2f}" if total_swap < 0 else f"${total_swap:.2f}",
    "DEX overnight")

st.markdown("---")

# ─────────────────────────────────────────────
# 6. SECTION B — Risk & Quality KPIs
# ─────────────────────────────────────────────
st.markdown("### Risk & Trade Quality")
r1, r2, r3, r4, r5, r6 = st.columns(6)

r1.metric("Max Drawdown",
    f"${max_drawdown:.2f}",
    f"{max_drawdown_pct:.1f}%")
r2.metric("Expectancy / Trade",
    f"+${expectancy:.2f}" if expectancy >= 0 else f"-${abs(expectancy):.2f}")
r3.metric("Avg Win",
    f"+${avg_win:.2f}")
r4.metric("Avg Loss",
    f"-${abs(avg_loss):.2f}")
r5.metric("Risk : Reward",
    f"1 : {risk_reward:.2f}")
r6.metric("Best / Worst Trade",
    f"+${best_trade:.2f}",
    f"Worst: -${abs(worst_trade):.2f}")

st.markdown("---")

# ─────────────────────────────────────────────
# 7. SECTION C — Consistency KPIs
# ─────────────────────────────────────────────
st.markdown("### Consistency Metrics")
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric("Max Win Streak",  f"{max_win_streak} trades")
k2.metric("Max Loss Streak", f"{max_loss_streak} trades")
k3.metric("Best Day",        f"+${best_day:.2f}")
k4.metric("Worst Day",       f"-${abs(worst_day):.2f}")
k5.metric("Positive Days",   f"{positive_days}",
    f"{negative_days} negative")

st.markdown("---")

# ─────────────────────────────────────────────
# 8. SECTION D — Charts (Row 1)
# ─────────────────────────────────────────────
st.markdown("### P&L Analysis")
col1, col2 = st.columns(2)

with col1:
    st.subheader("Running Balance")
    fig_bal = go.Figure()
    fig_bal.add_trace(go.Scatter(
        x=df_sorted['trade_time'],
        y=df_sorted['running_balance'],
        mode='lines+markers',
        line=dict(color=GREEN, width=2),
        fill='tozeroy',
        fillcolor='rgba(16,185,129,0.08)',
        name='Balance'
    ))
    fig_bal.update_layout(template=DARK, showlegend=False,
        yaxis_title="Balance ($)", xaxis_title="",
        margin=dict(t=10, b=10))
    st.plotly_chart(fig_bal, use_container_width=True)

with col2:
    st.subheader("Drawdown Curve")
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=df_sorted['trade_time'],
        y=df_sorted['drawdown'],
        mode='lines',
        line=dict(color=RED, width=2),
        fill='tozeroy',
        fillcolor='rgba(239,68,68,0.12)',
        name='Drawdown'
    ))
    fig_dd.update_layout(template=DARK, showlegend=False,
        yaxis_title="Drawdown ($)", xaxis_title="",
        margin=dict(t=10, b=10))
    st.plotly_chart(fig_dd, use_container_width=True)

# ─────────────────────────────────────────────
# 9. SECTION E — Charts (Row 2)
# ─────────────────────────────────────────────
col3, col4 = st.columns(2)

with col3:
    st.subheader("Daily P&L")
    daily['color'] = daily['Daily P&L'].apply(lambda x: GREEN if x >= 0 else RED)
    fig_daily = go.Figure(go.Bar(
        x=daily['Date'].astype(str),
        y=daily['Daily P&L'],
        marker_color=daily['color'],
    ))
    fig_daily.update_layout(template=DARK, showlegend=False,
        yaxis_title="P&L ($)", margin=dict(t=10, b=10))
    st.plotly_chart(fig_daily, use_container_width=True)

with col4:
    st.subheader("P&L by Instrument")
    instr_colors = instrument_stats['net_pnl'].apply(lambda x: GREEN if x >= 0 else RED)
    fig_instr = go.Figure(go.Bar(
        x=instrument_stats['symbol'],
        y=instrument_stats['net_pnl'],
        marker_color=instr_colors,
        text=instrument_stats['net_pnl'].apply(lambda x: f"+${x:.2f}" if x >= 0 else f"-${abs(x):.2f}"),
        textposition='outside'
    ))
    fig_instr.update_layout(template=DARK, showlegend=False,
        yaxis_title="Net P&L ($)", margin=dict(t=30, b=10))
    st.plotly_chart(fig_instr, use_container_width=True)

# ─────────────────────────────────────────────
# 10. SECTION F — Charts (Row 3)
# ─────────────────────────────────────────────
col5, col6 = st.columns(2)

with col5:
    st.subheader("Direction Breakdown")
    dir_colors = [GREEN if d == 'Short' else RED for d in dir_stats['Direction']]
    fig_dir = go.Figure(go.Bar(
        x=dir_stats['Direction'],
        y=dir_stats['net_pnl'],
        marker_color=dir_colors,
        text=dir_stats.apply(
            lambda r: f"{r['trades']} trades · {r['win_rate']}% WR", axis=1),
        textposition='outside'
    ))
    fig_dir.update_layout(template=DARK, showlegend=False,
        yaxis_title="Net P&L ($)", margin=dict(t=30, b=10))
    st.plotly_chart(fig_dir, use_container_width=True)

with col6:
    st.subheader("Best Trading Hours")
    hour_colors = hour_stats['Net P&L'].apply(lambda x: GREEN if x >= 0 else RED)
    fig_hour = go.Figure(go.Bar(
        x=hour_stats['Hour'].apply(lambda h: f"{h:02d}:00"),
        y=hour_stats['Net P&L'],
        marker_color=hour_colors,
        text=hour_stats['Trades'].apply(lambda t: f"{t}T"),
        textposition='outside'
    ))
    fig_hour.update_layout(template=DARK, showlegend=False,
        yaxis_title="Net P&L ($)", xaxis_title="Hour (UTC)",
        margin=dict(t=30, b=10))
    st.plotly_chart(fig_hour, use_container_width=True)

# ─────────────────────────────────────────────
# 11. SECTION G — Win/Loss Distribution
# ─────────────────────────────────────────────
st.markdown("---")
st.markdown("### Trade Distribution")
col7, col8 = st.columns(2)

with col7:
    st.subheader("P&L Distribution")
    fig_hist = go.Figure()
    fig_hist.add_trace(go.Histogram(
        x=wins['profit'], name='Wins',
        marker_color=GREEN, opacity=0.75, nbinsx=15))
    fig_hist.add_trace(go.Histogram(
        x=losses['profit'], name='Losses',
        marker_color=RED, opacity=0.75, nbinsx=15))
    fig_hist.update_layout(template=DARK, barmode='overlay',
        xaxis_title="P&L ($)", yaxis_title="Frequency",
        legend=dict(orientation='h', y=1.1),
        margin=dict(t=30, b=10))
    st.plotly_chart(fig_hist, use_container_width=True)

with col8:
    st.subheader("Win Rate by Instrument")
    fig_wr = go.Figure(go.Bar(
        x=instrument_stats['symbol'],
        y=instrument_stats['win_rate'],
        marker_color=BLUE,
        text=instrument_stats['win_rate'].apply(lambda x: f"{x:.0f}%"),
        textposition='outside'
    ))
    fig_wr.add_hline(y=50, line_dash='dash', line_color='gray',
        annotation_text='50% breakeven', annotation_position='bottom right')
    fig_wr.update_layout(template=DARK, showlegend=False,
        yaxis_title="Win Rate (%)", yaxis_range=[0, 110],
        margin=dict(t=30, b=10))
    st.plotly_chart(fig_wr, use_container_width=True)

# ─────────────────────────────────────────────
# 12. SECTION H — Instrument Deep Dive Table
# ─────────────────────────────────────────────
st.markdown("---")
st.markdown("### Instrument Deep Dive")

display_instr = instrument_stats[['symbol','trades','net_pnl','avg_pnl','win_rate','pnl_per_lot']].copy()
display_instr.columns = ['Symbol','Trades','Net P&L','Avg P&L/Trade','Win Rate (%)','P&L per Lot']
display_instr['Net P&L']        = display_instr['Net P&L'].apply(lambda x: f"+${x:.2f}" if x >= 0 else f"-${abs(x):.2f}")
display_instr['Avg P&L/Trade']  = display_instr['Avg P&L/Trade'].apply(lambda x: f"+${x:.2f}" if x >= 0 else f"-${abs(x):.2f}")
display_instr['Win Rate (%)']   = display_instr['Win Rate (%)'].apply(lambda x: f"{x:.1f}%")
display_instr['P&L per Lot']    = display_instr['P&L per Lot'].apply(lambda x: f"+${x:.2f}" if x >= 0 else f"-${abs(x):.2f}")

st.dataframe(display_instr, use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────
# 13. SECTION I — Full Transaction Log
# ─────────────────────────────────────────────
st.markdown("---")
st.markdown("### Live Filtered Transaction Log")

df_display = df_sorted[['trade_time','symbol','type','volume','profit','Direction']].copy()
df_display.columns = ['Closed Time','Symbol','Type','Volume','Net Profit','Direction']

def color_profit(val):
    if isinstance(val, (int, float)):
        if val > 0:  return 'color: #10b981; font-weight: bold;'
        if val < 0:  return 'color: #ef4444; font-weight: bold;'
    return 'color: #94a3b8;'

st.dataframe(
    df_display.style.map(color_profit, subset=['Net Profit']),
    use_container_width=True,
    hide_index=True
)

# ─────────────────────────────────────────────
# 14. SECTION J — Diagnostic Alerts
# ─────────────────────────────────────────────
st.markdown("---")
st.markdown("### 🔍 Diagnostic Alerts")

alerts = []

if max_loss_streak >= 3:
    alerts.append(("🔴", f"Max loss streak is {max_loss_streak} — consider a hard stop after 3 consecutive losses."))

short_pnl = dir_stats[dir_stats['Direction'] == 'Short']['net_pnl'].sum()
long_pnl  = dir_stats[dir_stats['Direction'] == 'Long']['net_pnl'].sum()
short_trades = dir_stats[dir_stats['Direction'] == 'Short']['trades'].sum()
long_trades  = dir_stats[dir_stats['Direction'] == 'Long']['trades'].sum()

if long_pnl > short_pnl and short_trades > long_trades:
    alerts.append(("🟡", f"Longs outperform shorts (+${long_pnl:.2f} vs +${short_pnl:.2f}) but {short_trades} of your trades are shorts — direction bias mismatch."))

losing_instruments = instrument_stats[instrument_stats['net_pnl'] < 0]['symbol'].tolist()
if losing_instruments:
    alerts.append(("🟡", f"Net losing instruments this period: {', '.join(losing_instruments)}. Review entry criteria on these."))

if risk_reward < 1.0:
    alerts.append(("🔴", f"Risk:Reward is 1:{risk_reward:.2f} — you are risking more than you win on average."))

if profit_factor >= 2.0:
    alerts.append(("🟢", f"Profit factor of {profit_factor:.2f}× is strong. Focus on maintaining consistency."))

if expectancy > 0:
    alerts.append(("🟢", f"Positive expectancy of +${expectancy:.2f} per trade — your edge is real."))

if not alerts:
    alerts.append(("🟢", "No critical alerts. Keep monitoring consistency week over week."))

for icon, msg in alerts:
    st.markdown(f"{icon} {msg}")
