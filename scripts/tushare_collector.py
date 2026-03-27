#!/usr/bin/env python3
"""Turtle Investment Framework - Tushare Data Collector (Phase 1A).

Collects 5 years of financial data from Tushare Pro API and outputs
a structured data_pack_market.md file.

Usage:
    python3 scripts/tushare_collector.py --code 600887.SH
    python3 scripts/tushare_collector.py --code 600887.SH --output output/data_pack.md
    python3 scripts/tushare_collector.py --code 600887.SH --dry-run
"""

import argparse
import functools
import os
import sys
import time

import pandas as pd
import tushare as ts

try:
    import yfinance as yf
    _yf_available = True
except ImportError:
    _yf_available = False

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from config import get_token, get_api_url, validate_stock_code
from format_utils import format_number, format_table, format_header


# VIP API name mapping: standard → VIP endpoint (identical params/schema, higher tier)
_VIP_MAP = {
    "income": "income_vip",
    "balancesheet": "balancesheet_vip",
    "cashflow": "cashflow_vip",
    "fina_indicator": "fina_indicator_vip",
    "fina_mainbz": "fina_mainbz_vip",
    "forecast": "forecast_vip",
    "express": "express_vip",
}

# HK line-item field mappings: Tushare column name → HK ind_name (Chinese)
HK_INCOME_MAP = {
    "revenue": "营业额",
    "oper_cost": "营运支出",
    "sell_exp": "销售及分销费用",
    "admin_exp": "行政开支",
    "operate_profit": "经营溢利",
    "invest_income": "应占联营公司溢利",
    "int_income": "利息收入",
    "finance_exp": "融资成本",
    "total_profit": "除税前溢利",
    "income_tax": "税项",
    "n_income": "除税后溢利",
    "n_income_attr_p": "股东应占溢利",
    "minority_gain": "少数股东损益",
    "basic_eps": "每股基本盈利",
    "diluted_eps": "每股摊薄盈利",
}

HK_BALANCE_MAP = {
    "money_cap": "现金及等价物",
    "accounts_receiv": "应收帐款",
    "inventories": "存货",
    "total_cur_assets": "流动资产合计",
    "lt_eqt_invest": "联营公司权益",
    "fix_assets": "物业厂房及设备",
    "cip": "在建工程",
    "intang_assets": "无形资产",
    "total_assets": "总资产",
    "acct_payable": "应付帐款",
    "notes_payable": "应付票据",
    "contract_liab": "递延收入(流动)",
    "st_borr": "短期贷款",
    "total_cur_liab": "流动负债合计",
    "lt_borr": "长期贷款",
    "bond_payable": "应付票据(非流动)",
    "total_liab": "总负债",
    "defer_tax_assets": "递延税项资产",
    "defer_tax_liab": "递延税项负债",
    "total_hldr_eqy_exc_min_int": "股东权益",
    "minority_int": "少数股东权益",
}

HK_CASHFLOW_MAP = {
    "n_cashflow_act": "经营业务现金净额",
    "n_cashflow_inv_act": "投资业务现金净额",
    "n_cash_flows_fnc_act": "融资业务现金净额",
    "c_pay_acq_const_fiolta": "购建无形资产及其他资产",
    "depr_fa_coga_dpba": "折旧及摊销",
    "c_pay_dist_dpcp_int_exp": "已付股息(融资)",
    "c_paid_for_taxes": "已付税项",
    "c_recp_return_invest": "收回投资所得现金",
}

# yfinance field mappings: yfinance index name → Tushare column name
# Both CamelCase and space-separated variants included (format varies by yfinance version)
_YF_INCOME_MAP = {
    "Total Revenue": "revenue", "TotalRevenue": "revenue",
    "Cost Of Revenue": "oper_cost", "CostOfRevenue": "oper_cost",
    "Selling General And Administration": "admin_exp",
    "SellingGeneralAndAdministration": "admin_exp",
    "Operating Income": "operate_profit", "OperatingIncome": "operate_profit",
    "Interest Expense": "finance_exp", "InterestExpense": "finance_exp",
    "Pretax Income": "total_profit", "PretaxIncome": "total_profit",
    "Tax Provision": "income_tax", "TaxProvision": "income_tax",
    "Net Income": "n_income", "NetIncome": "n_income",
    "Net Income Common Stockholders": "n_income_attr_p",
    "NetIncomeCommonStockholders": "n_income_attr_p",
    "Basic EPS": "basic_eps", "BasicEPS": "basic_eps",
    "Diluted EPS": "diluted_eps", "DilutedEPS": "diluted_eps",
}

_YF_BALANCE_MAP = {
    "Cash And Cash Equivalents": "money_cap",
    "CashAndCashEquivalents": "money_cap",
    "Accounts Receivable": "accounts_receiv", "AccountsReceivable": "accounts_receiv",
    "Inventory": "inventories",
    "Current Assets": "total_cur_assets", "CurrentAssets": "total_cur_assets",
    "Investments And Advances": "lt_eqt_invest",
    "Net PPE": "fix_assets", "NetPPE": "fix_assets",
    "Goodwill And Other Intangible Assets": "intang_assets",
    "Total Assets": "total_assets", "TotalAssets": "total_assets",
    "Accounts Payable": "acct_payable", "AccountsPayable": "acct_payable",
    "Current Debt": "st_borr", "CurrentDebt": "st_borr",
    "Current Liabilities": "total_cur_liab", "CurrentLiabilities": "total_cur_liab",
    "Long Term Debt": "lt_borr", "LongTermDebt": "lt_borr",
    "Total Liabilities Net Minority Interest": "total_liab",
    "Stockholders Equity": "total_hldr_eqy_exc_min_int",
    "StockholdersEquity": "total_hldr_eqy_exc_min_int",
    "Minority Interest": "minority_int", "MinorityInterest": "minority_int",
}

_YF_CASHFLOW_MAP = {
    "Operating Cash Flow": "n_cashflow_act", "OperatingCashFlow": "n_cashflow_act",
    "Investing Cash Flow": "n_cashflow_inv_act", "InvestingCashFlow": "n_cashflow_inv_act",
    "Financing Cash Flow": "n_cash_flows_fnc_act", "FinancingCashFlow": "n_cash_flows_fnc_act",
    "Capital Expenditure": "c_pay_acq_const_fiolta", "CapitalExpenditure": "c_pay_acq_const_fiolta",
    "Depreciation And Amortization": "depr_fa_coga_dpba",
    "DepreciationAndAmortization": "depr_fa_coga_dpba",
    "Common Stock Dividend Paid": "c_pay_dist_dpcp_int_exp",
    "Income Tax Paid Supplemental Data": "c_paid_for_taxes",
    "Sale Of Investment": "c_recp_return_invest",
}


