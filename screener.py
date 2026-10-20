# -*- coding: utf-8 -*-
"""
台股篩選：右上角 + 季線上 + 千元以上（上市+上櫃）
條件：
  1. 股價 >= MIN_PRICE (預設 1000)
  2. 股價 > 季線(MA60)
  3. 右上角：近 HIGH_WITHIN 日內創過52週新高，且現價離52週高 <= NEAR_HIGH_PCT
輸出 Markdown 檔（代號可點擊開技術分析頁）。
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
MIN_PRICE = 1000
NEAR_HIGH_PCT = 0.05
HIGH_WITHIN = 20
AS_OF = os.environ.get("AS_OF_DATE", "").strip()
OUT_FILE = f"screener_{datetime.now():%Y%m%d}.md"

COLUMNS = ["市場", "產業", "代號", "名稱", "股價", "漲跌幅%", "一週漲跌幅%",
           "一個月漲跌幅%", "一季漲跌幅%", "月線(MA20)", "季線(MA60)",
           "股價位置", "5MA vs 60MA %", "20MA vs 60MA %"]


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
    """用官方 OpenAPI 先抓最新收盤價 >= MIN_PRICE 的代號，減少下載量；失敗回傳 None"""
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
    if len(df) < 250:
        return None
    close, high = df["Close"], df["High"]
    price = close.iloc[-1]
    if price < MIN_PRICE:
        return None

    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    if price <= ma60:                      # 季線上
        return None

    high52 = high.rolling(250, min_periods=200).max()
    made_high = (high.iloc[-HIGH_WITHIN:] >= high52.iloc[-HIGH_WITHIN:]).any()
    near_high = price >= high52.iloc[-1] * (1 - NEAR_HIGH_PCT)
    if not (made_high and near_high):      # 右上角
        return None

    def pct(n):
        return round((price / close.iloc[-1 - n] - 1) * 100, 2)

    return {
        "市場": info["market"], "產業": info["industry"], "代號": info["code"],
        "名稱": info["name"], "股價": round(price, 2),
        "漲跌幅%": pct(1), "一週漲跌幅%": pct(5), "一個月漲跌幅%": pct(20),
        "一季漲跌幅%": pct(60),
        "月線(MA20)": round(ma20, 2), "季線(MA60)": round(ma60, 2),
        "股價位置": f"距52週高 {round((price / high52.iloc[-1] - 1) * 100, 2)}%",
        "5MA vs 60MA %": round((ma5 / ma60 - 1) * 100, 2),
        "20MA vs 60MA %": round((ma20 / ma60 - 1) * 100, 2),
    }


# ===== 輸出 Markdown =====
def write_md(result):
    date_str = AS_OF or f"{datetime.now():%Y-%m-%d}"
    lines = [f"# 台股篩選結果（右上角 + 季線上 + 千元以上）",
             "",
             f"- 日期：{date_str}",
             f"- 條件：股價 ≥ {MIN_PRICE}；股價 > 季線(MA60)；{HIGH_WITHIN} 日內創52週新高且離高點 ≤ {NEAR_HIGH_PCT*100:.0f}%",
             f"- 符合：**{len(result)}** 檔（依 20MA vs 60MA % 由大到小）",
             ""]
    if result.empty:
        lines.append("今日無符合股票。")
    else:
        lines.append("| " + " | ".join(COLUMNS) + " |")
        lines.append("|" + "|".join(["---"] * len(COLUMNS)) + "|")
        for _, r in result.iterrows():
            vals = []
            for c in COLUMNS:
                v = r[c]
                if c == "代號":
                    v = f"[{v}](https://tw.stock.yahoo.com/quote/{v}/technical-analysis)"
                vals.append(str(v))
            lines.append("| " + " | ".join(vals) + " |")
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
        result = result.sort_values("20MA vs 60MA %", ascending=False)
    write_md(result)


if __name__ == "__main__":
    main()
