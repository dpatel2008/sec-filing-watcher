"""
config.py
Every setting for the tracker lives here. Change numbers here and nowhere else.
"""

# Where the daily ranked list comes from (your GitHub repo must be public).
SIGNALS_URL = (
    "https://raw.githubusercontent.com/dpatel2008/sec-filing-watcher/"
    "main/daily_signals_ranked.csv"
)
DATA_DIR = "tracker_data"

# Interactive Brokers connection (paper account only).
IB_HOST = "127.0.0.1"
IB_PORTS = [4002, 7497]      # 4002 = IB Gateway paper, 7497 = Trader Workstation paper
IB_CLIENT_ID = 17
IB_MARKET_DATA_TYPE = 3      # 3 = delayed (free), 1 = live (needs a subscription)

# Which signals to trade.
MIN_SCORE_TO_TRADE = 35
MAX_SIGNAL_AGE_DAYS = 5
MAX_CANDIDATES_TO_ANALYZE = 25
MAX_NEW_TRADES_PER_RUN = 5
MAX_OPEN_POSITIONS = 10
TRADE_13D_LONGS = False      # the SEC list can show the filer instead of the target

# Liquidity filters.
MIN_PRICE = 2.0
MIN_AVG_DOLLAR_VOLUME = 500_000
MAX_PCT_OF_DAILY_VOLUME = 0.05
HISTORY_VOLUME_MULTIPLIER = 1   # IBKR reports US stock volume in lots of 100

# Sizing and exits.
RISK_PER_TRADE_PCT = 0.005   # risk 0.5% of account value per trade
MAX_POSITION_PCT = 0.05      # never more than 5% of account value in one stock
STOP_ATR_MULT = 2.0
TARGET_ATR_MULT = 3.0
HOLD_CALENDAR_DAYS = 14
DEFAULT_ATR_PCT = 0.05       # used only when price history is missing

# Shorting.
ALLOW_UNKNOWN_SHORTABLE = True
MIN_SHORTABLE_LEVEL = 1.5    # IBKR: above 2.5 easy, 1.5 to 2.5 maybe, below 1.5 not available

# Hedge and benchmark.
HEDGE_ENABLED = True
HEDGE_SYMBOL = "IWM"
BENCHMARK_SYMBOL = "SPY"
MIN_HEDGE_DOLLARS = 1000
DEFAULT_BETA = 1.2
HISTORY_DAYS = 120
MIN_RETURNS_FOR_BETA = 20

# Option ideas (shown in the report, never traded).
OPTION_TARGET_DAYS = 30
OPTION_MIN_DAYS = 14
OPTION_SPREAD_WIDTH_PCT = 0.10
OPTION_MAX_CANDIDATES = 10

ORDER_WAIT_SECONDS = 30