def rate_limit(func):
    """Decorator to enforce 0.5s delay between Tushare API calls."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        time.sleep(0.5)
        return func(*args, **kwargs)
    return wrapper


class TushareClient:
    """Client for Tushare Pro API with rate limiting and retry logic."""

    MAX_RETRIES = 5
    RETRY_DELAY = 2.0  # seconds between retries

    BASIC_CACHE_TTL = 7 * 86400  # 7 days in seconds

    def __init__(self, token: str):
        ts.set_token(token)
        self.pro = ts.pro_api(timeout=30)
        self.token = token
        self._store = {}  # {key: pd.DataFrame} for derived metrics computation
        self._yf_available = _yf_available
        self._cache_dir = os.path.join(_PROJECT_ROOT, "output", ".collector_cache")
        # Broker API support: route calls through custom URL + enable VIP endpoints
        api_url = get_api_url()
        self._vip_mode = bool(api_url)
        if api_url:
            self.pro._DataApi__token = token
            self.pro._DataApi__http_url = api_url

    @staticmethod
    def _detect_currency(ts_code: str) -> str:
        """Detect reporting currency based on stock code suffix."""
        return "HKD" if ts_code.upper().endswith(".HK") else "CNY"

    @staticmethod
    def _yf_ticker(ts_code: str) -> str:
        """Convert Tushare stock code to yfinance ticker symbol."""
        code, suffix = ts_code.rsplit(".", 1)
        suffix = suffix.upper()
        if suffix == "SH":
            return f"{code}.SS"
        elif suffix == "SZ":
            return f"{code}.SZ"
        elif suffix == "HK":
            # Tushare 5-digit → YF 4-digit (e.g., 00700 -> 0700)
            return f"{code.lstrip('0').zfill(4)}.HK"
        return ts_code

    def _yf_fallback_price(self, ts_code: str) -> dict | None:
        """Fetch basic price/market cap via yfinance as fallback."""
        if not self._yf_available:
            return None
        try:
            ticker = yf.Ticker(self._yf_ticker(ts_code))
            info = ticker.info
            return {
                "close": info.get("regularMarketPrice") or info.get("previousClose"),
                "market_cap": info.get("marketCap"),
                "source": "yfinance (降级)",
            }
        except Exception:
            return None

    @staticmethod
    def _is_hk(ts_code: str) -> bool:
        """Check if stock code is a Hong Kong listing."""
        return ts_code.upper().endswith(".HK")

    @staticmethod
    def _pivot_hk_line_items(df: pd.DataFrame, field_map: dict) -> pd.DataFrame:
        """Pivot HK ind_name/ind_value rows into one-row-per-period columns.

        HK financial APIs return data in line-item format:
            end_date | ind_name   | ind_value
            20241231 | 营业额     | 60000
            20241231 | 除税后溢利 | 10000
        This pivots them into columnar format matching A-share structure.
        """
        if df.empty or "ind_name" not in df.columns:
            return pd.DataFrame()

        reverse_map = {v: k for k, v in field_map.items() if v is not None}
        df_mapped = df[df["ind_name"].isin(reverse_map)].copy()
        if df_mapped.empty:
            return pd.DataFrame()

        df_mapped["field"] = df_mapped["ind_name"].map(reverse_map)

        # Convert ind_value to numeric
        df_mapped["ind_value"] = pd.to_numeric(df_mapped["ind_value"], errors="coerce")

        pivoted = df_mapped.pivot_table(
            index=["end_date", "ts_code"], columns="field",
            values="ind_value", aggfunc="first"
        ).reset_index()
        pivoted.columns.name = None
        return pivoted

    def _yf_hk_market_data(self, ts_code: str) -> dict | None:
        """Fetch HK stock market data via yfinance (52-week, price, volume)."""
        if not self._yf_available:
            return None
        try:
            ticker = yf.Ticker(self._yf_ticker(ts_code))
            info = ticker.info
            return {
                "close": info.get("regularMarketPrice") or info.get("previousClose"),
                "high_52w": info.get("fiftyTwoWeekHigh"),
                "low_52w": info.get("fiftyTwoWeekLow"),
                "market_cap": info.get("marketCap"),
                "volume_avg": info.get("averageDailyVolume10Day"),
            }
        except Exception:
            return None

    def _yf_weekly_history(self, ts_code: str) -> pd.DataFrame:
        """Fetch 10-year weekly price history via yfinance."""
        if not self._yf_available:
            return pd.DataFrame()
        try:
            ticker = yf.Ticker(self._yf_ticker(ts_code))
            df = ticker.history(period="10y", interval="1wk")
            if df.empty:
                return df
            # Normalize column names to match Tushare weekly format
            df = df.reset_index()
            df = df.rename(columns={
                "Date": "trade_date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "vol",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
            df["ts_code"] = ts_code
            return df[["ts_code", "trade_date", "open", "high", "low", "close", "vol"]]
        except Exception:
            return pd.DataFrame()

    def _yf_fill_missing_hk(self, pivoted, ts_code, statement_type):
        """Fill NaN fields in pivoted HK DataFrame using yfinance data.

        Args:
            pivoted: DataFrame with Tushare data (one row per period).
            ts_code: Tushare stock code (e.g. '00700.HK').
            statement_type: 'income', 'balance', or 'cashflow'.

        Returns:
            (filled_df, yf_used_flag) tuple.
        """
        if not self._yf_available:
            return pivoted, False

        # Select mapping dict FIRST (before NaN check)
        yf_map = {
            "income": _YF_INCOME_MAP,
            "balance": _YF_BALANCE_MAP,
            "cashflow": _YF_CASHFLOW_MAP,
        }.get(statement_type, {})
        if not yf_map:
            return pivoted, False

        filled = pivoted.copy()

        # Ensure all mapped target columns exist (allows filling completely absent fields)
        mapped_ts_cols = set(yf_map.values())
        for col in mapped_ts_cols - set(filled.columns):
            filled[col] = float("nan")

        # Check if any NaN in numeric columns (after column expansion)
        numeric_cols = filled.select_dtypes(include="number").columns
        if numeric_cols.empty or not filled[numeric_cols].isna().any().any():
            return pivoted, False  # return ORIGINAL (no extra NaN cols)

        # Build reverse map: tushare_col → list of yfinance field names
        reverse = {}
        for yf_name, ts_col in yf_map.items():
            reverse.setdefault(ts_col, []).append(yf_name)

        try:
            ticker = yf.Ticker(self._yf_ticker(ts_code))
            if statement_type == "income":
                yf_df = ticker.income_stmt
            elif statement_type == "balance":
                yf_df = ticker.balance_sheet
            else:
                yf_df = ticker.cashflow
        except Exception:
            return pivoted, False  # return ORIGINAL on failure

        if yf_df is None or yf_df.empty:
            return pivoted, False  # return ORIGINAL on failure

        yf_used = False

        for idx, row in filled.iterrows():
            end_date = str(row.get("end_date", ""))
            if len(end_date) < 8:
                continue

            # Match yfinance column by date
            yf_col = None
            for col in yf_df.columns:
                ts = pd.Timestamp(col)
                col_date = ts.strftime("%Y%m%d")
                if col_date == end_date:
                    yf_col = col
                    break
            # Fallback: same year + month=12
            if yf_col is None and end_date[4:] == "1231":
                for col in yf_df.columns:
                    ts = pd.Timestamp(col)
                    if ts.year == int(end_date[:4]) and ts.month == 12:
                        yf_col = col
                        break
            if yf_col is None:
                continue

            # Fill NaN fields from yfinance
            for ts_col, yf_names in reverse.items():
                if ts_col not in filled.columns:
                    continue
                val = filled.at[idx, ts_col]
                if val is not None and val == val:  # not NaN
                    continue
                for yf_name in yf_names:
                    if yf_name in yf_df.index:
                        yf_val = yf_df.at[yf_name, yf_col]
                        if yf_val is not None and yf_val == yf_val:
                            filled.at[idx, ts_col] = float(yf_val)
                            yf_used = True
                            break

        return filled, yf_used

    @rate_limit
    def _safe_call(self, api_name: str, **kwargs) -> pd.DataFrame:
        """Call a Tushare API endpoint with retry logic.

        Auto-upgrades to VIP endpoints when broker is active.

        Args:
            api_name: The API endpoint name (e.g., 'stock_basic').
            **kwargs: Parameters passed to the API call.

        Returns:
            DataFrame with results.

        Raises:
            RuntimeError: After MAX_RETRIES failures.
        """
        # Auto-upgrade to VIP endpoint when broker is active
        effective_name = api_name
        if self._vip_mode and api_name in _VIP_MAP:
            effective_name = _VIP_MAP[api_name]

        last_err = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                api_func = getattr(self.pro, effective_name)
                df = api_func(**kwargs)
                return df
            except Exception as e:
                last_err = e
                if attempt < self.MAX_RETRIES:
                    is_conn_err = isinstance(e, (ConnectionError, OSError)) or \
                        "RemoteDisconnected" in type(e).__name__ or \
                        "ConnectionAborted" in str(e) or \
                        "RemoteDisconnected" in str(e)
                    if is_conn_err:
                        print(f"[retry {attempt}/{self.MAX_RETRIES}] {effective_name}: connection error, re-creating API client...", file=sys.stderr)
                        self.pro = ts.pro_api(timeout=30)
                        # Re-apply broker hacks after re-creating client
                        api_url = get_api_url()
                        if api_url:
                            self.pro._DataApi__token = self.token
                            self.pro._DataApi__http_url = api_url
                    else:
                        print(f"[retry {attempt}/{self.MAX_RETRIES}] {effective_name}: {e}", file=sys.stderr)
                    time.sleep(self.RETRY_DELAY * attempt)
        raise RuntimeError(
            f"Tushare API '{effective_name}' failed after {self.MAX_RETRIES} retries: {last_err}"
        )

    def _cached_basic_call(self, api_name: str, **kwargs) -> pd.DataFrame:
        """Call stock_basic/hk_basic with 7-day file cache."""
        ts_code = kwargs.get("ts_code", "all")
        cache_file = os.path.join(self._cache_dir, f"{api_name}_{ts_code}.json")
        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < self.BASIC_CACHE_TTL:
                return pd.read_json(cache_file)
        df = self._safe_call(api_name, **kwargs)
        if not df.empty:
            os.makedirs(self._cache_dir, exist_ok=True)
            df.to_json(cache_file, orient="records", force_ascii=False)
        return df

    @staticmethod
    def _prepare_display_periods(df, max_annual=5):
        """Select up to max_annual annual reports + any newer interim reports.

        Returns (display_df, column_labels) where column_labels are like:
        ["2025Q3", "2025H1", "2025Q1", "2024", "2023", "2022", "2021", "2020"]
        """
        if df.empty:
            return df, []

        df = df.copy()
        df["end_date"] = df["end_date"].astype(str)
        df = df.drop_duplicates(subset=["end_date"])

        # Split into annual (1231) and non-annual
        annual = df[df["end_date"].str.endswith("1231")].copy()
        non_annual = df[~df["end_date"].str.endswith("1231")].copy()

        # Sort annual descending, take top max_annual
        annual = annual.sort_values("end_date", ascending=False).head(max_annual)

        latest_annual_date = annual["end_date"].max() if not annual.empty else "00000000"

        # Keep only non-annual entries strictly newer than latest annual
        interim = non_annual[non_annual["end_date"] > latest_annual_date].copy()
        interim = interim.sort_values("end_date", ascending=False)

        # Build labels
        def _label(end_date):
            mmdd = end_date[4:]
            year = end_date[:4]
            if mmdd == "1231":
                return year
            elif mmdd == "0630":
                return f"{year}H1"
            elif mmdd == "0331":
                return f"{year}Q1"
            elif mmdd == "0930":
                return f"{year}Q3"
            else:
                return f"{year}_{mmdd}"

        # Combine: interim (desc) + annual (desc)
        display_df = pd.concat([interim, annual], ignore_index=True)
        if display_df.empty:
            return display_df, []

        labels = [_label(d) for d in display_df["end_date"]]
        return display_df, labels

    # --- Feature #14: Section 1 — Basic company info ---

    def get_basic_info(self, ts_code: str) -> str:
        """Section 1: Basic company info from stock_basic/hk_basic + daily_basic/hk_fina_indicator."""
        if self._is_hk(ts_code):
            return self._get_basic_info_hk(ts_code)

        basic = self._cached_basic_call("stock_basic", ts_code=ts_code,
                                       fields="ts_code,name,industry,area,market,exchange,list_date,fullname")
        if basic.empty:
            return format_header(2, "1. 基本信息") + "\n\n数据缺失\n"

        row = basic.iloc[0]

        # Get latest daily_basic for valuation
        daily = self._safe_call("daily_basic", ts_code=ts_code,
                                fields="ts_code,trade_date,close,pe_ttm,pb,total_mv,circ_mv,total_share,float_share")
        val_rows = []
        if not daily.empty:
            self._store["basic_info"] = daily
            d = daily.iloc[0]
            val_rows = [
                ["当前价格", f"{d.get('close', '—')}"],
                ["PE (TTM)", f"{d.get('pe_ttm', '—')}"],
                ["PB", f"{d.get('pb', '—')}"],
                ["总市值 (万元)", format_number(d.get('total_mv', None), divider=1, decimals=2)],
                ["流通市值 (万元)", format_number(d.get('circ_mv', None), divider=1, decimals=2)],
            ]

        lines = [format_header(2, "1. 基本信息"), ""]
        info_table = format_table(
            ["项目", "内容"],
            [
                ["股票代码", str(row.get("ts_code", ""))],
                ["公司名称", str(row.get("name", ""))],
                ["全称", str(row.get("fullname", ""))],
                ["行业", str(row.get("industry", ""))],
                ["地区", str(row.get("area", ""))],
                ["交易所", str(row.get("exchange", ""))],
                ["上市日期", str(row.get("list_date", ""))],
            ] + val_rows,
            alignments=["l", "r"],
        )
        lines.append(info_table)
        return "\n".join(lines)

    def _get_basic_info_hk(self, ts_code: str) -> str:
        """Section 1 (HK): Basic info from hk_basic + hk_fina_indicator."""
        basic = self._cached_basic_call("hk_basic", ts_code=ts_code,
                                       fields="ts_code,name,fullname,market,list_date,enname")
        if basic.empty:
            return format_header(2, "1. 基本信息") + "\n\n数据缺失\n"

        row = basic.iloc[0]

        # Get PE/PB/market_cap from hk_fina_indicator
        val_rows = []
        try:
            fina = self._safe_call("hk_fina_indicator", ts_code=ts_code,
                                   fields="ts_code,end_date,pe_ttm,pb_ttm,total_market_cap,hksk_market_cap")
            if not fina.empty:
                self._store["basic_info"] = fina
                d = fina.iloc[0]
                # Try yfinance for current price
                close_price = "—"
                yf_data = self._yf_hk_market_data(ts_code)
                if yf_data and yf_data.get("close"):
                    close_price = f"{yf_data['close']:.2f}"
                    # Store close for downstream
                    fina_copy = fina.copy()
                    fina_copy["close"] = yf_data["close"]
                    self._store["basic_info"] = fina_copy
                val_rows = [
                    ["当前价格 (HKD)", close_price],
                    ["PE (TTM)", f"{d.get('pe_ttm', '—')}"],
                    ["PB", f"{d.get('pb_ttm', '—')}"],
                    ["总市值 (百万港元)", format_number(d.get('total_market_cap', None), divider=1, decimals=2)],
                ]
        except RuntimeError:
            pass

        lines = [format_header(2, "1. 基本信息"), ""]
        info_table = format_table(
            ["项目", "内容"],
            [
                ["股票代码", str(row.get("ts_code", ""))],
                ["公司名称", str(row.get("name", ""))],
                ["全称", str(row.get("fullname", ""))],
                ["英文名", str(row.get("enname", ""))],
                ["市场", str(row.get("market", ""))],
                ["上市日期", str(row.get("list_date", ""))],
            ] + val_rows,
            alignments=["l", "r"],
        )
        lines.append(info_table)
        return "\n".join(lines)

    # --- Feature #15: Section 2 — Market data ---

    def get_market_data(self, ts_code: str) -> str:
        """Section 2: Current price and 52-week range."""
        if self._is_hk(ts_code):
            return self._get_market_data_hk(ts_code)

        today = pd.Timestamp.now().strftime("%Y%m%d")
        year_ago = (pd.Timestamp.now() - pd.DateOffset(years=1)).strftime("%Y%m%d")

        df = self._safe_call("daily", ts_code=ts_code,
                             start_date=year_ago, end_date=today,
                             fields="ts_code,trade_date,open,high,low,close,vol,amount")
        lines = [format_header(2, "2. 市场行情"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        latest_close = df.iloc[0]["close"]
        high_52w = df["high"].max()
        low_52w = df["low"].min()
        high_date = df.loc[df["high"].idxmax(), "trade_date"]
        low_date = df.loc[df["low"].idxmin(), "trade_date"]
        avg_vol = df["vol"].mean()

        # Store for derived metrics
        self._store["daily_quote"] = df

        table = format_table(
            ["指标", "数值"],
            [
                ["最新收盘价", f"{latest_close:.2f}"],
                ["52周最高", f"{high_52w:.2f} ({high_date})"],
                ["52周最低", f"{low_52w:.2f} ({low_date})"],
                ["52周涨跌幅", f"{(latest_close / low_52w - 1) * 100:.1f}% (自低点)"],
                ["日均成交量 (手)", f"{avg_vol:,.0f}"],
            ],
            alignments=["l", "r"],
        )
        lines.append(table)
        return "\n".join(lines)

    def _get_market_data_hk(self, ts_code: str) -> str:
        """Section 2 (HK): Market data via yfinance (primary) or hk_daily fallback."""
        lines = [format_header(2, "2. 市场行情"), ""]

        # Primary: yfinance
        yf_data = self._yf_hk_market_data(ts_code)
        if yf_data and yf_data.get("close"):
            rows = [["最新价格 (HKD)", f"{yf_data['close']:.2f}"]]
            if yf_data.get("high_52w"):
                rows.append(["52周最高", f"{yf_data['high_52w']:.2f}"])
            if yf_data.get("low_52w"):
                rows.append(["52周最低", f"{yf_data['low_52w']:.2f}"])
            if yf_data.get("market_cap"):
                rows.append(["总市值", format_number(yf_data["market_cap"], divider=1e6)])
            if yf_data.get("volume_avg"):
                rows.append(["10日均量", f"{yf_data['volume_avg']:,.0f}"])
            table = format_table(["指标", "数值"], rows, alignments=["l", "r"])
            lines.append(table)
            return "\n".join(lines)

        # Fallback: hk_daily (requires broker permission)
        df = pd.DataFrame()
        try:
            today = pd.Timestamp.now().strftime("%Y%m%d")
            year_ago = (pd.Timestamp.now() - pd.DateOffset(years=1)).strftime("%Y%m%d")
            df = self._safe_call("hk_daily", ts_code=ts_code,
                                 start_date=year_ago, end_date=today,
                                 fields="ts_code,trade_date,open,high,low,close,vol,amount")
        except RuntimeError:
            pass

        if not df.empty:
            latest_close = df.iloc[0]["close"]
            high_52w = df["high"].max()
            low_52w = df["low"].min()
            high_date = df.loc[df["high"].idxmax(), "trade_date"]
            low_date = df.loc[df["low"].idxmin(), "trade_date"]
            avg_vol = df["vol"].mean()

            lines.append("*来源: Tushare hk_daily*\n")
            table = format_table(
                ["指标", "数值"],
                [
                    ["最新收盘价 (HKD)", f"{latest_close:.2f}"],
                    ["52周最高", f"{high_52w:.2f} ({high_date})"],
                    ["52周最低", f"{low_52w:.2f} ({low_date})"],
                    ["52周涨跌幅", f"{(latest_close / low_52w - 1) * 100:.1f}% (自低点)"],
                    ["日均成交量 (股)", f"{avg_vol:,.0f}"],
                ],
                alignments=["l", "r"],
            )
            lines.append(table)
            return "\n".join(lines)

        lines.append("数据缺失\n")
        return "\n".join(lines)

    # --- Feature #16: Section 3 — Consolidated income statement ---

    def get_income(self, ts_code: str, report_type: str = "1") -> str:
        """Section 3: Five-year consolidated income statement."""
        if self._is_hk(ts_code):
            return self._get_income_hk(ts_code)

        df = self._safe_call("income", ts_code=ts_code,
                             report_type=report_type,
                             fields="ts_code,end_date,report_type,"
                                    "revenue,oper_cost,biz_tax_surch,"
                                    "sell_exp,admin_exp,rd_exp,finance_exp,"
                                    "assets_impair_loss,credit_impair_loss,"
                                    "fv_value_chg_gain,invest_income,asset_disp_income,"
                                    "operate_profit,non_oper_income,non_oper_exp,"
                                    "total_profit,income_tax,"
                                    "n_income,n_income_attr_p,minority_gain,"
                                    "basic_eps,diluted_eps,dt_eps")
        section_label = "3P. 母公司利润表" if report_type == "6" else "3. 合并利润表"
        lines = [format_header(2, section_label), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df, years = self._prepare_display_periods(df)

        # Store for derived metrics
        store_key = "income_parent" if report_type == "6" else "income"
        self._store[store_key] = df
        self._store[store_key + "_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        fields = [
            ("营业收入", "revenue"),
            ("营业成本", "oper_cost"),
            ("税金及附加", "biz_tax_surch"),
            ("销售费用", "sell_exp"),
            ("管理费用", "admin_exp"),
            ("研发费用", "rd_exp"),
            ("财务费用", "finance_exp"),
            ("资产减值损失", "assets_impair_loss"),
            ("信用减值损失", "credit_impair_loss"),
            ("公允价值变动收益", "fv_value_chg_gain"),
            ("投资收益", "invest_income"),
            ("资产处置收益", "asset_disp_income"),
            ("营业利润", "operate_profit"),
            ("营业外收入", "non_oper_income"),
            ("营业外支出", "non_oper_exp"),
            ("利润总额", "total_profit"),
            ("所得税费用", "income_tax"),
            ("净利润", "n_income"),
            ("归母净利润", "n_income_attr_p"),
            ("少数股东损益", "minority_gain"),
            ("基本EPS", "basic_eps"),
            ("稀释EPS", "diluted_eps"),
        ]

        if report_type == "6":
            _exclude = {"minority_gain", "basic_eps", "diluted_eps", "credit_impair_loss"}
            fields = [(label, col) for label, col in fields if col not in _exclude]

        headers = ["项目 (百万元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                if col in ("basic_eps", "diluted_eps", "dt_eps"):
                    row.append(f"{val:.2f}" if val is not None and val == val else "—")
                else:
                    row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万元 (原始数据 / 1,000,000), EPS为元/股*")
        return "\n".join(lines)

    def _get_income_hk(self, ts_code: str) -> str:
        """Section 3 (HK): Income statement via hk_income line-item pivot."""
        df = self._safe_call("hk_income", ts_code=ts_code,
                             fields="ts_code,end_date,ind_name,ind_value")
        lines = [format_header(2, "3. 合并利润表"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        pivoted = self._pivot_hk_line_items(df, HK_INCOME_MAP)
        if pivoted.empty:
            lines.append("数据缺失 (无法匹配行项目)\n")
            return "\n".join(lines)

        pivoted, yf_used = self._yf_fill_missing_hk(pivoted, ts_code, "income")

        pivoted, years = self._prepare_display_periods(pivoted)
        self._store["income"] = pivoted
        self._store["income_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        fields = [
            ("营业额", "revenue"),
            ("营运支出", "oper_cost"),
            ("销售及分销费用", "sell_exp"),
            ("行政开支", "admin_exp"),
            ("经营溢利", "operate_profit"),
            ("应占联营公司溢利", "invest_income"),
            ("融资成本", "finance_exp"),
            ("除税前溢利", "total_profit"),
            ("税项", "income_tax"),
            ("除税后溢利", "n_income"),
            ("股东应占溢利", "n_income_attr_p"),
            ("少数股东损益", "minority_gain"),
            ("每股基本盈利 (HKD)", "basic_eps"),
            ("每股摊薄盈利 (HKD)", "diluted_eps"),
        ]

        headers = ["项目 (百万港元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in pivoted.iterrows():
                val = r.get(col)
                if col in ("basic_eps", "diluted_eps"):
                    row.append(f"{val:.2f}" if val is not None and val == val else "—")
                else:
                    row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万港元 (原始数据 / 1,000,000), EPS为港元/股*")
        if yf_used:
            lines.append("\n*部分缺失数据由 yfinance 补充*")
        return "\n".join(lines)

    # --- Feature #17: Section 3P — Parent company income ---

    def get_income_parent(self, ts_code: str) -> str:
        """Section 3P: Five-year parent-company income statement."""
        if self._is_hk(ts_code):
            return format_header(2, "3P. 母公司利润表") + "\n\n数据缺失 (港股HKFRS不区分母/合并)\n"
        return self.get_income(ts_code, report_type="6")

    # --- Feature #18: Section 4 — Consolidated balance sheet ---

    def get_balance_sheet(self, ts_code: str, report_type: str = "1") -> str:
        """Section 4: Five-year consolidated balance sheet."""
        if self._is_hk(ts_code):
            return self._get_balance_sheet_hk(ts_code)

        df = self._safe_call("balancesheet", ts_code=ts_code,
                             report_type=report_type,
                             fields="ts_code,end_date,report_type,"
                                    "money_cap,trad_asset,notes_receiv,"
                                    "accounts_receiv,oth_receiv,inventories,"
                                    "oth_cur_assets,total_cur_assets,"
                                    "lt_eqt_invest,fix_assets,cip,"
                                    "intang_assets,goodwill,total_assets,"
                                    "st_borr,notes_payable,acct_payable,"
                                    "contract_liab,adv_receipts,"
                                    "non_cur_liab_due_1y,oth_cur_liab,"
                                    "total_cur_liab,lt_borr,bond_payable,"
                                    "total_liab,defer_tax_assets,defer_tax_liab,"
                                    "total_hldr_eqy_exc_min_int,minority_int")
        section_label = "4P. 母公司资产负债表" if report_type == "6" else "4. 合并资产负债表"
        lines = [format_header(2, section_label), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df, years = self._prepare_display_periods(df)

        # Store for derived metrics
        store_key = "balance_sheet_parent" if report_type == "6" else "balance_sheet"
        self._store[store_key] = df
        self._store[store_key + "_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        fields = [
            ("货币资金", "money_cap"),
            ("交易性金融资产", "trad_asset"),
            ("应收票据", "notes_receiv"),
            ("应收账款", "accounts_receiv"),
            ("其他应收款", "oth_receiv"),
            ("存货", "inventories"),
            ("其他流动资产", "oth_cur_assets"),
            ("流动资产合计", "total_cur_assets"),
            ("长期股权投资", "lt_eqt_invest"),
            ("固定资产", "fix_assets"),
            ("在建工程", "cip"),
            ("无形资产", "intang_assets"),
            ("商誉", "goodwill"),
            ("总资产", "total_assets"),
            ("短期借款", "st_borr"),
            ("应付票据", "notes_payable"),
            ("应付账款", "acct_payable"),
            ("合同负债", "contract_liab"),
            ("预收款项", "adv_receipts"),
            ("一年内到期非流动负债", "non_cur_liab_due_1y"),
            ("其他流动负债", "oth_cur_liab"),
            ("流动负债合计", "total_cur_liab"),
            ("长期借款", "lt_borr"),
            ("应付债券", "bond_payable"),
            ("总负债", "total_liab"),
            ("递延所得税资产", "defer_tax_assets"),
            ("递延所得税负债", "defer_tax_liab"),
            ("归母所有者权益", "total_hldr_eqy_exc_min_int"),
            ("少数股东权益", "minority_int"),
        ]

        # Feature #81: For parent company, use subset of fields
        if report_type == "6":
            fields = [
                ("货币资金", "money_cap"),
                ("长期股权投资", "lt_eqt_invest"),
                ("总资产", "total_assets"),
                ("短期借款", "st_borr"),
                ("长期借款", "lt_borr"),
                ("应付债券", "bond_payable"),
                ("一年内到期非流动负债", "non_cur_liab_due_1y"),
                ("总负债", "total_liab"),
                ("归母权益", "total_hldr_eqy_exc_min_int"),
            ]

        headers = ["项目 (百万元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in df.iterrows():
                row.append(format_number(r.get(col)))
            rows.append(row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万元*")
        return "\n".join(lines)

    def _get_balance_sheet_hk(self, ts_code: str) -> str:
        """Section 4 (HK): Balance sheet via hk_balancesheet line-item pivot."""
        df = self._safe_call("hk_balancesheet", ts_code=ts_code,
                             fields="ts_code,end_date,ind_name,ind_value")
        lines = [format_header(2, "4. 合并资产负债表"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        pivoted = self._pivot_hk_line_items(df, HK_BALANCE_MAP)
        if pivoted.empty:
            lines.append("数据缺失 (无法匹配行项目)\n")
            return "\n".join(lines)

        pivoted, yf_used = self._yf_fill_missing_hk(pivoted, ts_code, "balance")

        pivoted, years = self._prepare_display_periods(pivoted)
        self._store["balance_sheet"] = pivoted
        self._store["balance_sheet_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        fields = [
            ("现金及等价物", "money_cap"),
            ("应收帐款", "accounts_receiv"),
            ("存货", "inventories"),
            ("流动资产合计", "total_cur_assets"),
            ("联营公司权益", "lt_eqt_invest"),
            ("物业厂房及设备", "fix_assets"),
            ("无形资产", "intang_assets"),
            ("总资产", "total_assets"),
            ("应付帐款", "acct_payable"),
            ("短期贷款", "st_borr"),
            ("流动负债合计", "total_cur_liab"),
            ("长期贷款", "lt_borr"),
            ("总负债", "total_liab"),
            ("递延税项资产", "defer_tax_assets"),
            ("递延税项负债", "defer_tax_liab"),
            ("股东权益", "total_hldr_eqy_exc_min_int"),
            ("少数股东权益", "minority_int"),
        ]

        headers = ["项目 (百万港元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in pivoted.iterrows():
                row.append(format_number(r.get(col)))
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万港元*")
        if yf_used:
            lines.append("\n*部分缺失数据由 yfinance 补充*")
        return "\n".join(lines)

    # --- Feature #19: Section 4P — Parent company balance sheet ---

    def get_balance_sheet_parent(self, ts_code: str) -> str:
        """Section 4P: Five-year parent-company balance sheet."""
        if self._is_hk(ts_code):
            return format_header(2, "4P. 母公司资产负债表") + "\n\n数据缺失 (港股HKFRS不区分母/合并)\n"
        return self.get_balance_sheet(ts_code, report_type="6")

    # --- Feature #20: Section 5 — Cash flow statement ---

    def get_cashflow(self, ts_code: str) -> str:
        """Section 5: Five-year cash flow statement with FCF calculation."""
        if self._is_hk(ts_code):
            return self._get_cashflow_hk(ts_code)

        df = self._safe_call("cashflow", ts_code=ts_code,
                             report_type="1",
                             fields="ts_code,end_date,report_type,"
                                    "n_cashflow_act,n_cashflow_inv_act,"
                                    "n_cash_flows_fnc_act,c_pay_acq_const_fiolta,"
                                    "depr_fa_coga_dpba,amort_intang_assets,"
                                    "lt_amort_deferred_exp,"
                                    "c_pay_dist_dpcp_int_exp,"
                                    "c_pay_to_staff,c_paid_for_taxes,"
                                    "n_recp_disp_fiolta,receiv_tax_refund,"
                                    "c_recp_return_invest")
        lines = [format_header(2, "5. 现金流量表"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df, years = self._prepare_display_periods(df)

        # Store for derived metrics
        self._store["cashflow"] = df
        self._store["cashflow_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        headers = ["项目 (百万元)"] + years
        rows = []

        simple_fields = [
            ("经营活动现金流 (OCF)", "n_cashflow_act"),
            ("投资活动现金流", "n_cashflow_inv_act"),
            ("筹资活动现金流", "n_cash_flows_fnc_act"),
            ("资本支出(购建固定资产等)", "c_pay_acq_const_fiolta"),
            ("支付给职工现金", "c_pay_to_staff"),
            ("支付的各项税费", "c_paid_for_taxes"),
            ("处置固定资产收回现金", "n_recp_disp_fiolta"),
            ("收到税费返还", "receiv_tax_refund"),
            ("取得投资收益收到现金", "c_recp_return_invest"),
            ("分配股利偿付利息", "c_pay_dist_dpcp_int_exp"),
        ]
        for label, col in simple_fields:
            row = [label]
            for _, r in df.iterrows():
                row.append(format_number(r.get(col)))
            rows.append(row)

        # D&A = 固定资产折旧 + 无形资产摊销 + 长期待摊费用摊销
        da_row = ["折旧与摊销 (D&A)"]
        for _, r in df.iterrows():
            depr = r.get("depr_fa_coga_dpba")
            amort_intang = r.get("amort_intang_assets")
            amort_deferred = r.get("lt_amort_deferred_exp")
            vals = [v for v in [depr, amort_intang, amort_deferred]
                    if v is not None and v == v]
            if vals:
                da_row.append(format_number(sum(float(v) for v in vals)))
            else:
                da_row.append("—")
        rows.append(da_row)

        # FCF = OCF - |Capex| (values are in raw yuan, format_number divides by 1e6)
        fcf_row = ["自由现金流 (FCF)"]
        for _, r in df.iterrows():
            ocf = r.get("n_cashflow_act")
            capex = r.get("c_pay_acq_const_fiolta")
            if ocf is not None and capex is not None:
                fcf = float(ocf) - abs(float(capex))
                fcf_row.append(format_number(fcf))
            else:
                fcf_row.append("—")
        rows.append(fcf_row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万元; FCF = OCF - |Capex|*")
        return "\n".join(lines)

    def _get_cashflow_hk(self, ts_code: str) -> str:
        """Section 5 (HK): Cash flow via hk_cashflow line-item pivot."""
        df = self._safe_call("hk_cashflow", ts_code=ts_code,
                             fields="ts_code,end_date,ind_name,ind_value")
        lines = [format_header(2, "5. 现金流量表"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        pivoted = self._pivot_hk_line_items(df, HK_CASHFLOW_MAP)
        if pivoted.empty:
            lines.append("数据缺失 (无法匹配行项目)\n")
            return "\n".join(lines)

        pivoted, yf_used = self._yf_fill_missing_hk(pivoted, ts_code, "cashflow")

        pivoted, years = self._prepare_display_periods(pivoted)
        self._store["cashflow"] = pivoted
        self._store["cashflow_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        headers = ["项目 (百万港元)"] + years
        rows = []

        simple_fields = [
            ("经营业务现金净额 (OCF)", "n_cashflow_act"),
            ("投资业务现金净额", "n_cashflow_inv_act"),
            ("融资业务现金净额", "n_cash_flows_fnc_act"),
            ("购建无形资产及其他资产", "c_pay_acq_const_fiolta"),
            ("已付税项", "c_paid_for_taxes"),
            ("收回投资所得现金", "c_recp_return_invest"),
            ("已付股息(融资)", "c_pay_dist_dpcp_int_exp"),
        ]
        for label, col in simple_fields:
            row = [label]
            for _, r in pivoted.iterrows():
                row.append(format_number(r.get(col)))
            rows.append(row)

        # D&A: single combined line for HK (no separate amort_intang_assets)
        da_row = ["折旧及摊销 (D&A)"]
        for _, r in pivoted.iterrows():
            da = r.get("depr_fa_coga_dpba")
            if da is not None and da == da:
                da_row.append(format_number(da))
            else:
                da_row.append("—")
        rows.append(da_row)

        # FCF = OCF - |Capex|
        fcf_row = ["自由现金流 (FCF)"]
        for _, r in pivoted.iterrows():
            ocf = r.get("n_cashflow_act")
            capex = r.get("c_pay_acq_const_fiolta")
            if ocf is not None and capex is not None:
                fcf = float(ocf) - abs(float(capex))
                fcf_row.append(format_number(fcf))
            else:
                fcf_row.append("—")
        rows.append(fcf_row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万港元; FCF = OCF - |Capex|; c_pay_to_staff 港股不可用*")
        if yf_used:
            lines.append("\n*部分缺失数据由 yfinance 补充*")
        return "\n".join(lines)

    # --- Feature #21: Section 6 — Dividend history ---

    def get_dividends(self, ts_code: str) -> str:
        """Section 6: Dividend history.
        
        显示三个维度：
        1. 按会计年度（end_date）显示分红明细（区分中期/年度）
        2. 按自然年度（除权日年份）汇总实际实施的分红
        3. 计算正确的分红率（证监会口径：年度总分红/年度净利润）
        
        口径说明：
        - 中期分红率 = 中期分红 / 全年归母净利润（非上半年净利润）
        - 年度分红率 = 年度总分红（中期+末期） / 全年归母净利润
        - TTM股息率 = 过去12个月已实施分红 / 当前股价
        """
        if self._is_hk(ts_code):
            return self._get_dividends_hk(ts_code)

        df = self._safe_call("dividend", ts_code=ts_code,
                             fields="ts_code,end_date,ann_date,div_proc,"
                                    "stk_div,cash_div_tax,record_date,"
                                    "ex_date,base_share")
        lines = [format_header(2, "6. 分红历史"), ""]

        if df.empty:
            lines.append("暂无分红数据\n")
            return "\n".join(lines)

        # Filter for completed dividends
        df_impl = df[df["div_proc"] == "实施"].copy()
        
        # 获取净利润数据用于计算分红率
        income_df = self._get_annual_df("income")
        np_lookup = {}
        if not income_df.empty:
            for _, r in income_df.iterrows():
                year = str(r["end_date"])[:4]
                np_val = self._safe_float(r.get("n_income_attr_p"))
                if np_val is not None and np_val > 0:
                    np_lookup[year] = np_val / 1e6
        
        # Fallback: 直接从存储中获取（使用原始格式）
        if not np_lookup:
            income_raw = self._store.get("income")
            if income_raw is not None and not income_raw.empty:
                for _, r in income_raw.iterrows():
                    end_date_raw = r.get("end_date", "")
                    end_date = str(end_date_raw) if end_date_raw is not None else ""
                    if end_date.endswith("1231"):
                        year = end_date[:4]
                        np_val = self._safe_float(r.get("n_income_attr_p"))
                        if np_val is not None and np_val > 0:
                            np_lookup[year] = np_val / 1e6
        
        # Fallback 2: 从母公司利润表获取
        if not np_lookup:
            income_parent_raw = self._store.get("income_parent")
            if income_parent_raw is not None and not income_parent_raw.empty:
                for _, r in income_parent_raw.iterrows():
                    end_date_raw = r.get("end_date", "")
                    end_date = str(end_date_raw) if end_date_raw is not None else ""
                    if end_date.endswith("1231"):
                        year = end_date[:4]
                        np_val = self._safe_float(r.get("n_income_attr_p"))
                        if np_val is not None and np_val > 0:
                            np_lookup[year] = np_val / 1e6

        # 按会计年度分组，区分中期和年度
        df_impl["end_date_str"] = df_impl["end_date"].astype(str)
        df_impl["fiscal_year"] = df_impl["end_date_str"].str[:4]
        df_impl["is_interim"] = df_impl["end_date_str"].str[4:6] == "06"
        
        # Store for derived metrics
        self._store["dividends_raw"] = df_impl.copy()

        if df_impl.empty:
            lines.append("暂无已实施分红\n")
            return "\n".join(lines)

        # 表1：按会计年度显示分红明细（区分中期/年度）
        lines.append("#### 按会计年度（分红方案归属年度）")
        lines.append("")
        headers = ["会计年度", "类型", "每股现金分红(税前)", "登记日", "除权日", "总分红(百万元)", "分红率"]
        rows = []
        
        # 按会计年度分组汇总
        fiscal_years = sorted(df_impl["fiscal_year"].unique(), reverse=True)[:5]
        for fy in fiscal_years:
            fy_df = df_impl[df_impl["fiscal_year"] == fy]
            for _, r in fy_df.iterrows():
                div_type = "中期" if r.get("is_interim") else "年度"
                cash_div = r.get("cash_div_tax", 0) or 0
                base_share = r.get("base_share", 0) or 0
                total_div = cash_div * base_share * 10000 / 1000000  # 转百万元
                
                # 计算分红率（用对应会计年度的净利润）
                np_val = np_lookup.get(fy, 0)
                payout = (total_div / np_val * 100) if np_val > 0 else 0
                
                rows.append([
                    fy,
                    div_type,
                    f"{cash_div:.4f}",
                    str(r.get("record_date", "—")),
                    str(r.get("ex_date", "—")),
                    f"{total_div:.2f}",
                    f"{payout:.2f}%" if payout > 0 else "—",
                ])

        table = format_table(headers, rows,
                             alignments=["l", "l", "r", "l", "l", "r", "r"])
        lines.append(table)
        lines.append("")
        
        # 表2：按自然年度（除权日年份）汇总实际实施分红
        lines.append("#### 按自然年度（实际除权年份）")
        lines.append("")
        df_impl["ex_year"] = df_impl["ex_date"].astype(str).str[:4]
        df_by_ex_year = df_impl.groupby("ex_year").agg({
            "cash_div_tax": "sum",
            "stk_div": "sum",
            "base_share": "first",
        }).reset_index()
        df_by_ex_year = df_by_ex_year.sort_values("ex_year", ascending=False).head(5)
        
        headers2 = ["自然年度", "累计每股分红(税前)", "分红次数", "总分红(百万元)"]
        rows2 = []
        for _, r in df_by_ex_year.iterrows():
            ex_year = r["ex_year"]
            cash_div = r.get("cash_div_tax", 0) or 0
            base_share = r.get("base_share", 0) or 0
            count = len(df_impl[df_impl["ex_year"] == ex_year])
            total_div = cash_div * base_share * 10000 / 1000000
            rows2.append([
                ex_year,
                f"{cash_div:.4f}",
                str(count),
                f"{total_div:.2f}",
            ])
        
        table2 = format_table(headers2, rows2,
                              alignments=["l", "r", "r", "r"])
        lines.append(table2)
        lines.append("")
        
        # 表3：股息率计算（TTM口径）
        lines.append("#### 股息率计算（TTM口径）")
        lines.append("")
        
        # 获取当前股价
        quote_df = self._store.get("daily_quote")
        latest_price = 0
        if quote_df is not None and not quote_df.empty:
            latest_price = self._safe_float(quote_df.iloc[0].get("close")) or 0
        
        # 计算TTM分红（过去12个月已实施）
        df_impl["ex_date_dt"] = pd.to_datetime(df_impl["ex_date"], format="%Y%m%d", errors="coerce")
        latest_date = df_impl["ex_date_dt"].max()
        if pd.notna(latest_date):
            ttm_start = latest_date - pd.DateOffset(years=1)
            ttm_df = df_impl[df_impl["ex_date_dt"] >= ttm_start]
            ttm_dps = ttm_df["cash_div_tax"].sum()
            ttm_yield = (ttm_dps / latest_price * 100) if latest_price > 0 else 0
        else:
            ttm_dps = 0
            ttm_yield = 0
        
        lines.append(f"| 指标 | 数值 | 口径说明 |")
        lines.append(f"| --- | ---: | --- |")
        lines.append(f"| TTM股息率 | {ttm_yield:.2f}% | 过去12个月已实施分红 / 当前股价 |")
        lines.append(f"| TTM每股分红 | {ttm_dps:.4f}元 | 过去12个月已实施分红合计 |")
        if latest_price > 0:
            lines.append(f"| 当前股价 | {latest_price:.2f}元 | 最新收盘价 |")
        lines.append("")
        
        lines.append("> **口径说明**：")
        lines.append("> - 分红率 = 分红金额 / 对应会计年度归母净利润（证监会法定口径）")
        lines.append("> - TTM股息率 = 过去12个月已实施分红 / 当前股价（行业通用口径）")
        lines.append("> - 中期分红率 ≠ 公司公告的中期分红率（公司用上半年净利润，此处用全年净利润）")
        
        # Store for derived metrics - 使用自然年度汇总数据
        self._store["dividends_by_ex_year"] = df_by_ex_year
        
        return "\n".join(lines)

    def _get_dividends_hk(self, ts_code: str) -> str:
        """Section 6 (HK): Dividend history from hk_fina_indicator."""
        lines = [format_header(2, "6. 分红历史"), ""]
        try:
            df = self._safe_call("hk_fina_indicator", ts_code=ts_code,
                                 fields="ts_code,end_date,dps_hkd,divi_ratio")
        except RuntimeError:
            lines.append("数据缺失 (接口可能无权限)\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("暂无分红数据\n")
            return "\n".join(lines)

        df = df.drop_duplicates(subset=["end_date"])
        df = df.sort_values("end_date", ascending=False).head(5)

        # Store for derived metrics
        self._store["dividends_hk"] = df

        # Build a compatible dividends store for downstream (§17)
        # Create synthetic dividend records with cash_div_tax = dps_hkd
        div_records = []
        for _, r in df.iterrows():
            dps = self._safe_float(r.get("dps_hkd"))
            if dps is not None and dps > 0:
                div_records.append({
                    "end_date": r.get("end_date"),
                    "cash_div_tax": dps,
                    "base_share": 1,  # DPS is per-share already
                    "div_proc": "实施",
                })
        if div_records:
            self._store["dividends"] = pd.DataFrame(div_records)

        # Build EPS lookup for cross-validation
        income_df = self._get_annual_df("income")
        eps_lookup: dict[str, float] = {}
        if not income_df.empty and "basic_eps" in income_df.columns:
            for _, r2 in income_df.iterrows():
                y = str(r2["end_date"])[:4]
                eps = self._safe_float(r2.get("basic_eps"))
                if eps and eps > 0:
                    eps_lookup[y] = eps

        headers = ["年度", "每股股息 (HKD)", "派息率 (%)"]
        rows = []
        for _, r in df.iterrows():
            year = str(r.get("end_date", ""))[:4]
            dps = r.get("dps_hkd")
            ts_ratio = self._safe_float(r.get("divi_ratio"))
            dps_f = self._safe_float(dps)
            eps = eps_lookup.get(year)
            payout = self._resolve_hk_payout(ts_ratio, dps_f, eps)
            rows.append([
                year,
                f"{dps:.4f}" if dps is not None and dps == dps else "—",
                f"{payout:.2f}" if payout is not None else "—",
            ])

        table = format_table(headers, rows, alignments=["l", "r", "r"])
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #22: Section 11 + Appendix A — 10-year weekly prices ---

    def get_weekly_prices(self, ts_code: str) -> str:
        """Section 11 + Appendix A: 10-year weekly price history."""
        if self._is_hk(ts_code):
            return self._get_weekly_prices_hk(ts_code)

        today = pd.Timestamp.now().strftime("%Y%m%d")
        ten_years_ago = (pd.Timestamp.now() - pd.DateOffset(years=10)).strftime("%Y%m%d")

        df = self._safe_call("weekly", ts_code=ts_code,
                             start_date=ten_years_ago, end_date=today,
                             fields="ts_code,trade_date,open,high,low,close,vol,amount")
        lines = [format_header(2, "11. 十年周线行情"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df.sort_values("trade_date", ascending=True)

        # Store for derived metrics
        self._store["weekly_prices"] = df

        # 10-year summary
        high_10y = df["high"].max()
        low_10y = df["low"].min()
        high_date = df.loc[df["high"].idxmax(), "trade_date"]
        low_date = df.loc[df["low"].idxmin(), "trade_date"]
        latest_close = df.iloc[-1]["close"]

        summary_table = format_table(
            ["指标", "数值"],
            [
                ["10年最高", f"{high_10y:.2f} ({high_date})"],
                ["10年最低", f"{low_10y:.2f} ({low_date})"],
                ["最新收盘", f"{latest_close:.2f}"],
                ["距最高回撤", f"{(1 - latest_close / high_10y) * 100:.1f}%"],
                ["距最低涨幅", f"{(latest_close / low_10y - 1) * 100:.1f}%"],
            ],
            alignments=["l", "r"],
        )
        lines.append(summary_table)
        lines.append("")

        # Annual summary
        df["year"] = df["trade_date"].str[:4]
        annual = df.groupby("year").agg(
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            avg_vol=("vol", "mean"),
        ).reset_index()
        annual = annual.sort_values("year", ascending=False)

        lines.append(format_header(3, "年度行情汇总"))
        lines.append("")
        annual_table = format_table(
            ["年度", "最高", "最低", "年末收盘", "周均成交量(手)"],
            [[
                r["year"],
                f"{r['high']:.2f}",
                f"{r['low']:.2f}",
                f"{r['close']:.2f}",
                f"{r['avg_vol']:,.0f}",
            ] for _, r in annual.iterrows()],
            alignments=["l", "r", "r", "r", "r"],
        )
        lines.append(annual_table)
        return "\n".join(lines)

    def _get_weekly_prices_hk(self, ts_code: str) -> str:
        """Section 11 (HK): Weekly prices via yfinance (primary) or hk_daily fallback."""
        lines = [format_header(2, "11. 十年周线行情"), ""]

        # Primary: yfinance
        df = self._yf_weekly_history(ts_code)

        # Fallback: hk_daily → resample to weekly
        if df.empty:
            try:
                today = pd.Timestamp.now().strftime("%Y%m%d")
                ten_years_ago = (pd.Timestamp.now() - pd.DateOffset(years=10)).strftime("%Y%m%d")
                daily = self._safe_call("hk_daily", ts_code=ts_code,
                                        start_date=ten_years_ago, end_date=today,
                                        fields="ts_code,trade_date,open,high,low,close,vol,amount")
                if not daily.empty:
                    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
                    daily = daily.sort_values("trade_date")
                    weekly = daily.resample("W-FRI", on="trade_date").agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "vol": "sum",
                    }).dropna(subset=["close"])
                    weekly = weekly.reset_index()
                    weekly["trade_date"] = weekly["trade_date"].dt.strftime("%Y%m%d")
                    weekly["ts_code"] = ts_code
                    df = weekly
                    if not df.empty:
                        lines.append("*来源: Tushare hk_daily*\n")
            except RuntimeError:
                pass

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df.sort_values("trade_date", ascending=True)
        self._store["weekly_prices"] = df

        # 10-year summary (same logic as A-share)
        high_10y = df["high"].max()
        low_10y = df["low"].min()
        high_date = df.loc[df["high"].idxmax(), "trade_date"]
        low_date = df.loc[df["low"].idxmin(), "trade_date"]
        latest_close = df.iloc[-1]["close"]

        summary_table = format_table(
            ["指标", "数值"],
            [
                ["10年最高 (HKD)", f"{high_10y:.2f} ({high_date})"],
                ["10年最低 (HKD)", f"{low_10y:.2f} ({low_date})"],
                ["最新收盘 (HKD)", f"{latest_close:.2f}"],
                ["距最高回撤", f"{(1 - latest_close / high_10y) * 100:.1f}%"],
                ["距最低涨幅", f"{(latest_close / low_10y - 1) * 100:.1f}%"],
            ],
            alignments=["l", "r"],
        )
        lines.append(summary_table)
        lines.append("")

        # Annual summary
        df["year"] = df["trade_date"].str[:4]
        annual = df.groupby("year").agg(
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            avg_vol=("vol", "mean"),
        ).reset_index()
        annual = annual.sort_values("year", ascending=False)

        lines.append(format_header(3, "年度行情汇总"))
        lines.append("")
        annual_table = format_table(
            ["年度", "最高", "最低", "年末收盘", "周均成交量"],
            [[
                r["year"],
                f"{r['high']:.2f}",
                f"{r['low']:.2f}",
                f"{r['close']:.2f}",
                f"{r['avg_vol']:,.0f}",
            ] for _, r in annual.iterrows()],
            alignments=["l", "r", "r", "r", "r"],
        )
        lines.append(annual_table)
        return "\n".join(lines)

    # --- Feature #23: Section 12 — Financial indicators ---

    def get_fina_indicators(self, ts_code: str) -> str:
        """Section 12: Key financial indicators from fina_indicator/hk_fina_indicator."""
        if self._is_hk(ts_code):
            return self._get_fina_indicators_hk(ts_code)

        df = self._safe_call("fina_indicator", ts_code=ts_code,
                             fields="ts_code,end_date,roe,roe_waa,"
                                    "grossprofit_margin,netprofit_margin,"
                                    "rd_exp,current_ratio,quick_ratio,"
                                    "assets_turn,debt_to_assets,"
                                    "revenue_yoy,netprofit_yoy,"
                                    "ocfps,bps,profit_dedt,"
                                    "ebitda,fcff,netdebt,interestdebt")
        lines = [format_header(2, "12. 关键财务指标"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df, years = self._prepare_display_periods(df)

        # Store for derived metrics
        self._store["fina_indicators"] = df
        self._store["fina_indicators_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        pct_fields = [
            ("ROE (%)", "roe"),
            ("加权ROE (%)", "roe_waa"),
            ("毛利率 (%)", "grossprofit_margin"),
            ("净利率 (%)", "netprofit_margin"),
            ("资产负债率 (%)", "debt_to_assets"),
        ]
        ratio_fields = [
            ("流动比率", "current_ratio"),
            ("速动比率", "quick_ratio"),
            ("总资产周转率", "assets_turn"),
        ]
        growth_fields = [
            ("营收同比增长率 (%)", "revenue_yoy"),
            ("净利润同比增长率 (%)", "netprofit_yoy"),
        ]
        per_share_fields = [
            ("每股经营现金流", "ocfps"),
            ("每股净资产", "bps"),
        ]

        headers = ["指标"] + years
        rows = []
        for label, col in pct_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and val == val else "—")
            rows.append(row)
        for label, col in ratio_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and val == val else "—")
            rows.append(row)
        for label, col in growth_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and val == val else "—")
            rows.append(row)
        for label, col in per_share_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and val == val else "—")
            rows.append(row)
        # Quality: 扣非净利润 (in millions)
        profit_dedt_row = ["扣非净利润 (百万元)"]
        for _, r in df.iterrows():
            val = r.get("profit_dedt")
            profit_dedt_row.append(format_number(val))
        rows.append(profit_dedt_row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        return "\n".join(lines)

    def _get_fina_indicators_hk(self, ts_code: str) -> str:
        """Section 12 (HK): Financial indicators from hk_fina_indicator (structured)."""
        df = self._safe_call("hk_fina_indicator", ts_code=ts_code,
                             fields="ts_code,end_date,roe_avg,gross_profit_ratio,"
                                    "net_profit_ratio,debt_asset_ratio,"
                                    "pe_ttm,pb_ttm,operate_income_yoy,holder_profit_yoy,"
                                    "bps,total_market_cap,hksk_market_cap")
        lines = [format_header(2, "12. 关键财务指标"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df, years = self._prepare_display_periods(df)
        self._store["fina_indicators"] = df
        self._store["fina_indicators_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        pct_fields = [
            ("ROE (%)", "roe_avg"),
            ("毛利率 (%)", "gross_profit_ratio"),
            ("净利率 (%)", "net_profit_ratio"),
            ("资产负债率 (%)", "debt_asset_ratio"),
        ]
        growth_fields = [
            ("营收同比增长率 (%)", "operate_income_yoy"),
            ("净利润同比增长率 (%)", "holder_profit_yoy"),
        ]
        per_share_fields = [
            ("每股净资产 (HKD)", "bps"),
            ("PE (TTM)", "pe_ttm"),
            ("PB", "pb_ttm"),
        ]

        headers = ["指标"] + years
        rows = []
        for label, col in pct_fields + growth_fields + per_share_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and val == val else "—")
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #24: Section 9 — Business segments ---

    def get_segments(self, ts_code: str) -> str:
        """Section 9: Business segment data from fina_mainbz."""
        if self._is_hk(ts_code):
            return format_header(2, "9. 主营业务构成") + "\n\n数据缺失 (港股暂不支持)\n"

        lines = [format_header(2, "9. 主营业务构成"), ""]
        try:
            df = self._safe_call("fina_mainbz", ts_code=ts_code, type="P",
                                 fields="ts_code,end_date,bz_item,bz_sales,bz_profit,bz_cost")
        except RuntimeError:
            lines.append("数据缺失 (接口可能无权限)\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        # Get latest period
        if "end_date" in df.columns:
            latest_period = df["end_date"].max()
            df = df[df["end_date"] == latest_period]

        # Deduplicate by bz_item (keep first occurrence with highest revenue)
        if "bz_item" in df.columns and "bz_sales" in df.columns:
            df = df.sort_values("bz_sales", ascending=False, na_position="last")
            df = df.drop_duplicates(subset=["bz_item"], keep="first")

        headers = ["业务名称", "营业收入 (百万元)", "营业利润 (百万元)", "毛利率 (%)"]
        rows = []
        for _, r in df.iterrows():
            name = r.get("bz_item", "—")
            rev = r.get("bz_sales", None)
            profit = r.get("bz_profit", None)
            margin = r.get("bz_cost", None)
            # Compute gross margin if both revenue and cost available
            gm = "—"
            if rev and margin:
                try:
                    gm = f"{(1 - float(margin)/float(rev)) * 100:.1f}"
                except (ValueError, ZeroDivisionError):
                    gm = "—"
            rows.append([
                str(name),
                format_number(rev),
                format_number(profit),
                gm,
            ])

        table = format_table(headers, rows,
                             alignments=["l", "r", "r", "r"])
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #25: Section 7 (partial) — Top 10 holders + audit ---

    def get_holders(self, ts_code: str) -> str:
        """Section 7: Top 10 shareholders + audit info + placeholders for Agent."""
        if self._is_hk(ts_code):
            return self._get_holders_hk(ts_code)

        lines = [format_header(2, "7. 股东与治理"), ""]

        try:
            df = self._safe_call("top10_holders", ts_code=ts_code)
        except RuntimeError:
            lines.append("股东数据缺失\n")
            lines.append(self._get_audit_section(ts_code))
            lines.append(self._get_agent_placeholders("7"))
            return "\n".join(lines)

        if df.empty:
            lines.append("股东数据缺失\n")
            lines.append(self._get_audit_section(ts_code))
            lines.append(self._get_agent_placeholders("7"))
            return "\n".join(lines)

        if "end_date" in df.columns:
            latest = df["end_date"].max()
            df = df[df["end_date"] == latest]
            lines.append(f"*截至 {latest}*\n")
        else:
            latest = None

        headers = ["序号", "股东名称", "持股数量 (万股)", "持股比例 (%)"]
        rows = []
        for i, (_, r) in enumerate(df.head(10).iterrows(), 1):
            rows.append([
                str(i),
                str(r.get("holder_name", "—")),
                format_number(r.get("hold_amount", None), divider=1e4, decimals=2),
                f"{r.get('hold_ratio', 0) or 0:.2f}",
            ])

        table = format_table(headers, rows,
                             alignments=["l", "l", "r", "r"])
        lines.append(table)
        lines.append("")
        
        lines.append(self._get_audit_section(ts_code))
        lines.append(self._get_agent_placeholders("7"))
        
        return "\n".join(lines)

    def _get_audit_section(self, ts_code: str) -> str:
        """Get audit opinion as subsection of §7."""
        lines = ["", format_header(3, "7.1 审计意见"), ""]
        
        try:
            df = self._safe_call("fina_audit", ts_code=ts_code,
                                 fields="ts_code,end_date,audit_result,audit_agency,audit_fees")
        except RuntimeError:
            lines.append("审计数据缺失\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("审计数据缺失\n")
            return "\n".join(lines)

        df = df.sort_values("end_date", ascending=False).head(3)
        headers = ["年度", "审计意见", "会计事务所", "审计费用 (万元)"]
        rows = []
        for _, r in df.iterrows():
            year = str(r.get("end_date", ""))[:4]
            opinion = str(r.get("audit_result", "—"))
            agency = str(r.get("audit_agency", "—")) if r.get("audit_agency") else "—"
            fees = r.get("audit_fees", None)
            if fees is not None and fees == fees:
                fees_str = f"{fees / 10000:.1f}"
            else:
                fees_str = "—"
            rows.append([year, opinion, agency, fees_str])

        table = format_table(headers, rows, alignments=["l", "l", "l", "r"])
        lines.append(table)
        return "\n".join(lines)

    def _get_agent_placeholders(self, section: str) -> str:
        """Generate placeholder subsections for Agent WebSearch."""
        placeholders = {
            "7": [
                ("7.2", "管理层信息", """逐项搜索并记录：
