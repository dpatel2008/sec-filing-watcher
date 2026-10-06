"""
broker_ib.py
The only file that talks to Interactive Brokers. Uses the free ib_async library.
Needs IB Gateway (or Trader Workstation) running and logged in to a PAPER account.
"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from ib_async import IB, MarketOrder, Option, Stock

import config as C


def _num(x):
    if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x):
        return float(x)
    return None


def _positive(*values):
    for v in values:
        n = _num(v)
        if n is not None and n > 0:
            return n
    return None


def _ib_symbol(symbol):
    return symbol.replace("-", " ").replace(".", " ")


class IBBroker:
    def __init__(self):
        self.ib = IB()
        self.account_id = ""
        self._stocks = {}

    # ------------------------------------------------------------ connection

    def connect(self):
        for port in C.IB_PORTS:
            try:
                self.ib.connect(C.IB_HOST, port, clientId=C.IB_CLIENT_ID, timeout=8)
                break
            except Exception:
                continue
        if not self.ib.isConnected():
            raise SystemExit(
                "Could not connect to Interactive Brokers.\n"
                "Make sure IB Gateway is open, logged in to your PAPER account, and its API "
                "settings are on (see the setup steps)."
            )
        accounts = self.ib.managedAccounts()
        self.account_id = accounts[0] if accounts else ""
        self.ib.reqMarketDataType(C.IB_MARKET_DATA_TYPE)
        return self.account_id

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()

    # ------------------------------------------------------------ account

    def account_values(self):
        wanted = {
            "NetLiquidation", "TotalCashValue", "GrossPositionValue",
            "AvailableFunds", "BuyingPower", "UnrealizedPnL", "RealizedPnL",
        }
        out = {}
        for item in self.ib.accountSummary():
            if item.tag in wanted and item.currency in ("USD", "BASE", ""):
                try:
                    out[item.tag] = float(item.value)
                except ValueError:
                    pass
        return out

    def positions(self):
        out = []
        for item in self.ib.portfolio():
            contract = item.contract
            out.append({
                "symbol": contract.symbol.replace(" ", "-"),
                "sec_type": contract.secType,
                "qty": float(item.position),
                "avg_cost": float(item.averageCost),
                "market_price": _num(item.marketPrice),
                "market_value": _num(item.marketValue),
                "unrealized": _num(item.unrealizedPNL),
                "realized": _num(item.realizedPNL),
            })
        return out

    # ------------------------------------------------------------ market data

    def _stock(self, symbol):
        if symbol in self._stocks:
            return self._stocks[symbol]
        contract = Stock(_ib_symbol(symbol), "SMART", "USD")
        try:
            self.ib.qualifyContracts(contract)
        except Exception:
            pass
        self._stocks[symbol] = contract if contract.conId else None
        return self._stocks[symbol]

    def snapshot(self, symbol):
        contract = self._stock(symbol)
        if contract is None:
            return None
        ticker = self.ib.reqMktData(contract, "100,101,236", False, False)
        self.ib.sleep(3)
        self.ib.cancelMktData(contract)
        price = _positive(ticker.marketPrice(), ticker.last, ticker.midpoint(), ticker.close)
        if price is None:
            return None
        return {
            "symbol": symbol,
            "price": price,
            "bid": _num(ticker.bid),
            "ask": _num(ticker.ask),
            "volume": _num(ticker.volume),
            "shortable_level": _num(ticker.shortable),
            "shortable_shares": _num(ticker.shortableShares),
            "call_volume": _num(ticker.callVolume),
            "put_volume": _num(ticker.putVolume),
            "call_oi": _num(ticker.callOpenInterest),
            "put_oi": _num(ticker.putOpenInterest),
        }

    def history(self, symbol, days):
        contract = self._stock(symbol)
        if contract is None:
            return []
        try:
            bars = self.ib.reqHistoricalData(
                contract, endDateTime="", durationStr=f"{days} D",
                barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1,
            )
        except Exception:
            return []
        return [
            {
                "date": str(bar.date)[:10],
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            }
            for bar in (bars or [])
        ]

    def option_chain(self, symbol, price):
        contract = self._stock(symbol)
        if contract is None:
            return None
        try:
            chains = self.ib.reqSecDefOptParams(contract.symbol, "", contract.secType, contract.conId)
        except Exception:
            return None
        chains = [c for c in chains if c.exchange == "SMART"] or list(chains)
        if not chains:
            return None
        chain = max(chains, key=lambda c: (len(c.expirations), len(c.strikes)))

        today = datetime.now(ZoneInfo("America/New_York")).date()
        best = None
        for expiry in sorted(chain.expirations):
            try:
                dte = (datetime.strptime(expiry, "%Y%m%d").date() - today).days
            except ValueError:
                continue
            if dte >= C.OPTION_MIN_DAYS and (best is None or abs(dte - C.OPTION_TARGET_DAYS) < abs(best[1] - C.OPTION_TARGET_DAYS)):
                best = (expiry, dte)
        strikes = sorted(chain.strikes)
        if best is None or not strikes:
            return None
        expiry, dte = best

        def nearest(target):
            return min(strikes, key=lambda k: abs(k - target))

        atm = nearest(price)
        low = nearest(price * (1 - C.OPTION_SPREAD_WIDTH_PCT))
        high = nearest(price * (1 + C.OPTION_SPREAD_WIDTH_PCT))
        wanted = {
            "atm_call": (atm, "C"), "atm_put": (atm, "P"),
            "otm_call": (high, "C"), "otm_put": (low, "P"),
        }
        options = {
            name: Option(
                contract.symbol, expiry, strike, right, "SMART",
                multiplier=chain.multiplier or "100", currency="USD",
                tradingClass=chain.tradingClass,
            )
            for name, (strike, right) in wanted.items()
        }
        try:
            self.ib.qualifyContracts(*options.values())
        except Exception:
            pass

        tickers = {}
        for name, option in options.items():
            if option.conId:
                tickers[name] = self.ib.reqMktData(option, "", False, False)
        self.ib.sleep(4)

        out = {"expiry": expiry, "dte": dte}
        for name in wanted:
            quote = None
            ticker = tickers.get(name)
            if ticker is not None:
                greeks = ticker.modelGreeks
                mid = _positive(
                    ticker.midpoint(),
                    greeks.optPrice if greeks else None,
                    ticker.last,
                    ticker.close,
                )
                if mid is not None:
                    quote = {
                        "strike": wanted[name][0],
                        "mid": mid,
                        "iv": _num(greeks.impliedVol) if greeks else None,
                        "delta": _num(greeks.delta) if greeks else None,
                        "gamma": _num(greeks.gamma) if greeks else None,
                        "vega": _num(greeks.vega) if greeks else None,
                        "theta": _num(greeks.theta) if greeks else None,
                    }
            out[name] = quote
        for name, ticker in tickers.items():
            self.ib.cancelMktData(options[name])
        return out

    # ------------------------------------------------------------ orders

    def place_market(self, symbol, action, qty, ref):
        contract = self._stock(symbol)
        if contract is None:
            return {"status": "no contract", "filled": 0, "avg_fill_price": None}
        order = MarketOrder(action, qty)
        order.orderRef = ref
        order.tif = "DAY"
        trade = self.ib.placeOrder(contract, order)
        waited = 0
        while not trade.isDone() and waited < C.ORDER_WAIT_SECONDS:
            self.ib.sleep(1)
            waited += 1
        if not trade.isDone():
            self.ib.cancelOrder(order)
            self.ib.sleep(2)
        status = trade.orderStatus
        return {
            "status": status.status,
            "filled": int(status.filled),
            "avg_fill_price": _positive(status.avgFillPrice),
        }
