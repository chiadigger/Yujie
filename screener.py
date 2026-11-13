# -*- coding: utf-8 -*-
"""
台股篩選：20/60/120MA 多頭排列 + 距52週高點不超過15%（上市+上櫃）
條件：
  1. 多頭排列：MA20 > MA60 > MA120
  2. 距52週(約250個交易日)高點跌幅 <= NEAR_HIGH_PCT (預設 15%)
  3. 排序：距52週高點越近排越前面（0% 最前）
輸出 README.md（repo 首頁直接顯示，代號可點擊開技術分析頁）。
環境變數：
  AS_OF_DATE          選填，YYYY-MM-DD，以該日收盤計算
"""
import os
import time
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

# ===== 參數 =====
MIN_PRICE = 0            # 股價下限，0 = 不限制（原本是 1000，想加回來就改這裡）
HIGH_DAYS = 250          # 52週 ≈ 250 個交易日
NEAR_HIGH_PCT = 0.15     # 距52週高點 15% 以內
MIN_BARS = 121           # 至少要有 121 根K棒才算得出 120MA
AS_OF = os.environ.get("AS_OF_DATE", "").strip()
OUT_FILE = "README.md"

COLUMNS = ["市場", "產業", "代號", "名稱", "股價", "漲跌幅%", "一週漲跌幅%",
           "一個月漲跌幅%", "一季漲跌幅%", "MA20", "MA60", "MA120",
           "52週高點", "距52週高點%"]


# ===== 股票清單 =====
def load_stock_list():
    """從證交所 ISIN 頁面取得上市/上櫃股票（4碼代號）"""
    rows = []
    for mode, market, suffix in [(2, "上市", ".TW"), (4, "上櫃", ".TWO")]:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        html = requests.get(url, timeout=60).content.decode("big5", errors="ignore")
        df = pd.read_html(StringIO(html))[0]
        df.columns = df.iloc[0]
        df = df.iloc[1:]
        for _, r in df.iterrows():
            first = str(r.iloc[0])
            if "\u3000" not in first:
                continue
            code, name = first.split("\u3000", 1)
            code = code.strip()
            if len(code) == 4 and code.isdigit():
                rows.append({"code": code, "name": name.strip(), "market": market,
                             "industry": str(r.get("產業別", "")).replace("nan", ""),
                             "ticker": code + suffix})
    return pd.DataFrame(rows).drop_duplicates("code")


def first_stage_codes():
    """MIN_PRICE > 0 時，用官方 OpenAPI 先篩收盤價，減少下載量；失敗回傳 None"""
    if MIN_PRICE <= 0:
        return None
    codes = set()
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=60).json()
        for x in r:
            try:
                if float(str(x.get("ClosingPrice", "")).replace(",", "")) >= MIN_PRICE * 0.9:
                    codes.add(x["Code"])
            except ValueError:
                pass
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", timeout=60).json()
        for x in r:
            try:
                if float(str(x.get("Close", "")).replace(",", "")) >= MIN_PRICE * 0.9:
                    codes.add(x["SecuritiesCompanyCode"])
            except ValueError:
                pass
        return codes if codes else None
    except Exception as e:
        print("OpenAPI 第一階段失敗，改用全部下載：", e)
        return None


# ===== 單檔計算 =====
def get_history(ticker):
    for _ in range(3):
        try:
            df = yf.Ticker(ticker).history(period="2y", auto_adjust=False)
            if not df.empty:
                df.index = df.index.tz_localize(None)
                if AS_OF:
                    df = df[df.index <= pd.Timestamp(AS_OF)]
                return df
        except Exception:
            pass
        time.sleep(2)
    return pd.DataFrame()


def evaluate(info):
    df = get_history(info["ticker"])
    if len(df) < MIN_BARS:
        return None
    close, high = df["Close"], df["High"]
    price = close.iloc[-1]
    if price < MIN_PRICE:
        return None

    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma120 = close.rolling(120).mean().iloc[-1]

    # 條件1：多頭排列 MA20 > MA60 > MA120
    if not (ma20 > ma60 > ma120):
        return None

    # 條件2：距52週高點不超過15%
    high52 = high.iloc[-HIGH_DAYS:].max()
    gap = price / high52 - 1            # 0 或負數
    if gap < -NEAR_HIGH_PCT:
        return None

    def pct(n):
        if len(close) <= n:
            return None
        return round((price / close.iloc[-1 - n] - 1) * 100, 2)

    return {
        "市場": info["market"], "產業": info["industry"], "代號": info["code"],
        "名稱": info["name"], "股價": round(price, 2),
        "漲跌幅%": pct(1), "一週漲跌幅%": pct(5), "一個月漲跌幅%": pct(20),
        "一季漲跌幅%": pct(60),
        "MA20": round(ma20, 2), "MA60": round(ma60, 2), "MA120": round(ma120, 2),
        "52週高點": round(high52, 2),
        "距52週高點%": round(gap * 100, 2),
    }


# ===== 輸出 Markdown =====
def table_lines(df):
    lines = ["| " + " | ".join(COLUMNS) + " |",
             "|" + "|".join(["---"] * len(COLUMNS)) + "|"]
    for _, r in df.iterrows():
        vals = []
        for c in COLUMNS:
            v = r[c]
            if c == "代號":
                v = f"[{v}](https://tw.stock.yahoo.com/quote/{v}/technical-analysis)"
            vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def write_md(result):
    date_str = AS_OF or f"{datetime.now():%Y-%m-%d}"
    price_note = f"；股價 ≥ {MIN_PRICE}" if MIN_PRICE > 0 else ""
    lines = ["# 台股篩選結果（20/60/120MA 多頭排列 + 52週高點附近）",
             "",
             f"- 日期：{date_str}",
             f"- 條件：MA20 > MA60 > MA120；距52週高點跌幅 ≤ {NEAR_HIGH_PCT*100:.0f}%{price_note}",
             "- 排序：距52週高點由近到遠（0% 最前）",
             f"- 共 **{len(result)}** 檔",
             ""]
    if result.empty:
        lines.append("今日無符合股票。")
    else:
        lines += table_lines(result)
        lines.append("")
    text = "\n".join(lines) + "\n"
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    # GitHub Actions 的執行摘要頁也顯示同一份結果
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    print(text)


def main():
    stocks = load_stock_list()
    print("股票總數：", len(stocks))
    codes = first_stage_codes()
    if codes is not None and not AS_OF:
        stocks = stocks[stocks["code"].isin(codes)]
    print("第一階段後：", len(stocks))

    out = []
    for _, info in stocks.iterrows():
        try:
            r = evaluate(info.to_dict())
            if r:
                out.append(r)
        except Exception as e:
            print("錯誤", info["code"], e)

    result = pd.DataFrame(out, columns=COLUMNS)
    if not result.empty:
        # 距52週高點越近越前面（0 > -1 > -5 ...）
        result = result.sort_values("距52週高點%", ascending=False)
    write_md(result)


if __name__ == "__main__":
    main()