| # | 搜索项 | 搜索关键词示例 |
|---|--------|-------------|
| 1 | 控股股东及持股比例 | "{公司名} 大股东 控股" |
| 2 | CEO/董事长/CFO 姓名及任期 | "{公司名} 管理层 CEO 董事长" |
| 3 | 过去5年管理层重大变更 | "{公司名} 管理层变更 更换" |
| 4 | 过去5年是否更换审计师 | "{公司名} 更换审计师" |
| 5 | 财务造假/违规/处罚记录 | "{公司名} 财务造假 处罚 证监会" |
| 6 | 控股股东质押/减持/诉讼 | "{公司名} 大股东 质押 减持" |
| 7 | 回购计划/授权 | "{公司名} 股份回购 计划" |

> **已有 tushare 结构化数据**：
> - 项目 1（控股股东持股比例）→ §7 十大股东已有数值
> - 项目 6（质押/减持）→ §16 股权质押已有质押数据
> - 项目 7（回购）→ §15 股票回购已有回购数据
>
> Agent 应**补充定性信息**（变更原因、具体情况、最新动态），不需要重新采集已有数值。"""),
                ("7.3", "历史发展脉络", """| # | 搜索项 | 搜索关键词示例 |
|---|--------|-------------|
| 1 | 发展历程 | "{公司名} 发展历程 历史沿革" |
| 2 | 重大并购/收购 | "{公司名} 重大收购 并购" |
| 3 | 战略转型 | "{公司名} 战略转型 业务调整" |
| 4 | 创始人/关键人物 | "{公司名} 创始人 背景" |"""),
            ],
            "8": [
                ("8.1", "产业链定位", "| # | 搜索项 | 搜索关键词示例 |\n|---|--------|-------------|\n| 1 | 产业链图谱 | \"{行业} 产业链图谱 上中下游\" |\n| 2 | 上下游企业 | \"{公司名} 上游供应商 下游客户\" |\n| 3 | 价值链分布 | \"{行业} 价值链 利润分布\" |\n| 4 | 上下游议价能力 | \"{公司名} 议价能力 上下游\" |"),
                ("8.2", "竞争格局", "| # | 搜索项 | 搜索关键词示例 |\n|---|--------|-------------|\n| 1 | 市场份额 | \"{行业} 市场份额 竞争格局\" |\n| 2 | 行业集中度 | \"{行业} CR5 行业集中度\" |\n| 3 | 差异化优势 | \"{公司名} 竞争优势 差异化\" |"),
                ("8.3", "行业技术路线", "| # | 搜索项 | 搜索关键词示例 |\n|---|--------|-------------|\n| 1 | 主流技术路线 | \"{行业} 技术路线 主流技术\" |\n| 2 | 技术迭代周期 | \"{行业} 技术迭代 更新周期\" |\n| 3 | 公司技术储备 | \"{公司名} 研发投入 专利\" |\n| 4 | 新兴技术趋势 | \"{行业} 新技术 下一代技术\" |"),
                ("8.4", "行业趋势", "| # | 搜索项 | 搜索关键词示例 |\n|---|--------|-------------|\n| 1 | 行业周期位置 | \"{行业} 周期 景气度\" |\n| 2 | 供需展望 | \"{行业} 供需 展望\" |\n| 3 | 核心原材料价格趋势 | \"{原材料} 价格走势 最新\" |"),
                ("8.5", "行业监管动态", "| # | 搜索项 | 搜索关键词示例 |\n|---|--------|-------------|\n| 1 | 碳排放/环保政策 | \"{行业} 碳排放政策 环保监管 最新\" |\n| 2 | 安全生产监管 | \"{行业} 安全生产 监管政策 最新\" |\n| 3 | 产业政策 | \"{行业} 产业政策 发展规划 最新\" |\n| 4 | 进出口政策 | \"{行业} 进出口政策 关税 最新\" |"),
            ],
            "10": [
                ("10.1", "经营回顾", "业绩亮点、核心驱动因素表格（产能释放、规模效应、成本优势、价格因素）"),
                ("10.2", "前瞻指引", "未来战略、资本开支计划"),
                ("10.3", "风险因素", "主要经营风险"),
            ],
        }
        
        lines = []
        if section in placeholders:
            for num, name, desc in placeholders[section]:
                lines.append("")
                lines.append(format_header(3, f"{num} {name}"))
                lines.append("")
                lines.append(f"*[§{num} 待Agent WebSearch补充]*")
                lines.append("")
                lines.append(desc)
        
        return "\n".join(lines)

    def _get_holders_hk(self, ts_code: str) -> str:
        """Section 7 (HK): Institutional holders via yfinance + placeholders."""
        lines = [format_header(2, "7. 股东与治理"), ""]

        if not self._yf_available:
            lines.append("数据缺失 (yfinance不可用)")
            lines.append("")
            lines.append(format_header(3, "7.1 审计意见"))
            lines.append("")
            lines.append("数据缺失 (港股暂不支持)")
            lines.append(self._get_agent_placeholders("7"))
            return "\n".join(lines)

        try:
            ticker = yf.Ticker(self._yf_ticker(ts_code))
            major = ticker.major_holders
            inst = ticker.institutional_holders
        except Exception:
            lines.append("数据缺失 (yfinance不可用)")
            lines.append("")
            lines.append(format_header(3, "7.1 审计意见"))
            lines.append("")
            lines.append("数据缺失 (港股暂不支持)")
            lines.append(self._get_agent_placeholders("7"))
            return "\n".join(lines)

        if major is not None and not major.empty:
            lines.append("**持股概况**\n")
            mh_headers = ["项目", "数值"]
            mh_rows = []
            for _, r in major.iterrows():
                vals = list(r)
                if len(vals) >= 2:
                    mh_rows.append([str(vals[1]), str(vals[0])])
            if mh_rows:
                lines.append(format_table(mh_headers, mh_rows, alignments=["l", "r"]))
                lines.append("")

        if inst is not None and not inst.empty:
            lines.append("**主要机构持股**\n")
            ih_headers = ["机构名称", "持股数量", "占比 (%)", "报告日期"]
            ih_rows = []
            for _, r in inst.head(10).iterrows():
                name = str(r.get("Holder", "—"))
                shares = r.get("Shares")
                pct = r.get("pctHeld") or r.get("% Out")
                date_val = r.get("Date Reported")
                shares_str = format_number(shares, divider=1e4, decimals=2) if shares is not None else "—"
                pct_str = f"{float(pct) * 100:.2f}" if pct is not None and pct == pct else "—"
                date_str = str(date_val)[:10] if date_val is not None else "—"
                ih_rows.append([name, shares_str, pct_str, date_str])
            lines.append(format_table(ih_headers, ih_rows, alignments=["l", "r", "r", "l"]))
            lines.append("")

        if (major is None or major.empty) and (inst is None or inst.empty):
            lines.append("数据缺失 (yfinance无持股数据)")
            lines.append("")
        
        lines.append("*数据来源: yfinance*")
        lines.append("")
        lines.append(format_header(3, "7.1 审计意见"))
        lines.append("")
        lines.append("数据缺失 (港股暂不支持)")
        lines.append(self._get_agent_placeholders("7"))
        return "\n".join(lines)

    # --- Feature #84: Section 14 — Risk-free rate ---

    def get_risk_free_rate(self, ts_code: str = None) -> str:
        """Section 14: Risk-free rate from yc_cb (中债国债收益率曲线)."""
        lines = [format_header(2, "14. 无风险利率"), ""]
        try:
            today = pd.Timestamp.now().strftime("%Y%m%d")
            # Get recent 10-year government bond yield
            df = self._safe_call("yc_cb", ts_code="1001.CB",
                                 curve_type="0",
                                 curve_term="10",
                                 start_date=(pd.Timestamp.now() - pd.DateOffset(months=1)).strftime("%Y%m%d"),
                                 end_date=today,
                                 fields="trade_date,yield")
        except RuntimeError:
            lines.append("数据缺失 (接口可能无权限)\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df.sort_values("trade_date", ascending=False)

        # Store for derived metrics
        self._store["risk_free_rate"] = df

        latest = df.iloc[0]

        table = format_table(
            ["日期", "10年期国债收益率 (%)"],
            [[str(latest.get("trade_date", "—")),
              f"{latest.get('yield', 0):.4f}"]],
            alignments=["l", "r"],
        )
        lines.append(table)
        lines.append("")
        lines.append("*数据来源: 中债国债收益率曲线 (yc_cb)*")
        return "\n".join(lines)

    # --- Feature #85: Section 15 — Share repurchase ---

    def get_repurchase(self, ts_code: str) -> str:
        """Section 15: Share repurchase data from repurchase endpoint."""
        if self._is_hk(ts_code):
            return format_header(2, "15. 股票回购") + "\n\n数据缺失 (港股暂不支持)\n"

        lines = [format_header(2, "15. 股票回购"), ""]
        try:
            df = self._safe_call("repurchase", ts_code=ts_code,
                                 fields="ts_code,ann_date,end_date,proc,exp_date,"
                                        "vol,amount,high_limit,low_limit")
        except RuntimeError:
            lines.append("数据缺失 (接口可能无权限)\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("近3年无回购记录\n")
            return "\n".join(lines)

        # Filter to last 3 years
        three_years_ago = (pd.Timestamp.now() - pd.DateOffset(years=3)).strftime("%Y%m%d")
        if "ann_date" in df.columns:
            df = df[df["ann_date"] >= three_years_ago].copy()

        if df.empty:
            lines.append("近3年无回购记录\n")
            return "\n".join(lines)

        df = df.sort_values("ann_date", ascending=False)

        # Deduplicate: same repurchase plan appears multiple times at
        # different progress stages (董事会预案→股东大会通过→实施→完成).
        # Keep only one record per (ann_date, amount) pair.
        if "amount" in df.columns:
            df = df.drop_duplicates(subset=["ann_date", "amount"], keep="first")

        # Filter to executed repurchases only (align with dividend
        # div_proc=="实施" filtering).  Fall back to deduped full data
        # if no executed records exist.
        if "proc" in df.columns:
            executed = df[df["proc"].isin(["完成", "实施"])]
            if not executed.empty:
                df = executed

        # Cross-date dedup: same repurchase plan may appear on different
        # announcement dates (progress updates).  Deduplicate by plan identity.
        if all(c in df.columns for c in ["high_limit", "amount", "proc"]):
            completed = df[df["proc"] == "完成"].copy()
            executing = df[df["proc"] == "实施"].copy()
            other = df[~df["proc"].isin(["完成", "实施"])].copy()

            if not completed.empty:
                completed = completed.drop_duplicates(
                    subset=["amount", "high_limit"], keep="first")
            if not executing.empty:
                executing = executing.sort_values("amount", ascending=False)
                executing = executing.drop_duplicates(
                    subset=["high_limit"], keep="first")

            # If a plan already has a 完成 record, drop its 实施 records
            if not completed.empty and not executing.empty:
                completed_limits = set(completed["high_limit"].dropna())
                executing = executing[
                    ~executing["high_limit"].isin(completed_limits)]

            df = pd.concat(
                [completed, executing, other]).sort_values(
                    "ann_date", ascending=False)

        # Store filtered/deduped data for derived metrics (§17.2 O)
        self._store["repurchase"] = df

        headers = ["公告日", "进度", "回购金额 (百万元)", "回购股数 (万股)", "价格下限", "价格上限"]
        rows = []
        total_amount = 0
        for _, r in df.iterrows():
            amt = r.get("amount", None)
            vol = r.get("vol", None)
            if amt is not None and amt == amt:
                total_amount += float(amt)
            rows.append([
                str(r.get("ann_date", "—")),
                str(r.get("proc", "—")),
                format_number(amt),
                format_number(vol, divider=1e4, decimals=2) if vol is not None and vol == vol else "—",
                f"{r.get('low_limit', 0):.2f}" if r.get("low_limit") is not None else "—",
                f"{r.get('high_limit', 0):.2f}" if r.get("high_limit") is not None else "—",
            ])

        table = format_table(headers, rows,
                             alignments=["l", "l", "r", "r", "r", "r"])
        lines.append(table)
        lines.append("")
        lines.append(f"近3年累计回购金额（已去重/仅完成+实施）: {format_number(total_amount)} 百万元")
        years_span = min(3, max(1, len(set(str(r.get("ann_date", ""))[:4] for _, r in df.iterrows()))))
        lines.append(f"年均回购金额: {format_number(total_amount / years_span)} 百万元")
        lines.append("")
        lines.append("> ⚠️ 上述金额包含所有用途（注销/员工持股/市值管理）。"
                     "O 仅计入注销型回购，Phase 3 需核实用途后调整。")
        return "\n".join(lines)

    # --- Feature #86: Section 16 — Share pledge statistics ---

    def get_pledge_stat(self, ts_code: str) -> str:
        """Section 16: Share pledge statistics from pledge_stat endpoint."""
        if self._is_hk(ts_code):
            return format_header(2, "16. 股权质押") + "\n\n不适用 (港股无此制度)\n"

        lines = [format_header(2, "16. 股权质押"), ""]
        try:
            df = self._safe_call("pledge_stat", ts_code=ts_code,
                                 fields="ts_code,end_date,pledge_count,"
                                        "unrest_pledge,rest_pledge,"
                                        "total_share,pledge_ratio")
        except RuntimeError:
            lines.append("数据缺失 (接口可能无权限)\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df.sort_values("end_date", ascending=False)
        latest = df.iloc[0]

        table = format_table(
            ["项目", "数值"],
            [
                ["统计日期", str(latest.get("end_date", "—"))],
                ["质押笔数", f"{int(latest.get('pledge_count', 0))}"],
                ["无限售质押 (万股)", format_number(latest.get("unrest_pledge"), divider=1e4, decimals=2)],
                ["有限售质押 (万股)", format_number(latest.get("rest_pledge"), divider=1e4, decimals=2)],
                ["总股本 (万股)", format_number(latest.get("total_share"), divider=1e4, decimals=2)],
                ["质押比例 (%)", f"{latest.get('pledge_ratio', 0):.2f}"],
            ],
            alignments=["l", "r"],
        )
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #90-92: Derived metrics (Section 17) ---

    @staticmethod
    def _safe_float(val) -> float | None:
        """Convert a value to float, returning None for NaN/None."""
        if val is None:
            return None
        try:
            f = float(val)
            return None if f != f else f  # NaN check
        except (TypeError, ValueError):
            return None

    def _get_annual_df(self, store_key: str) -> pd.DataFrame:
        """Get stored DataFrame filtered to annual periods only (end_date ending in 1231)."""
        df = self._store.get(store_key)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["end_date"] = df["end_date"].astype(str)
        annual = df[df["end_date"].str.endswith("1231")].copy()
        return annual.sort_values("end_date", ascending=False)

    def _get_annual_series(self, store_key: str, col: str) -> list[tuple[str, float | None]]:
        """Extract (year_label, value) pairs for annual periods, sorted desc."""
        df = self._get_annual_df(store_key)
        if df.empty or col not in df.columns:
            return []
        result = []
        for _, r in df.iterrows():
            year = str(r["end_date"])[:4]
            result.append((year, self._safe_float(r.get(col))))
        return result

    @staticmethod
    def _resolve_hk_payout(ts_ratio: float | None, dps: float | None, eps: float | None) -> float | None:
        """Resolve HK payout ratio (%) with Tushare fix + DPS/EPS cross-validation.

        Steps:
        1. Fix Tushare divi_ratio if < 1 (dirty data: decimal instead of %).
        2. Self-compute from DPS / EPS × 100 if both available.
        3. Cross-validate: prefer Tushare if diff < 20%, else use computed.
        4. Fall back to whichever is available; None if neither.
        """
        # Step 1: fix Tushare divi_ratio if < 1
        if ts_ratio is not None and ts_ratio < 1:
            ts_ratio *= 100

        # Step 2: self-compute from DPS / EPS
        computed = None
        if dps is not None and dps > 0 and eps is not None and eps > 0:
            computed = dps / eps * 100

        # Step 3: cross-validate and pick
        if ts_ratio is not None and computed is not None:
            diff = abs(ts_ratio - computed) / computed
            return ts_ratio if diff < 0.2 else computed
        if computed is not None:
            return computed
        if ts_ratio is not None:
            return ts_ratio
        return None

    def _get_payout_by_year(self) -> dict[str, float]:
        """Get payout ratio (%) by year from stored dividend data.

        HK path: Tushare divi_ratio fix + DPS/EPS cross-validation.
        A-share path: computes from cash_div × base_share × 10000 / net_income × 100.
        
        口径说明（证监会法定口径）：
        - 年度分红率 = 该会计年度所有分红（中期+末期） / 该年度归母净利润
        - 严禁用中期分红率（中期分红/上半年净利润）替代年度分红率
        """
        # HK path: Tushare divi_ratio fix + DPS/EPS cross-validation
        hk_df = self._store.get("dividends_hk")
        if hk_df is not None and not hk_df.empty:
            # Build EPS lookup from income statement
            income_df = self._get_annual_df("income")
            eps_lookup: dict[str, float] = {}
            if not income_df.empty and "basic_eps" in income_df.columns:
                for _, r in income_df.iterrows():
                    year = str(r["end_date"])[:4]
                    eps = self._safe_float(r.get("basic_eps"))
                    if eps and eps > 0:
                        eps_lookup[year] = eps

            result: dict[str, float] = {}
            for _, r in hk_df.iterrows():
                year = str(r.get("end_date", ""))[:4]
                if not year:
                    continue
                ts_ratio = self._safe_float(r.get("divi_ratio"))
                dps = self._safe_float(r.get("dps_hkd"))
                eps = eps_lookup.get(year)
                resolved = self._resolve_hk_payout(ts_ratio, dps, eps)
                if resolved is not None:
                    result[year] = resolved
            return result

        # A-share path: 使用原始分红数据按会计年度汇总
        div_raw = self._store.get("dividends_raw")
        income_df = self._get_annual_df("income")
        
        if div_raw is not None and not div_raw.empty and not income_df.empty:
            # Build net income lookup by year
            np_lookup = {}
            for _, r in income_df.iterrows():
                year = str(r["end_date"])[:4]
                np_lookup[year] = self._safe_float(r.get("n_income_attr_p")) or 0
            
            # 按会计年度汇总所有分红（中期+年度）
            result = {}
            for fy in div_raw["fiscal_year"].unique():
                fy_df = div_raw[div_raw["fiscal_year"] == fy]
                total_div = 0
                for _, r in fy_df.iterrows():
                    cash_div = self._safe_float(r.get("cash_div_tax")) or 0
                    base_share = self._safe_float(r.get("base_share")) or 0
                    total_div += cash_div * base_share * 10000  # base_share is 万股
                
                np_val = np_lookup.get(fy, 0)
                if total_div > 0 and np_val > 0:
                    result[fy] = total_div / np_val * 100
            return result

        # Fallback: 使用自然年度数据
        div_by_ex_year = self._store.get("dividends_by_ex_year")
        if div_by_ex_year is None or div_by_ex_year.empty or income_df.empty:
            return {}

        # Build net income lookup by year
        np_lookup = {}
        for _, r in income_df.iterrows():
            year = str(r["end_date"])[:4]
            np_lookup[year] = self._safe_float(r.get("n_income_attr_p"))

        result = {}
        for _, r in div_by_ex_year.iterrows():
            ex_year = str(r.get("ex_year", ""))
            if not ex_year:
                continue
            cash_div = self._safe_float(r.get("cash_div_tax")) or 0
            base_share = self._safe_float(r.get("base_share")) or 0
            div_total = cash_div * base_share * 10000  # base_share is 万股
            
            # 使用对应会计年度的净利润
            np_val = np_lookup.get(ex_year)
            if div_total and np_val and np_val > 0:
                result[ex_year] = div_total / np_val * 100
        return result

    def _compute_financial_trends(self) -> str | None:
        """Compute §17.1: Financial trend summary (CAGR, debt ratios, net cash, payout)."""
        income_df = self._get_annual_df("income")
        bs_df = self._get_annual_df("balance_sheet")

        if income_df.empty or len(income_df) < 2:
            return None

        years_labels = [str(r["end_date"])[:4] for _, r in income_df.iterrows()]
        n_years = len(years_labels)

        lines = [format_header(3, "17.1 财务趋势速览"), ""]

        # --- Revenue & Net Profit series ---
        rev_series = [(y, self._safe_float(r.get("revenue"))) for y, (_, r) in zip(years_labels, income_df.iterrows())]
        np_series = [(y, self._safe_float(r.get("n_income_attr_p"))) for y, (_, r) in zip(years_labels, income_df.iterrows())]

        # CAGR calculation
        def _cagr(series: list[tuple[str, float | None]]) -> str:
            vals = [v for _, v in series if v is not None and v > 0]
            if len(vals) < 2:
                return "—"
            # series is desc order: [latest, ..., oldest]
            latest, oldest = vals[0], vals[-1]
            n = len(vals) - 1
            if oldest <= 0:
                return "—"
            cagr = (latest / oldest) ** (1 / n) - 1
            return f"{cagr * 100:.2f}%"

        rev_cagr = _cagr(rev_series)
        np_cagr = _cagr(np_series)

        # --- Interest-bearing debt per year ---
        def _interest_bearing_debt(row) -> float | None:
            components = ["st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y"]
            total = 0.0
            any_valid = False
            for c in components:
                v = self._safe_float(row.get(c))
                if v is not None:
                    total += v
                    any_valid = True
            return total if any_valid else None

        debt_series = []  # (year, debt_raw)
        debt_ratio_series = []  # (year, ratio_pct)
        net_cash_series = []  # (year, net_cash_raw)
        if not bs_df.empty:
            for _, r in bs_df.iterrows():
                year = str(r["end_date"])[:4]
                debt = _interest_bearing_debt(r)
                ta = self._safe_float(r.get("total_assets"))
                cash = self._safe_float(r.get("money_cap"))
                debt_series.append((year, debt))
                if debt is not None and ta and ta > 0:
                    debt_ratio_series.append((year, debt / ta * 100))
                else:
                    debt_ratio_series.append((year, None))
                if cash is not None and debt is not None:
                    net_cash_series.append((year, cash - debt))
                else:
                    net_cash_series.append((year, None))

        # --- Payout ratio per year ---
        payout_lookup = self._get_payout_by_year()
        payout_series = [(y, payout_lookup.get(y)) for y, _ in np_series]

        # --- Build table ---
        # Use income years as primary (most complete)
        def _fmt_val(val: float | None, divider: float = 1e6, is_pct: bool = False) -> str:
            if val is None:
                return "—"
            if is_pct:
                return f"{val:.2f}"
            return format_number(val, divider=divider)

        def _lookup(series: list[tuple[str, float | None]], year: str) -> float | None:
            for y, v in series:
                if y == year:
                    return v
            return None

        headers = ["指标"] + years_labels + ["5年CAGR"]
        rows = []

        # Revenue
        row = ["营业收入（百万元）"]
        for y, v in rev_series:
            row.append(_fmt_val(v))
        row.append(rev_cagr)
        rows.append(row)

        # Net profit
        row = ["归母净利润（百万元）"]
        for y, v in np_series:
            row.append(_fmt_val(v))
        row.append(np_cagr)
        rows.append(row)

        # Interest-bearing debt
        row = ["有息负债（百万元）"]
        for y in years_labels:
            row.append(_fmt_val(_lookup(debt_series, y)))
        row.append("—")
        rows.append(row)

        # Debt/total_assets ratio
        row = ["有息负债/总资产（%）"]
        for y in years_labels:
            row.append(_fmt_val(_lookup(debt_ratio_series, y), is_pct=True))
        row.append("—")
        rows.append(row)

        # Net cash
        row = ["广义净现金（百万元）"]
        for y in years_labels:
            row.append(_fmt_val(_lookup(net_cash_series, y)))
        row.append("—")
        rows.append(row)

        # Payout ratio
        row = ["股息支付率（%）"]
        for y in years_labels:
            row.append(_fmt_val(_lookup(payout_series, y), is_pct=True))
        row.append("—")
        rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * (n_years + 1))
        lines.append(table)
        return "\n".join(lines)

    def _compute_factor2_inputs(self, ts_code: str) -> str | None:
        """Compute §17.2: Factor 2 input parameters (OE components, payout, threshold)."""
        income_df = self._get_annual_df("income")
        cf_df = self._get_annual_df("cashflow")

        if income_df.empty:
            return None

        years_labels = [str(r["end_date"])[:4] for _, r in income_df.iterrows()]
        n_years = len(years_labels)
        lines = [format_header(3, "17.2 因子2输入参数"), ""]

        # --- Per-year table: C, B, minority%, D&A, Capex, Capex/D&A, FCF ---
        headers = ["变量"] + years_labels
        rows = []

        # C = n_income_attr_p (already in millions after format_number)
        c_row = ["C 归母净利润"]
        b_row = ["B 少数股东损益"]
        min_pct_row = ["少数股东占比（%）"]
        for _, r in income_df.iterrows():
            c = self._safe_float(r.get("n_income_attr_p"))
            b = self._safe_float(r.get("minority_gain"))
            ni = self._safe_float(r.get("n_income"))
            c_row.append(format_number(c))
            b_row.append(format_number(b))
            if b is not None and ni and ni != 0:
                min_pct_row.append(f"{b / ni * 100:.2f}")
            else:
                min_pct_row.append("—")
        rows.extend([c_row, b_row, min_pct_row])

        # D&A and Capex from cashflow
        da_row = ["D 折旧与摊销"]
        capex_row = ["E 资本开支"]
        capex_da_row = ["Capex/D&A"]
        fcf_row = ["FCF = OCF - |Capex|"]
        da_vals = []  # for median calculation
        capex_vals = []  # for median calculation
        capex_da_vals = []

        if not cf_df.empty:
            # Align cashflow by year
            cf_by_year = {}
            for _, r in cf_df.iterrows():
                y = str(r["end_date"])[:4]
                cf_by_year[y] = r

            for y in years_labels:
                r = cf_by_year.get(y)
                if r is None:
                    da_row.append("—"); capex_row.append("—")
                    capex_da_row.append("—"); fcf_row.append("—")
                    continue

                depr = self._safe_float(r.get("depr_fa_coga_dpba"))
                amort_i = self._safe_float(r.get("amort_intang_assets"))
                amort_d = self._safe_float(r.get("lt_amort_deferred_exp"))
                da_components = [v for v in [depr, amort_i, amort_d] if v is not None]
                da = sum(da_components) if da_components else None

                capex = self._safe_float(r.get("c_pay_acq_const_fiolta"))
                ocf = self._safe_float(r.get("n_cashflow_act"))

                da_row.append(format_number(da) if da is not None else "—")
                capex_row.append(format_number(capex))
                if da and da > 0 and capex is not None:
                    ratio = abs(capex) / da
                    capex_da_row.append(f"{ratio:.2f}")
                    da_vals.append(da)
                    capex_vals.append(abs(capex))
                    capex_da_vals.append(ratio)
                else:
                    capex_da_row.append("—")
                if ocf is not None and capex is not None:
                    fcf_row.append(format_number(ocf - abs(capex)))
                else:
                    fcf_row.append("—")
        else:
            for _ in years_labels:
                da_row.append("—"); capex_row.append("—")
                capex_da_row.append("—"); fcf_row.append("—")

        rows.extend([da_row, capex_row, capex_da_row, fcf_row])

        table = format_table(headers, rows, alignments=["l"] + ["r"] * n_years)
        lines.append(table)
        lines.append("")

        # --- Summary variables ---
        summary_rows = []

        # F = Capex/D&A 5-year median
        if capex_da_vals:
            sorted_vals = sorted(capex_da_vals)
            mid = len(sorted_vals) // 2
            f_median = sorted_vals[mid] if len(sorted_vals) % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
            summary_rows.append(["F（Capex/D&A 5年中位数）", f"{f_median:.2f}", "—"])
        else:
            summary_rows.append(["F（Capex/D&A 5年中位数）", "—", "数据不足"])

        # Payout ratio: M, N
        payout_lookup = self._get_payout_by_year()
        payout_ratios = [payout_lookup[y] for y in years_labels[:3] if y in payout_lookup]

        if payout_ratios:
            m_mean = sum(payout_ratios) / len(payout_ratios)
            if len(payout_ratios) > 1:
                variance = sum((x - m_mean) ** 2 for x in payout_ratios) / (len(payout_ratios) - 1)
                n_std = variance ** 0.5
            else:
                n_std = 0
            summary_rows.append(["M（支付率3年均值）", f"{m_mean:.2f}%", f"基于 {len(payout_ratios)} 年"])
            summary_rows.append(["N（支付率3年标准差）", f"{n_std:.2f}%", "—"])
        else:
            summary_rows.append(["M（支付率3年均值）", "—", "分红数据不足"])
            summary_rows.append(["N（支付率3年标准差）", "—", "—"])

        # O = buyback annual average (cancellation-type only)
        # Tushare does not provide repurchase purpose; default to 0.
        # Phase 3 should determine cancellation amount from annual report.
        rep_df = self._store.get("repurchase")
        if rep_df is not None and not rep_df.empty:
            summary_rows.append(["O（年均回购金额）", "0.00 百万",
                                 "默认0（无法区分注销型），Phase 3 从年报确认后填入"])
        else:
            summary_rows.append(["O（年均回购金额）", "0.00 百万", "无回购记录"])

        # Rf and II (threshold)
        rf_df = self._store.get("risk_free_rate")
        rf_val = None
        if rf_df is not None and not rf_df.empty:
            rf_val = self._safe_float(rf_df.iloc[0].get("yield"))

        if rf_val is not None:
            summary_rows.append(["Rf（无风险利率）", f"{rf_val:.4f}%", "来自 §14"])
            # Determine market type from ts_code
            if ts_code.endswith(".HK"):
                ii = max(5.0, rf_val + 3.0)
                summary_rows.append(["II（门槛值）", f"{ii:.2f}%", f"港股: max(5%, {rf_val:.2f}%+3%)"])
            else:  # A-share default
                ii = max(3.5, rf_val + 2.0)
                summary_rows.append(["II（门槛值）", f"{ii:.2f}%", f"A股: max(3.5%, {rf_val:.2f}%+2%)"])
        else:
            summary_rows.append(["Rf（无风险利率）", "—", "数据缺失"])
            summary_rows.append(["II（门槛值）", "—", "需Rf"])

        # OE base case (G=1.0)
        latest_c = self._safe_float(income_df.iloc[0].get("n_income_attr_p"))
        if latest_c is not None:
            summary_rows.append(["OE_base（G=1.0）", f"{format_number(latest_c)} 百万",
                                 "OE = C + D×(1-G); LLM 选 G 后代入"])

        summary_table = format_table(["汇总变量", "值", "说明"], summary_rows,
                                     alignments=["l", "r", "l"])
        lines.append(summary_table)
        return "\n".join(lines)

    def _compute_factor4_inputs(self) -> str | None:
        """Compute §17.6: Price percentiles from 10yr weekly data."""
        wp_df = self._store.get("weekly_prices")
        basic_df = self._store.get("basic_info")

        if wp_df is None or wp_df.empty:
            return None

        lines = [format_header(3, "17.6 因子4·股价分位"), ""]

        closes = wp_df["close"].dropna().tolist()
        if not closes:
            return None

        nn = len(closes)
        current_price = closes[-1] if closes else None  # latest (sorted ascending)

        # Also try from basic_info
        if basic_df is not None and not basic_df.empty:
            bp = self._safe_float(basic_df.iloc[0].get("close"))
            if bp is not None:
                current_price = bp

        if current_price is None:
            return None

        # Current price percentile
        below_count = sum(1 for c in closes if c < current_price)
        current_percentile = below_count / nn * 100

        # Key percentile prices
        sorted_closes = sorted(closes)

        def _percentile_price(pct: float) -> float:
            idx = int(pct / 100 * (nn - 1))
            return sorted_closes[min(idx, nn - 1)]

        rows = [
            ["10年数据点数", str(nn)],
            ["当前股价", f"{current_price:.2f}"],
            ["当前股价历史分位", f"{current_percentile:.1f}%"],
            ["10%分位价格", f"{_percentile_price(10):.2f}"],
            ["25%分位价格", f"{_percentile_price(25):.2f}"],
            ["50%分位价格（中位数）", f"{_percentile_price(50):.2f}"],
            ["75%分位价格", f"{_percentile_price(75):.2f}"],
            ["90%分位价格", f"{_percentile_price(90):.2f}"],
        ]

        table = format_table(["指标", "值"], rows, alignments=["l", "r"])
        lines.append(table)
        return "\n".join(lines)

    def _compute_sotp_inputs(self) -> str | None:
        """Compute §17.7: SOTP holding company structure inputs from parent/consolidated BS."""
        bs_df = self._get_annual_df("balance_sheet")
        bs_parent_df = self._get_annual_df("balance_sheet_parent")

        if bs_df.empty or bs_parent_df.empty:
            return None

        lines = [format_header(3, "17.7 控股结构辅助"), ""]

        latest_consol = bs_df.iloc[0]
        latest_parent = bs_parent_df.iloc[0]

        def _debt(row):
            components = ["st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y"]
            total = 0.0
            for c in components:
                v = self._safe_float(row.get(c))
                if v:
                    total += v
            return total

        consol_debt = _debt(latest_consol)
        parent_debt = _debt(latest_parent)
        consol_cash = self._safe_float(latest_consol.get("money_cap")) or 0
        parent_cash = self._safe_float(latest_parent.get("money_cap")) or 0

        rows = [
            ["有息负债", format_number(consol_debt), format_number(parent_debt)],
            ["现金", format_number(consol_cash), format_number(parent_cash)],
            ["净现金", format_number(consol_cash - consol_debt), format_number(parent_cash - parent_debt)],
        ]

        if consol_debt > 0:
            sub_ratio = (consol_debt - parent_debt) / consol_debt * 100
            rows.append(["子公司层面负债占比", f"{sub_ratio:.1f}%", "—"])

        table = format_table(["指标", "合并口径", "母公司口径"], rows,
                             alignments=["l", "r", "r"])
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #94: §17.8 EV baseline + "买入就是胜利"基准价 ---

    def _compute_factor4_ev_baseline(self, ts_code: str) -> str | None:
        """Compute §17.8: Valuation dashboard + floor-price baseline.

        Requires basic_info in _store (provides close, total_mv, total_share).
        All amounts in 百万元 unless stated otherwise.
        """
        basic_df = self._store.get("basic_info")
        if basic_df is None or basic_df.empty:
            return None

        bi = basic_df.iloc[0]
        close = self._safe_float(bi.get("close"))
        total_mv_wan = self._safe_float(bi.get("total_mv"))  # 万元
        total_share_wan = self._safe_float(bi.get("total_share"))  # 万股

        if not close or not total_mv_wan or not total_share_wan or total_share_wan <= 0:
            return None

        # Convert to common units
        mkt_cap_yuan = total_mv_wan * 10000  # yuan
        mkt_cap = mkt_cap_yuan / 1e6  # 百万元
        total_shares = total_share_wan * 10000  # 股

        # --- Gather data from _store ---
        income_df = self._get_annual_df("income")
        bs_df = self._get_annual_df("balance_sheet")
        cf_df = self._get_annual_df("cashflow")

        if income_df.empty or bs_df.empty or cf_df.empty:
            return None

        latest_inc = income_df.iloc[0]
        latest_bs = bs_df.iloc[0]
        latest_cf = cf_df.iloc[0]

        # Helper: interest-bearing debt components (yuan)
        def _ibd_yuan(row):
            total = 0.0
            for c in ["st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y"]:
                v = self._safe_float(row.get(c))
                if v:
                    total += v
            return total

        ibd_yuan = _ibd_yuan(latest_bs)
        cash_yuan = self._safe_float(latest_bs.get("money_cap")) or 0
        trad_yuan = self._safe_float(latest_bs.get("trad_asset")) or 0
        goodwill_yuan = self._safe_float(latest_bs.get("goodwill")) or 0
        total_assets_yuan = self._safe_float(latest_bs.get("total_assets")) or 0
        equity_yuan = self._safe_float(latest_bs.get("total_hldr_eqy_exc_min_int")) or 0

        oper_profit_yuan = self._safe_float(latest_inc.get("operate_profit")) or 0
        finance_exp_yuan = self._safe_float(latest_inc.get("finance_exp")) or 0
        np_parent_yuan = self._safe_float(latest_inc.get("n_income_attr_p")) or 0

        da_yuan = 0.0
        for c in ["depr_fa_coga_dpba", "amort_intang_assets", "lt_amort_deferred_exp"]:
            v = self._safe_float(latest_cf.get(c))
            if v:
                da_yuan += v

        ocf_yuan = self._safe_float(latest_cf.get("n_cashflow_act")) or 0
        capex_yuan = self._safe_float(latest_cf.get("c_pay_acq_const_fiolta")) or 0
        fcf_yuan = ocf_yuan - capex_yuan

        # Convert to 百万元
        ibd = ibd_yuan / 1e6
        cash = cash_yuan / 1e6
        trad = trad_yuan / 1e6
        goodwill = goodwill_yuan / 1e6
        ta = total_assets_yuan / 1e6
        equity = equity_yuan / 1e6
        oper_profit = oper_profit_yuan / 1e6
        fin_exp = finance_exp_yuan / 1e6
        np_parent = np_parent_yuan / 1e6
        da = da_yuan / 1e6
        fcf = fcf_yuan / 1e6

        # ===== Part A: Valuation indicators =====
        # Manual calculations (fallback)
        ebitda = oper_profit + fin_exp + da
        net_debt = ibd - cash  # positive = net debt, negative = net cash

        # Prefer fina_indicator pre-computed values when available
        fi_df = self._store.get("fina_indicators")
        if fi_df is not None and not fi_df.empty:
            fi_annual = fi_df[fi_df["end_date"].str.endswith("1231")].sort_values(
                "end_date", ascending=False)
            if not fi_annual.empty:
                fi_row = fi_annual.iloc[0]
                v = self._safe_float(fi_row.get("ebitda"))
                if v is not None:
                    ebitda = v / 1e6
                v = self._safe_float(fi_row.get("netdebt"))
                if v is not None:
                    net_debt = v / 1e6
                v = self._safe_float(fi_row.get("fcff"))
                if v is not None:
                    fcf = v / 1e6

        ev = mkt_cap + net_debt
        net_cash = -net_debt

        ev_ebitda = f"{ev / ebitda:.2f}x" if ebitda > 0 else "—"
        cash_pe = f"{(mkt_cap - net_cash) / np_parent:.2f}x" if np_parent > 0 else "—"
        fcf_yield = f"{fcf / mkt_cap * 100:.2f}%" if mkt_cap > 0 else "—"
        pb = f"{mkt_cap / equity:.2f}x" if equity > 0 else "—"
        net_debt_ebitda = f"{net_debt / ebitda:.2f}x" if ebitda > 0 else "—"
        goodwill_ratio = f"{goodwill / ta * 100:.2f}%" if ta > 0 else "—"
        ibd_ratio = f"{ibd / ta * 100:.2f}%" if ta > 0 else "—"

        # Dividend yield: latest DPS / close
        div_yield_str = "—"
        div_df = self._store.get("dividends")
        latest_dps = None
        if div_df is not None and not div_df.empty:
            sorted_div = div_df.sort_values("end_date", ascending=False)
            latest_dps = self._safe_float(sorted_div.iloc[0].get("cash_div_tax"))
            if latest_dps is not None and close > 0:
                div_yield_str = f"{latest_dps / close * 100:.2f}%"

        lines = [format_header(3, '17.8 因子4·绝对估值与"买入就是胜利"基准价'), ""]

        # Valuation table
        lines.append("#### 估值指标")
        lines.append("")
        fmt = lambda v: format_number(v, divider=1)
        val_rows = [
            ["总市值（百万元）", fmt(mkt_cap), "—"],
            ["企业价值 EV（百万元）", fmt(ev), "市值+有息负债-现金"],
            ["EBITDA（百万元）", fmt(ebitda), "营业利润+财务费用+D&A"],
            ["EV/EBITDA", ev_ebitda, "—"],
            ["扣除现金PE", cash_pe, "(市值-净现金)/归母净利润"],
            ["FCF收益率", fcf_yield, "FCF/市值"],
            ["P/B", pb, "市值/归母权益"],
            ["净负债/EBITDA", net_debt_ebitda, "(有息负债-现金)/EBITDA，负值=净现金"],
            ["商誉/总资产", goodwill_ratio, "—"],
            ["有息负债率", ibd_ratio, "有息负债/总资产"],
            ["股息率", div_yield_str, "最新DPS/当前股价"],
        ]
        lines.append(format_table(["指标", "值", "说明"], val_rows,
                                  alignments=["l", "r", "l"]))
        lines.append("")

        # ===== Part B: "买入就是胜利" baselines =====
        lines.append('#### "买入就是胜利"基准价')
        lines.append("")

        baselines = []  # (name, value_yuan_per_share, logic)

        # ① Net liquid assets / share
        nla = (cash_yuan + trad_yuan - ibd_yuan) / total_shares
        baselines.append(("① 净流动资产/股", nla, "(现金+交易性金融资产-有息负债)/总股本"))

        # ② BVPS
        bvps = equity_yuan / total_shares
        baselines.append(("② 每股净资产", bvps, "归母权益/总股本"))

        # ③ 10-year low from weekly prices
        wp_df = self._store.get("weekly_prices")
        if wp_df is not None and not wp_df.empty:
            min_close = wp_df["close"].dropna().min()
            if min_close is not None and min_close == min_close:  # NaN check
                baselines.append(("③ 10年最低价", float(min_close), "周线最低收盘价"))

        # ④ Dividend yield implied price: 3yr avg DPS / max(Rf, 3%)
        rf_df = self._store.get("risk_free_rate")
        rf_pct = None
        if rf_df is not None and not rf_df.empty:
            rf_pct = self._safe_float(rf_df.iloc[0].get("yield"))

        if div_df is not None and not div_df.empty and rf_pct is not None:
            sorted_div = div_df.sort_values("end_date", ascending=False)
            recent_dps = []
            for _, row in sorted_div.head(3).iterrows():
                v = self._safe_float(row.get("cash_div_tax"))
                if v is not None:
                    recent_dps.append(v)
            if recent_dps:
                avg_dps = sum(recent_dps) / len(recent_dps)
                discount = max(rf_pct / 100, 0.03)
                implied_price = avg_dps / discount
                baselines.append(("④ 股息隐含价", implied_price,
                                  f"3年均DPS÷max(Rf,3%)"))

        # ⑤ Pessimistic FCF capitalization: min(5yr FCF) / Rf / total_shares
        if rf_pct is not None and rf_pct > 0:
            fcf_list = []
            for _, row in cf_df.iterrows():
                ocf_v = self._safe_float(row.get("n_cashflow_act"))
                cap_v = self._safe_float(row.get("c_pay_acq_const_fiolta"))
                if ocf_v is not None and cap_v is not None:
                    fcf_list.append(ocf_v - cap_v)
            if fcf_list and min(fcf_list) <= 0:
                lines.append("> ⑤ 悲观FCF资本化：跳过（存在负FCF年份）")
                lines.append("")
            if fcf_list and min(fcf_list) > 0:
                min_fcf = min(fcf_list)
                cap_price = min_fcf / (rf_pct / 100) / total_shares
                baselines.append(("⑤ 悲观FCF资本化", cap_price,
                                  "min(5年FCF)÷Rf÷总股本"))

        # Build baseline table
        bl_rows = []
        valid_prices = []
        for name, val, logic in baselines:
            bl_rows.append([name, f"{val:.2f}", logic])
            valid_prices.append(val)

        lines.append(format_table(["方法", "基准价（元）", "计算逻辑"], bl_rows,
                                  alignments=["l", "r", "l"]))
        lines.append("")

        # ===== Part C: Composite baseline =====
        if valid_prices:
            composite = sum(valid_prices) / len(valid_prices)

            lines.append(f"**综合基准价（算术平均）= {composite:.2f} 元**")

            if len(valid_prices) < 3:
                lines.append("*数据不足（有效方法<3），仅供参考*")

            # ===== Part D: Premium analysis =====
            premium = (close / composite - 1) * 100
            lines.append(f"当前股价 {close:.2f} 元，较基准价溢价 **{premium:.1f}%**")

            if premium <= 0:
                verdict = "低于基准线 — 买入就是胜利"
            elif premium <= 30:
                verdict = "接近基准线 — 安全边际充足"
            elif premium <= 80:
                verdict = "合理溢价 — 需确认成长性"
            elif premium <= 150:
                verdict = "较高溢价 — 依赖持续成长"
            else:
                verdict = "显著溢价 — 高成长预期已定价"

            lines.append(f"→ {verdict}")

        return "\n".join(lines)

    # --- Feature #96: §17.9 Factor 4 earnings decline sensitivity ---

    def _compute_factor4_sensitivity(self, ts_code: str) -> str | None:
        """Compute §17.9: Earnings decline sensitivity tables.

        Shows how 穿透回报率 and 门槛价格 change under AA decline scenarios.
        Requires factor3_sensitivity (AA), basic_info (market cap, shares),
        risk_free_rate (II), dividends+income (M payout ratio).
        """
        # Read AA from factor3_sensitivity stored by _compute_factor3_sensitivity_base
        f3s = self._store.get("factor3_sensitivity")
        if not f3s:
            return None
        aa = f3s.get("aa_selected")
        if aa is None or aa == 0:
            return None

        # Read basic_info for market cap and total shares
        basic_df = self._store.get("basic_info")
        if basic_df is None or basic_df.empty:
            return None
        bi = basic_df.iloc[0]
        total_mv_wan = self._safe_float(bi.get("total_mv"))  # 万元
        total_share_wan = self._safe_float(bi.get("total_share"))  # 万股
        if not total_mv_wan or not total_share_wan or total_share_wan <= 0:
            return None
        mkt_cap = total_mv_wan * 10000  # 元（与 aa 同单位）
        total_shares = total_share_wan * 10000  # 股
        close = self._safe_float(bi.get("close"))  # 当前股价（元）

        # Read II (threshold) from risk_free_rate
        rf_df = self._store.get("risk_free_rate")
        if rf_df is None or rf_df.empty:
            return None
        rf_val = self._safe_float(rf_df.iloc[0].get("yield"))
        if rf_val is None:
            return None
        if ts_code.endswith(".HK"):
            ii = max(5.0, rf_val + 3.0)
        else:
            ii = max(3.5, rf_val + 2.0)

        # Read M (payout ratio) — uses _get_payout_by_year helper
        income_df = self._get_annual_df("income")
        payout_lookup = self._get_payout_by_year()
        years_labels = [str(r["end_date"])[:4] for _, r in income_df.iterrows()] if not income_df.empty else []
        payout_ratios = [payout_lookup[y] for y in years_labels[:3] if y in payout_lookup]
        m_pct = sum(payout_ratios) / len(payout_ratios) if payout_ratios else None
        if m_pct is None:
            return None

        # O = repurchase annual average (default 0, same as §17.2)
        o_val = 0.0

        # Base 穿透回报率
        gg_base = (aa * m_pct / 100 + o_val) / mkt_cap * 100  # percent
        threshold_price_base = (aa * m_pct / 100 + o_val) / (ii / 100 * total_shares)

        def _row(label: str, factor: float):
            aa_new = aa * factor
            gg = (aa_new * m_pct / 100 + o_val) / mkt_cap * 100
            vs_threshold = gg - ii
            tp = (aa_new * m_pct / 100 + o_val) / (ii / 100 * total_shares)
            vs_price = (tp / close - 1) * 100 if close and close > 0 else 0
            return [
                label,
                format_number(aa_new),
                f"{gg:.2f}%",
                f"{vs_threshold:+.2f} pct",
                f"{tp:.2f}",
                f"{vs_price:+.1f}%",
            ]

        lines = [format_header(3, "17.9 因子4·业绩下滑敏感性"), ""]
        lines.append(f"> AA（真实可支配现金结余）= {format_number(aa)} 百万元，"
                     f"M = {m_pct:.2f}%，O = {format_number(o_val)}，"
                     f"II = {ii:.2f}%，市值 = {format_number(mkt_cap)} 百万元")
        lines.append("")

        # Table 1: cumulative 10%/year decline over 1-3 years
        lines.append("#### 表1：逐年累积下滑（每年-10%）")
        lines.append("")
        headers1 = ["情景", "真实可支配现金结余", "穿透回报率", "vs 门槛", "门槛价格（元）", "vs当前股价"]
        rows1 = [
            _row("基准", 1.0),
            _row("下滑1年 (×0.9)", 0.9),
            _row("下滑2年 (×0.9²)", 0.81),
            _row("下滑3年 (×0.9³)", 0.729),
        ]
        lines.append(format_table(headers1, rows1, alignments=["l", "r", "r", "r", "r", "r"]))
        lines.append("")

        # Table 2: single-year different decline magnitudes
        lines.append("#### 表2：单年不同下滑幅度")
        lines.append("")
        headers2 = ["下滑幅度", "真实可支配现金结余", "穿透回报率", "门槛价格（元）", "vs当前股价"]
        rows2 = []
        for pct, factor in [("-10%", 0.9), ("-20%", 0.8), ("-30%", 0.7)]:
            r = _row(pct, factor)
            rows2.append([r[0], r[1], r[2], r[4], r[5]])
        lines.append(format_table(headers2, rows2, alignments=["l", "r", "r", "r", "r"]))

        return "\n".join(lines)

    # --- Feature #92: §17.3-17.5 Factor 3 base case computations ---

    def _compute_factor3_step1(self) -> str | None:
        """Compute §17.3: True cash revenue (步骤1).

        Conservative base case:
        - Deduct AR increases (revenue not yet collected as cash)
        - Deduct contract liability decreases (consumed pre-collected cash)
        - Do NOT add back AR decreases or CL increases (conservative)
        Stores results in self._store["_true_cash_rev"] for §17.5.
        """
        income_df = self._get_annual_df("income")
        bs_df = self._get_annual_df("balance_sheet")

        if income_df.empty or bs_df.empty or len(income_df) < 2:
            return None

        # Build year-indexed lookups from balance sheet
        bs_by_year = {}
        for _, r in bs_df.iterrows():
            year = str(r["end_date"])[:4]
            bs_by_year[year] = r

        # Income years (desc order)
        income_years = [str(r["end_date"])[:4] for _, r in income_df.iterrows()]

        # Compute changes — need year and prior year in BS
        results = []  # (year, S, T, U, true_cash_rev, collection_ratio) in raw yuan
        true_cash_rev_store = {}

        for i, year in enumerate(income_years):
            # Find prior year in income (next in list since desc)
            prior_year = str(int(year) - 1)
            if year not in bs_by_year or prior_year not in bs_by_year:
                continue

            bs_cur = bs_by_year[year]
            bs_prev = bs_by_year[prior_year]

            # S = revenue (raw yuan)
            filtered = income_df[income_df["end_date"].str.startswith(year)]
            if filtered.empty:
                continue
            s = self._safe_float(filtered.iloc[0].get("revenue"))
            if s is None:
                continue

            # T = AR change (increase positive)
            ar_cur = self._safe_float(bs_cur.get("accounts_receiv")) or 0
            ar_prev = self._safe_float(bs_prev.get("accounts_receiv")) or 0
            t = ar_cur - ar_prev

            # U = contract_liab change (increase positive)
            cl_cur = self._safe_float(bs_cur.get("contract_liab")) or 0
            cl_prev = self._safe_float(bs_prev.get("contract_liab")) or 0
            u = cl_cur - cl_prev

            # Conservative: deduct AR increases, deduct CL decreases
            true_cash = s - max(0, t) - max(0, -u)
            ratio = true_cash / s if s > 0 else None

            results.append((year, s, t, u, true_cash, ratio))
            true_cash_rev_store[year] = true_cash

        if not results:
            return None

        # Store for §17.5
        self._store["_true_cash_rev"] = true_cash_rev_store

        # Build output
        lines = [format_header(3, "17.3 因子3·步骤1 真实现金收入（保守基准）"), ""]
        lines.append("> AR增加扣除，CL增加不加回。LLM 可根据例外规则（如白酒预收）调整。")
        lines.append("")

        headers = ["年份", "S 营业收入", "T 应收变动", "U 合同负债变动",
                   "真实现金收入", "收款比率"]
        rows = []
        for year, s, t, u, tcr, ratio in results:
            rows.append([
                year,
                format_number(s),
                format_number(t),
                format_number(u),
                format_number(tcr),
                f"{ratio * 100:.2f}%" if ratio is not None else "—",
            ])
        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * 5)
        lines.append(table)

        # Null-value warnings for AR / contract_liab
        warnings = []
        for year, s, t, u, tcr, ratio in results:
            if year in bs_by_year:
                bs_cur = bs_by_year[year]
                prior_year = str(int(year) - 1)
                bs_prev = bs_by_year.get(prior_year)
                if bs_prev is not None:
                    ar_cur = self._safe_float(bs_cur.get("accounts_receiv"))
                    ar_prev = self._safe_float(bs_prev.get("accounts_receiv"))
                    if ar_cur is None and ar_prev is None and s > 0:
                        warnings.append(f"{year}: accounts_receiv 为空，AR变动=0 可能高估现金收入")
                    cl_cur = self._safe_float(bs_cur.get("contract_liab"))
                    cl_prev = self._safe_float(bs_prev.get("contract_liab"))
                    if cl_cur is None and cl_prev is None and s > 0:
                        warnings.append(f"{year}: contract_liab 为空，CL变动=0 可能影响现金收入")
        if warnings:
            lines.append("")
            for wm in warnings:
                lines.append(f"> ⚠️ {wm}")

        return "\n".join(lines)

    def _compute_factor3_step4(self) -> str | None:
        """Compute §17.4: Operating cash outflows (步骤4).

        W1 = oper_cost + max(0, -AP_change)
        W2 = c_pay_to_staff (from cashflow)
        W3 = income_tax - deferred_tax_net_change
        W4 = finance_exp
        Stores results in self._store["_w_total"] for §17.5.
        """
        income_df = self._get_annual_df("income")
        bs_df = self._get_annual_df("balance_sheet")
        cf_df = self._get_annual_df("cashflow")

        if income_df.empty or bs_df.empty or cf_df.empty or len(income_df) < 2:
            return None

        # Build lookups
        bs_by_year = {}
        for _, r in bs_df.iterrows():
            bs_by_year[str(r["end_date"])[:4]] = r
        cf_by_year = {}
        for _, r in cf_df.iterrows():
            cf_by_year[str(r["end_date"])[:4]] = r
        inc_by_year = {}
        for _, r in income_df.iterrows():
            inc_by_year[str(r["end_date"])[:4]] = r

        income_years = [str(r["end_date"])[:4] for _, r in income_df.iterrows()]

        results = []  # (year, W1, W2, W3, W4, W)
        w_total_store = {}

        for year in income_years:
            prior_year = str(int(year) - 1)
            if year not in bs_by_year or prior_year not in bs_by_year:
                continue
            if year not in cf_by_year or year not in inc_by_year:
                continue

            inc = inc_by_year[year]
            bs_cur = bs_by_year[year]
            bs_prev = bs_by_year[prior_year]
            cf = cf_by_year[year]

            # W1: supplier = oper_cost + max(0, -AP_change)
            oper_cost = self._safe_float(inc.get("oper_cost")) or 0
            ap_cur = self._safe_float(bs_cur.get("acct_payable")) or 0
            ap_prev = self._safe_float(bs_prev.get("acct_payable")) or 0
            ap_change = ap_cur - ap_prev
            w1 = oper_cost + max(0, -ap_change)

            # W2: employee = c_pay_to_staff (fallback to SGA if null)
            w2_raw = self._safe_float(cf.get("c_pay_to_staff"))
            w2_is_fallback = False
            if w2_raw is None or w2_raw == 0:
                # Fallback: SGA from income statement as proxy
                selling = self._safe_float(inc.get("sell_exp")) or 0
                admin = self._safe_float(inc.get("admin_exp")) or 0
                rd = self._safe_float(inc.get("rd_exp")) or 0
                w2 = selling + admin + rd
                w2_is_fallback = w2 > 0  # only mark fallback if SGA produced a value
            else:
                w2 = w2_raw

            # W3: cash tax = income_tax - (DTA_change - DTL_change)
            income_tax = self._safe_float(inc.get("income_tax")) or 0
            dta_cur = self._safe_float(bs_cur.get("defer_tax_assets")) or 0
            dta_prev = self._safe_float(bs_prev.get("defer_tax_assets")) or 0
            dtl_cur = self._safe_float(bs_cur.get("defer_tax_liab")) or 0
            dtl_prev = self._safe_float(bs_prev.get("defer_tax_liab")) or 0
            deferred_net_change = (dta_cur - dta_prev) - (dtl_cur - dtl_prev)
            w3 = income_tax - deferred_net_change

            # W4: interest = finance_exp
            w4 = self._safe_float(inc.get("finance_exp")) or 0

            w = w1 + w2 + w3 + w4
            results.append((year, w1, w2, w3, w4, w, w2_is_fallback))
            w_total_store[year] = w

        if not results:
            return None

        # Store for §17.5
        self._store["_w_total"] = w_total_store

        # Build output
        lines = [format_header(3, "17.4 因子3·步骤4 经营性现金支出"), ""]

        headers = ["年份", "W1 供应商", "W2 员工", "W3 现金税", "W4 利息", "W 合计"]
        rows = []
        has_w2_fallback = False
        for year, w1, w2, w3, w4, w, w2_fb in results:
            w2_display = format_number(w2)
            if w2_fb:
                w2_display += "†"
                has_w2_fallback = True
            rows.append([
                year,
                format_number(w1),
                w2_display,
                format_number(w3),
                format_number(w4),
                format_number(w),
            ])
        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * 5)
        lines.append(table)

        # Footnote for W2 fallback
        if has_w2_fallback:
            lines.append("")
            lines.append("> † W2: c_pay_to_staff 为空，已用利润表 SGA（销售+管理+研发费用）替代，偏保守。")

        # Null-value warnings
        warnings = []
        for year, w1, w2, w3, w4, w, w2_fb in results:
            inc = inc_by_year.get(year)
            cf = cf_by_year.get(year)
            if inc is not None:
                if (self._safe_float(inc.get("oper_cost")) or 0) == 0:
                    warnings.append(f"{year}: oper_cost 为空，W1 可能偏低")
                total_profit = self._safe_float(inc.get("total_profit")) or 0
                if (self._safe_float(inc.get("income_tax")) or 0) == 0 and total_profit > 0:
                    warnings.append(f"{year}: income_tax 为空但利润总额>0，W3 可能偏低")
        if warnings:
            lines.append("")
            for wm in warnings:
                lines.append(f"> ⚠️ {wm}")

        return "\n".join(lines)

    def _compute_factor3_sensitivity_base(self) -> str | None:
        """Compute §17.5: Base surplus + sensitivity inputs.

        Base surplus = true_cash_revenue - W - Capex (per year, no V/X adjustments).
        Also computes: AA_incl, AA_excl, revenue CV, λ, λ reliability.
        Requires _compute_factor3_step1() and _compute_factor3_step4() to have run first.
        """
        true_cash_rev = self._store.get("_true_cash_rev")
        w_total = self._store.get("_w_total")
        if not true_cash_rev or not w_total:
            return None

        cf_df = self._get_annual_df("cashflow")
        income_df = self._get_annual_df("income")
        if cf_df.empty or income_df.empty:
            return None

        # Capex by year
        capex_by_year = {}
        for _, r in cf_df.iterrows():
            year = str(r["end_date"])[:4]
            capex_by_year[year] = self._safe_float(r.get("c_pay_acq_const_fiolta")) or 0

        # Revenue by year (for CV and λ)
        rev_by_year = {}
        for _, r in income_df.iterrows():
            year = str(r["end_date"])[:4]
            rev_by_year[year] = self._safe_float(r.get("revenue")) or 0

        # Compute base surplus per year (only years with all data)
        common_years = sorted(
            set(true_cash_rev.keys()) & set(w_total.keys()) & set(capex_by_year.keys()),
            reverse=True
        )
        if not common_years:
            return None

        surplus_data = []  # (year, tcr, w, capex, base_surplus)
        for year in common_years:
            tcr = true_cash_rev[year]
            w = w_total[year]
            capex = capex_by_year.get(year, 0)
            base = tcr - w - capex
            surplus_data.append((year, tcr, w, capex, base))

        surpluses = [s[4] for s in surplus_data]

        # AA_all: mean of all years (was aa_incl)
        aa_all = sum(surpluses) / len(surpluses)

        # AA_2y: mean of most recent 2 years (surplus_data sorted descending)
        aa_2y = sum(surpluses[:2]) / min(2, len(surpluses)) if surpluses else aa_all

        # AA_excl: exclude years where base_surplus < 0
        positive_surpluses = [s for s in surpluses if s >= 0]
        aa_excl = sum(positive_surpluses) / len(positive_surpluses) if positive_surpluses else aa_all

        # Default: use AA_2y; fallback to AA_all if <2 years of data
        aa_selected = aa_2y if len(surpluses) >= 2 else aa_all

        # Store AA values for downstream use (§17.9 sensitivity)
        self._store["factor3_sensitivity"] = {
            "aa_incl": aa_all,  # legacy key for backward compatibility
            "aa_all": aa_all,
            "aa_2y": aa_2y,
            "aa_excl": aa_excl,
            "aa_selected": aa_selected,
        }

        # Revenue CV (all available years, not just change-computed years)
        all_revenues = [rev_by_year[y] for y in sorted(rev_by_year.keys()) if rev_by_year[y] > 0]
        cv = None
        if len(all_revenues) >= 2:
            import statistics
            rev_mean = statistics.mean(all_revenues)
            rev_stdev = statistics.pstdev(all_revenues)  # population stdev
            cv = rev_stdev / rev_mean if rev_mean > 0 else None

        # λ: median(ΔSurplus/ΔRevenue) over latest 3 year-pairs
        lambda_vals = []
        sorted_years_asc = sorted(common_years)
        for i in range(1, len(sorted_years_asc)):
            y_cur = sorted_years_asc[i]
            y_prev = sorted_years_asc[i - 1]
            delta_s = rev_by_year.get(y_cur, 0) - rev_by_year.get(y_prev, 0)
            surplus_cur = next((s[4] for s in surplus_data if s[0] == y_cur), None)
            surplus_prev = next((s[4] for s in surplus_data if s[0] == y_prev), None)
            if surplus_cur is not None and surplus_prev is not None and delta_s != 0:
                delta_surplus = surplus_cur - surplus_prev
                lambda_vals.append(delta_surplus / delta_s)

        # Use latest 3 pairs
        lambda_vals = lambda_vals[-3:] if len(lambda_vals) > 3 else lambda_vals
        import statistics
        lambda_median = statistics.median(lambda_vals) if lambda_vals else None

        # λ reliability checks
        lambda_warnings = []
        if len(all_revenues) >= 3:
            # Check 1: revenue amplitude over years used for λ
            lambda_rev_years = sorted(common_years)
            lambda_revs = [rev_by_year.get(y, 0) for y in lambda_rev_years if rev_by_year.get(y, 0) > 0]
            if lambda_revs and min(lambda_revs) > 0:
                amplitude = max(lambda_revs) / min(lambda_revs) - 1
                if amplitude < 0.10:
                    lambda_warnings.append("历史收入波幅不足10%，λ外推可靠性低")

        # Check 2: sign consistency
        if lambda_vals:
            signs = [1 if v >= 0 else -1 for v in lambda_vals]
            if len(set(signs)) > 1:
                lambda_warnings.append("ΔSurplus/ΔRevenue符号不一致，成本结构可能变化")

        # Check 3: λ range
        if lambda_median is not None and (lambda_median > 3 or lambda_median < 0):
            lambda_warnings.append(f"λ={lambda_median:.2f}异常，建议人工核查")

        lambda_reliability = "正常"
        if len(lambda_warnings) >= 2 or (lambda_median is not None and (lambda_median > 3 or lambda_median < 0)):
            lambda_reliability = "多项警告或异常"
        elif len(lambda_warnings) == 1:
            lambda_reliability = "有一项警告"

        # Build output
        lines = [format_header(3, "17.5 因子3·步骤7 基准可支配结余 + 敏感性输入"), ""]
        lines.append("> 不含 V1/V5/-V_deduct/-X1/-X2 调整。LLM 需在此基础上加减调整项。")
        lines.append("")

        # Per-year table
        headers = ["年份", "真实现金收入", "- W 经营支出", "- E 资本开支", "= 基准结余"]
        rows = []
        for year, tcr, w, capex, base in surplus_data:
            rows.append([
                year,
                format_number(tcr),
                format_number(w),
                format_number(capex),
                format_number(base),
            ])
        table = format_table(headers, rows, alignments=["l"] + ["r"] * 4)
        lines.append(table)
        lines.append("")

        # Summary
        lines.append(f"- AA_2y（近2年均值，默认基准）= {format_number(aa_2y)} 百万元")
        lines.append(f"- AA_all（全部年份均值）= {format_number(aa_all)} 百万元")
        lines.append(f"- AA_excl（剔除负值年份均值）= {format_number(aa_excl)} 百万元")
        diff_2y_all_pct = abs(aa_2y - aa_all) / abs(aa_all) * 100 if aa_all != 0 else 0
        if diff_2y_all_pct > 30:
            lines.append(f"  ⚠️ AA_2y 与 AA_all 差异 {diff_2y_all_pct:.1f}% > 30%，请审核近2年是否存在非经常性高峰")
        lines.append(f"- 收入波动率 CV = {cv * 100:.2f}%" if cv is not None else "- 收入波动率 CV = —")
        lines.append(f"- 经营杠杆系数 λ = {lambda_median:.4f}" if lambda_median is not None else "- 经营杠杆系数 λ = —")
        lines.append(f"- λ可靠性 = {lambda_reliability}")
        for w_msg in lambda_warnings:
            lines.append(f"  ⚠️ {w_msg}")

        # Capex null-value warnings
        capex_warnings = []
        for _, r in cf_df.iterrows():
            year = str(r["end_date"])[:4]
            if year in common_years:
                if self._safe_float(r.get("c_pay_acq_const_fiolta")) is None:
                    capex_warnings.append(f"{year}: capex（c_pay_acq_const_fiolta）为空，基准结余可能偏高")
        if capex_warnings:
            lines.append("")
            for wm in capex_warnings:
                lines.append(f"> ⚠️ {wm}")

        # AA vs OCF cross-validation
        ocf_values = []
        for _, r in cf_df.iterrows():
            year = str(r["end_date"])[:4]
            if year in [s[0] for s in surplus_data]:
                ocf = self._safe_float(r.get("n_cashflow_act"))
                if ocf is not None:
                    ocf_values.append(ocf)
        if ocf_values:
            ocf_avg = sum(ocf_values) / len(ocf_values)
            if aa_selected > 0 and ocf_avg > 0 and aa_selected / ocf_avg > 2.0:
                lines.append("")
                lines.append(
                    f"> ⚠️ AA/OCF = {aa_selected / ocf_avg:.1f}x，"
                    f"基准结余远超经营现金流（均值 {format_number(ocf_avg)} 百万元），"
                    f"可能存在数据缺失导致 W 偏低"
                )

        return "\n".join(lines)

    def compute_derived_metrics(self, ts_code: str) -> str:
        """Compute §17: Derived metrics from stored DataFrames.

        Must be called after all get_* methods have populated self._store.
        """
        lines = [
            format_header(2, "17. 衍生指标（Python 预计算）"),
            "",
            "> 以下指标基于 §1-§16 原始数据确定性计算，无 LLM 判断成分。Phase 3 可直接引用。百万元。",
            "",
        ]

        sub_methods = [
            self._compute_financial_trends,
            lambda: self._compute_factor2_inputs(ts_code),
            self._compute_factor3_step1,
            self._compute_factor3_step4,
            self._compute_factor3_sensitivity_base,
            self._compute_factor4_inputs,
            self._compute_sotp_inputs,
            lambda: self._compute_factor4_ev_baseline(ts_code),
            lambda: self._compute_factor4_sensitivity(ts_code),
        ]

        for method in sub_methods:
            try:
                result = method()
                if result:
                    lines.append(result)
                    lines.append("")
            except Exception as e:
                name = getattr(method, "__name__", str(method))
                lines.append(f"*{name} 计算失败: {e}*")
                lines.append("")

        return "\n".join(lines)

    def _build_section_13(self, ts_code: str, wc: "WarningsCollector") -> str:
        """Build §13 风险警示 section with auto-detection and agent placeholder."""
        lines = [format_header(2, "13. 风险警示"), ""]
        
        try:
            if self._is_hk(ts_code):
                for label, store_key in [
                    ("合并利润表", "income"),
                    ("合并资产负债表", "balance_sheet"),
                    ("现金流量表", "cashflow"),
                ]:
                    stored = self._store.get(store_key)
                    wc.check_missing_data(label, stored if stored is not None else pd.DataFrame())
            else:
                for label, api, fields in [
                    ("合并利润表", "income", "ts_code,end_date,revenue,n_income_attr_p"),
                    ("合并资产负债表", "balancesheet", "ts_code,end_date,total_assets"),
                    ("现金流量表", "cashflow", "ts_code,end_date,n_cashflow_act"),
                ]:
                    df = self._safe_call(api, ts_code=ts_code, fields=fields)
                    wc.check_missing_data(label, df)
                    if not df.empty and "end_date" in df.columns:
                        annual = df[df["end_date"].astype(str).str.endswith("1231")].copy()
                        annual = annual.sort_values("end_date", ascending=False)
                        annual["year"] = annual["end_date"].astype(str).str[:4]
                        annual = annual.drop_duplicates(subset=["year"], keep="first")
                        if not annual.empty:
                            dates = annual["year"].tolist()
                            for col in fields.split(",")[2:]:
                                if col in annual.columns:
                                    values = [v / 1e6 if v is not None and not pd.isna(v) else v for v in annual[col].tolist()]
                                    wc.check_yoy_change(label, col, values, dates=dates)

                audit_df = self._safe_call("fina_audit", ts_code=ts_code,
                                           fields="ts_code,end_date,audit_agency,audit_result")
                if not audit_df.empty and "audit_result" in audit_df.columns:
                    wc.check_audit_risk(str(audit_df.iloc[0].get("audit_result", "")))

            bs_df = self._store.get("balance_sheet") if self._is_hk(ts_code) else \
                self._safe_call("balancesheet", ts_code=ts_code,
                                fields="ts_code,end_date,goodwill,total_assets,total_liab")
            if bs_df is not None and not bs_df.empty:
                latest = bs_df.iloc[0]
                gw = latest.get("goodwill", 0) or 0
                ta = latest.get("total_assets", 0) or 0
                tl = latest.get("total_liab", 0) or 0
                wc.check_goodwill_ratio(float(gw), float(ta))
                wc.check_debt_ratio(float(tl), float(ta))
        except Exception:
            pass

        lines.append("### 13.1 脚本自动检测")
        lines.append("")
        
        lines.append("#### 检测阈值配置")
        lines.append("")
        lines.append("| 检测项目 | 阈值 | 说明 |")
        lines.append("|:---------|:-----|:-----|")
        lines.append(f"| 同比变化 | ≤{WarningThresholds.YOY_CHANGE_THRESHOLD*100:.0f}% | 近{WarningThresholds.YOY_MAX_YEARS}年数据 |")
        lines.append(f"| 商誉占比 | ≤{WarningThresholds.GOODWILL_RATIO_THRESHOLD*100:.0f}% | 商誉/总资产 |")
        lines.append(f"| 资产负债率 | ≤{WarningThresholds.DEBT_RATIO_THRESHOLD*100:.0f}% | 负债/总资产 |")
        lines.append(f"| 审计意见 | 标准无保留意见 | 最新年报 |")
        lines.append(f"| 数据完整性 | 非空 | 核心报表 |")
        lines.append("")
        
        lines.append("#### 检测结果汇总")
        lines.append("")
        if wc.check_results:
            passed = sum(1 for r in wc.check_results if r["passed"])
            total = len(wc.check_results)
            lines.append(f"共检测 {total} 项，通过 {passed} 项，异常 {total - passed} 项")
            lines.append("")
            
            lines.append("| 检测项 | 项目 | 年份对比 | 前值 | 当前值 | 阈值 | 实际值 | 结果 |")
            lines.append("|:-------|:-----|:---------|-----:|-------:|:-----|:-------|:----:|")
            for r in wc.check_results:
                status = "✓" if r["passed"] else "⚠️"
                period = r.get("period", "")
                curr_val = r.get("curr_value", "")
                prev_val = r.get("prev_value", "")
                
                if r["check"] == "同比变化" and curr_val != "" and prev_val != "":
                    item = r["item"]
                    try:
                        curr_fmt = f"{float(curr_val):,.2f}" if curr_val else "-"
                        prev_fmt = f"{float(prev_val):,.2f}" if prev_val else "-"
                    except:
                        curr_fmt = str(curr_val)
                        prev_fmt = str(prev_val)
                    lines.append(f"| {r['check']} | {item} | {period} | {prev_fmt} | {curr_fmt} | {r['threshold']} | {r['actual']} | {status} |")
                else:
                    item = f"{r['item']} ({period})" if period else r["item"]
                    lines.append(f"| {r['check']} | {item} | — | — | — | {r['threshold']} | {r['actual']} | {status} |")
            lines.append("")
        
        if wc.warnings:
            lines.append("#### 异常警告")
            lines.append("")
            high = [w for w in wc.warnings if w["severity"] == "高"]
            medium = [w for w in wc.warnings if w["severity"] == "中"]
            low = [w for w in wc.warnings if w["severity"] == "低"]
            for sev_label, items in [("高风险", high), ("中风险", medium), ("低风险", low)]:
                if items:
                    lines.append(f"**{sev_label}:**")
                    for w in items:
                        lines.append(f"- [{w['type']}|{w['severity']}] {w['message']}")
                    lines.append("")
        else:
            lines.append("**检测结果：未发现异常**")
            lines.append("")
            
        if self._is_hk(ts_code):
            lines.append("")
            lines.append("> 港股数据覆盖有限：§9业务构成/§15回购 暂缺，"
                         "§16质押不适用（港股无此制度），"
                         "§3P/§4P母公司报表在HKFRS体系下不适用，c_pay_to_staff 不可用。")
            lines.append("")

        lines.append("### 13.2 Agent WebSearch 补充")
        lines.append("")
        lines.append("> **必填风险类型清单**（每类至少1条）：")
        lines.append("> - [ ] 治理风险：管理层/实控人负面事件")
        lines.append("> - [ ] 周期风险：行业周期位置、供需变化")
        lines.append("> - [ ] 政策风险：监管政策变化")
        lines.append("> - [ ] 财务风险：偿债/流动性/应收等")
        lines.append("> - [ ] 估值风险：股价位置、估值水平")
        lines.append("")
        lines.append("| # | 类型 | 严重程度 | 描述 |")
        lines.append("|---|------|---------|------|")
        lines.append("| 1 | [治理风险\\|{高/中/低}] | {严重程度} | {描述} |")
        lines.append("| 2 | [周期风险\\|{高/中/低}] | {严重程度} | {描述} |")
        lines.append("| 3 | [政策风险\\|{高/中/低}] | {严重程度} | {描述} |")
        lines.append("| 4 | [财务风险\\|{高/中/低}] | {严重程度} | {描述} |")
        lines.append("| 5 | [估值风险\\|{高/中/低}] | {严重程度} | {描述} |")
        lines.append("")
        lines.append("> **说明**：以上风险信息来源于公开新闻报道、监管公告和财务数据分析，供Phase 3分析时参考。")
        
        return "\n".join(lines)

    # --- Feature #28: Full data_pack_market.md assembly ---

    def assemble_data_pack(self, ts_code: str) -> str:
        """Assemble complete data_pack_market.md combining all sections."""
        timestamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        currency = self._detect_currency(ts_code)
        unit_label = "百万港元" if currency == "HKD" else "百万元"
        lines = [
            format_header(1, f"数据包 — {ts_code}"),
            "",
            f"*生成时间: {timestamp}*",
            f"*数据来源: Tushare Pro*",
            f"*金额单位: {unit_label} (除特殊标注)*",
        ]
        if currency == "HKD":
            lines.append(f"*报表币种: HKD*")
        lines.extend(["", "---", ""])

        if self._is_hk(ts_code):
            sections = [
                ("1. 基本信息", self.get_basic_info),
                ("2. 市场行情", self.get_market_data),
                ("3. 合并利润表", self.get_income),
                # No §3P for HK (HKFRS does not split parent/consolidated)
                ("4. 合并资产负债表", self.get_balance_sheet),
                # No §4P for HK
                ("5. 现金流量表", self.get_cashflow),
                ("6. 分红历史", self.get_dividends),
                ("7. 股东与治理", self.get_holders),       # placeholder
                ("9. 主营业务构成", self.get_segments),      # placeholder
                ("11. 十年周线行情", self.get_weekly_prices),
                ("12. 关键财务指标", self.get_fina_indicators),
                ("15. 股票回购", self.get_repurchase),       # placeholder
                ("16. 股权质押", self.get_pledge_stat),      # placeholder
            ]
        else:
            sections = [
                ("1. 基本信息", self.get_basic_info),
                ("2. 市场行情", self.get_market_data),
                ("3. 合并利润表", self.get_income),
                ("3P. 母公司利润表", self.get_income_parent),
                ("4. 合并资产负债表", self.get_balance_sheet),
                ("4P. 母公司资产负债表", self.get_balance_sheet_parent),
                ("5. 现金流量表", self.get_cashflow),
                ("6. 分红历史", self.get_dividends),
                ("7. 股东与治理", self.get_holders),
                ("8. 行业与竞争", None),  # placeholder, handled separately
                ("9. 主营业务构成", self.get_segments),
                ("10. 管理层讨论与分析 (MD&A)", None),  # placeholder, handled separately
                ("11. 十年周线行情", self.get_weekly_prices),
                ("12. 关键财务指标", self.get_fina_indicators),
                ("13. 风险警示", None),  # auto-generated, handled separately
                ("14. 无风险利率", self.get_risk_free_rate),
                ("15. 股票回购", self.get_repurchase),
                ("16. 股权质押", self.get_pledge_stat),
            ]

        completed = 0
        wc = WarningsCollector()  # 初始化警告收集器，用于§13
        
        for name, method in sections:
            sec_num = name.split(".")[0]
            
            # §8 和 §10 是占位符section，由Agent WebSearch补充
            if sec_num in ("8", "10"):
                lines.append(format_header(2, name))
                lines.append(self._get_agent_placeholders(sec_num))
                lines.append("")
                completed += 1
                continue
            
            # §13 风险警示需要特殊处理
            if sec_num == "13":
                lines.append(self._build_section_13(ts_code, wc))
                lines.append("")
                completed += 1
                continue
            
            # 常规section处理
            if method is None:
                continue
            try:
                print(f"  Collecting {name}...")
                section_md = method(ts_code)
                lines.append(section_md)
                lines.append("")
                completed += 1
            except Exception as e:
                yf_data = self._yf_fallback_price(ts_code)
                if yf_data and name in ("1. 基本信息", "2. 市场行情"):
                    lines.append(format_header(2, name))
                    lines.append(f"\n*来源: yfinance (降级)*")
                    if yf_data.get("close"):
                        lines.append(f"- 当前价格: {yf_data['close']}")
                    if yf_data.get("market_cap"):
                        lines.append(f"- 总市值: {format_number(yf_data['market_cap'], divider=1e6)}")
                    lines.append("")
                    completed += 1
                else:
                    lines.append(format_header(2, name))
                    lines.append(f"\n数据获取失败: {e}\n")

        # §17 Derived metrics (pre-computed from stored DataFrames)
        try:
            print("  Computing 17. 衍生指标...")
            derived_md = self.compute_derived_metrics(ts_code)
            lines.append(derived_md)
            lines.append("")
        except Exception as e:
            lines.append(format_header(2, "17. 衍生指标（Python 预计算）"))
            lines.append(f"\n计算失败: {e}\n")

        lines.append("---")
        lines.append(f"*共 {completed}/{len(sections)} 个数据板块成功获取*")

        return "\n".join(lines)


class DataPackValidator:
    """Validate data_pack_market.md structure completeness after Agent WebSearch."""
    
    REQUIRED_STRUCTURE = {
        "## 7. 股东与治理": {
            "section_patterns": ["## 7. 股东与治理"],
            "subsections": [
                ("### 7.1", ["### 7.1 审计意见", "### 7.1 管理层信息"]),
                ("### 7.2", ["### 7.2 管理层信息", "### 7.2 历史发展脉络"]),
            ],
            "allow_placeholder": True,
        },
        "## 8. 行业与竞争": {
            "section_patterns": ["## 8. 行业与竞争"],
            "subsections": [
                ("### 8.1", ["### 8.1 产业链定位"]),
                ("### 8.2", ["### 8.2 竞争格局"]),
                ("### 8.3", ["### 8.3 行业技术路线"]),
                ("### 8.4", ["### 8.4 行业趋势"]),
                ("### 8.5", ["### 8.5 行业监管动态"]),
            ],
            "allow_placeholder": False,
        },
        "## 10. 管理层讨论与分析": {
            "section_patterns": ["## 10. 管理层讨论与分析", "## 10. MD&A"],
            "subsections": [
                ("### 10.1", ["### 10.1 经营回顾"]),
            ],
            "required_content": [
                "核心驱动因素",
            ],
            "allow_placeholder": False,
        },
        "### 13.2 Agent WebSearch 补充": {
            "required_content": [
                "[治理风险",
                "[周期风险",
                "[政策风险",
                "[财务风险",
                "[估值风险",
            ],
            "allow_placeholder": False,
        },
    }
    
    @classmethod
    def _find_section(cls, content: str, patterns: list) -> tuple:
        """Find section in content using multiple patterns.
        
        Returns:
            Tuple of (found_section_title, start_position) or (None, -1)
        """
        for pattern in patterns:
            pos = content.find(pattern)
            if pos != -1:
                return pattern, pos
        return None, -1
    
    @classmethod
    def validate(cls, content: str) -> list:
        """Validate data_pack_market.md structure completeness.
        
        Args:
            content: The full content of data_pack_market.md
            
        Returns:
            List of validation issues (empty if all checks pass)
        """
        issues = []
        
        for section, requirements in cls.REQUIRED_STRUCTURE.items():
            patterns = requirements.get("section_patterns", [section])
            found_section, section_start = cls._find_section(content, patterns)
            
            if section_start == -1:
                issues.append(f"缺少章节: {section}")
                continue
            
            next_section_match = content.find("\n## ", section_start + 1)
            if next_section_match == -1:
                next_section_match = len(content)
            section_content = content[section_start:next_section_match]
            
            if "subsections" in requirements:
                for subsection_tuple in requirements["subsections"]:
                    subsection_prefix, subsection_patterns = subsection_tuple
                    found = False
                    for pattern in subsection_patterns:
                        if pattern in section_content:
                            found = True
                            break
                    if not found:
                        issues.append(f"{section} 缺少子章节: {subsection_prefix}")
                    elif requirements.get("allow_placeholder", False):
                        placeholder = f"*[§{subsection_prefix.split()[0]} 待Agent WebSearch补充]"
                        if placeholder in section_content:
                            issues.append(f"{subsection_prefix} 仍为占位符，待Agent补充")
            
            if "required_content" in requirements:
                for req_content in requirements["required_content"]:
                    if req_content not in section_content:
                        issues.append(f"{section} 缺少必要内容: {req_content}")
        
        return issues
    
    @classmethod
    def validate_file(cls, filepath: str) -> list:
        """Validate a data_pack_market.md file.
        
        Args:
            filepath: Path to the data_pack_market.md file
            
        Returns:
            List of validation issues (empty if all checks pass)
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            return cls.validate(content)
        except FileNotFoundError:
            return [f"文件不存在: {filepath}"]
        except Exception as e:
            return [f"读取文件失败: {e}"]


class WarningThresholds:
    """可配置的警告阈值参数"""
    
    YOY_CHANGE_THRESHOLD = 3.0
    YOY_MAX_YEARS = 5
    GOODWILL_RATIO_THRESHOLD = 0.20
    DEBT_RATIO_THRESHOLD = 0.70
    
    @classmethod
    def set_thresholds(cls, yoy_change: float = None, yoy_max_years: int = None,
                       goodwill_ratio: float = None, debt_ratio: float = None):
        """动态设置阈值参数"""
        if yoy_change is not None:
            cls.YOY_CHANGE_THRESHOLD = yoy_change
        if yoy_max_years is not None:
            cls.YOY_MAX_YEARS = yoy_max_years
        if goodwill_ratio is not None:
            cls.GOODWILL_RATIO_THRESHOLD = goodwill_ratio
        if debt_ratio is not None:
            cls.DEBT_RATIO_THRESHOLD = debt_ratio


class WarningsCollector:
    """Auto-detect anomalies during data collection (Feature #30)."""

    def __init__(self):
        self.warnings = []
        self.check_results = []

    def check_missing_data(self, section_name: str, df: pd.DataFrame):
        """Warn if a data section returned empty."""
        is_empty = df is None or (isinstance(df, pd.DataFrame) and df.empty)
        self.check_results.append({
            "check": "数据完整性",
            "item": section_name,
            "threshold": "非空",
            "actual": "空" if is_empty else "非空",
            "passed": not is_empty,
        })
        if is_empty:
            self.warnings.append({
                "type": "DATA_MISSING",
                "severity": "中",
                "message": f"{section_name} 数据缺失",
            })

    def check_yoy_change(self, section_name: str, field_name: str,
                         values: list, threshold: float = None,
                         dates: list = None, max_years: int = None):
        """Warn if year-over-year change exceeds threshold.
        
        Args:
            max_years: Only check the most recent N years.
                       Set to 0 or negative to check all years.
        """
        if threshold is None:
            threshold = WarningThresholds.YOY_CHANGE_THRESHOLD
        if max_years is None:
            max_years = WarningThresholds.YOY_MAX_YEARS
            
        limit = len(values) - 1 if max_years <= 0 else min(len(values) - 1, max_years)
        for i in range(limit):
            curr, prev = values[i], values[i + 1]
            curr_date = dates[i] if dates and i < len(dates) else ""
            prev_date = dates[i + 1] if dates and i + 1 < len(dates) else ""
            
            if prev is not None and curr is not None and float(prev) != 0:
                try:
                    change = abs(float(curr) / float(prev) - 1)
                    period = f"{prev_date}→{curr_date}" if curr_date and prev_date else ""
                    
                    self.check_results.append({
                        "check": "同比变化",
                        "item": f"{section_name}/{field_name}",
                        "threshold": f"≤{threshold*100:.0f}%",
                        "actual": f"{change*100:.1f}%",
                        "period": period,
                        "curr_value": curr,
                        "prev_value": prev,
                        "curr_year": curr_date,
                        "prev_year": prev_date,
                        "passed": change <= threshold,
                    })
                    if change > threshold:
                        self.warnings.append({
                            "type": "YOY_ANOMALY",
                            "severity": "高",
                            "message": f"{section_name}/{field_name}: "
                                       f"{period} 同比变化 {change*100:.0f}% 超过 {threshold*100:.0f}% 阈值",
                        })
                except (ValueError, ZeroDivisionError):
                    pass

    def check_audit_risk(self, audit_opinion: str):
        """Warn if audit opinion is not clean."""
        is_clean = audit_opinion in ("标准无保留意见", "—", "", None)
        self.check_results.append({
            "check": "审计意见",
            "item": "年报审计",
            "threshold": "标准无保留意见",
            "actual": audit_opinion if audit_opinion else "未知",
            "passed": is_clean,
        })
        if not is_clean:
            self.warnings.append({
                "type": "AUDIT_RISK",
                "severity": "高",
                "message": f"审计意见非标准: {audit_opinion}",
            })

    def check_goodwill_ratio(self, goodwill: float, total_assets: float):
        """Warn if goodwill/total_assets exceeds threshold."""
        threshold = WarningThresholds.GOODWILL_RATIO_THRESHOLD
        if goodwill and total_assets and total_assets > 0:
            ratio = float(goodwill) / float(total_assets)
            self.check_results.append({
                "check": "商誉占比",
                "item": "商誉/总资产",
                "threshold": f"≤{threshold*100:.0f}%",
                "actual": f"{ratio*100:.2f}%",
                "passed": ratio <= threshold,
            })
            if ratio > threshold:
                self.warnings.append({
                    "type": "GOODWILL_RISK",
                    "severity": "高",
                    "message": f"商誉占总资产比例 {ratio*100:.1f}% 超过 {threshold*100:.0f}%",
                })

    def check_debt_ratio(self, total_liab: float, total_assets: float):
        """Warn if debt ratio exceeds threshold."""
        threshold = WarningThresholds.DEBT_RATIO_THRESHOLD
        if total_liab and total_assets and total_assets > 0:
            ratio = float(total_liab) / float(total_assets)
            self.check_results.append({
                "check": "资产负债率",
                "item": "负债/总资产",
                "threshold": f"≤{threshold*100:.0f}%",
                "actual": f"{ratio*100:.2f}%",
                "passed": ratio <= threshold,
            })
            if ratio > threshold:
                self.warnings.append({
                    "type": "LEVERAGE_RISK",
                    "severity": "中",
                    "message": f"资产负债率 {ratio*100:.1f}% 超过 {threshold*100:.0f}%",
                })

    def format_warnings(self) -> str:
        """Format all collected warnings as section 13 markdown."""
        lines = [format_header(2, "13. 风险警示 (脚本自动生成)"), ""]

        if not self.warnings:
            lines.append("未检测到异常。")
            return "\n".join(lines)

        high = [w for w in self.warnings if w["severity"] == "高"]
        medium = [w for w in self.warnings if w["severity"] == "中"]
        low = [w for w in self.warnings if w["severity"] == "低"]

        if high:
            lines.append("**高风险:**")
            for w in high:
                lines.append(f"- [{w['type']}] {w['message']}")
            lines.append("")
        if medium:
            lines.append("**中风险:**")
            for w in medium:
                lines.append(f"- [{w['type']}] {w['message']}")
            lines.append("")
        if low:
            lines.append("**低风险:**")
            for w in low:
                lines.append(f"- [{w['type']}] {w['message']}")
            lines.append("")

        lines.append(f"*共 {len(self.warnings)} 条自动警示*")
        return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect financial data from Tushare Pro API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --code 600887.SH
  %(prog)s --code 600887 --output output/data_pack_market.md
  %(prog)s --code 00700.HK --extra-fields balancesheet.defer_tax_assets
  %(prog)s --validate output/data_pack_market.md
        """,
    )
    parser.add_argument(
        "--code",
        help="Stock code (e.g., 600887.SH, 000858.SZ, 00700.HK, or plain digits)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Tushare API token (defaults to TUSHARE_TOKEN env var)",
    )
    parser.add_argument(
        "--output",
        default="output/data_pack_market.md",
        help="Output file path (default: output/data_pack_market.md)",
    )
    parser.add_argument(
        "--extra-fields",
        nargs="*",
        help="Additional fields to fetch (format: endpoint.field_name)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed arguments and exit without calling API",
    )
    parser.add_argument(
        "--validate",
        metavar="FILE",
        help="Validate an existing data_pack_market.md file for structure completeness",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.validate:
        print(f"Validating {args.validate}...")
        issues = DataPackValidator.validate_file(args.validate)
        if issues:
            print(f"发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)
        else:
            print("校验通过，所有必填章节和内容完整。")
            return

    if not args.code:
        print("Error: --code is required when not using --validate", file=sys.stderr)
        sys.exit(1)

    try:
        ts_code = validate_stock_code(args.code)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("=== Dry Run ===")
        print(f"  Stock code: {args.code} -> {ts_code}")
        print(f"  Token: {'provided via --token' if args.token else 'from TUSHARE_TOKEN env'}")
        print(f"  Output: {args.output}")
        print(f"  Extra fields: {args.extra_fields or 'none'}")
        return

    token = args.token or get_token()
    client = TushareClient(token)

    print(f"Collecting data for {ts_code}...")
    data_pack = client.assemble_data_pack(ts_code)

    if args.extra_fields:
        extra_lines = ["\n", format_header(2, "附加字段"), ""]
        for field_spec in args.extra_fields:
            parts = field_spec.split(".", 1)
            if len(parts) != 2:
                extra_lines.append(f"- 无效字段格式: {field_spec} (应为 endpoint.field_name)")
                continue
            endpoint, field_name = parts
            try:
                df = client._safe_call(endpoint, ts_code=ts_code, fields=f"ts_code,end_date,{field_name}")
                if not df.empty:
                    extra_lines.append(f"**{endpoint}.{field_name}**:")
                    extra_lines.append(df.to_markdown(index=False))
                    extra_lines.append("")
                else:
                    extra_lines.append(f"- {endpoint}.{field_name}: 无数据")
            except Exception as e:
                extra_lines.append(f"- {endpoint}.{field_name}: 获取失败 ({e})")
        data_pack += "\n".join(extra_lines)

    import os
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(data_pack)
    print(f"Output written to {args.output}")
    print(f"File size: {os.path.getsize(args.output):,} bytes")


if __name__ == "__main__":
    main()
