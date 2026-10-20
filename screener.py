# -*- coding: utf-8 -*-
"""
台股篩選：右上角 + 季線上 + 千元以上（上市+上櫃）
條件：
  1. 股價 >= MIN_PRICE (預設 1000)
  2. 股價 > 季線(MA60)
  3. 右上角：近 HIGH_WITHIN 日內創過52週新高，且現價離52週高 <= NEAR_HIGH_PCT
輸出 Excel（代號可點擊開技術分析頁），並用 Gmail 寄出。
環境變數：
  GMAIL_APP_PASSWORD  Gmail 應用程式密碼（寄信用，沒設就只存檔不寄）
  AS_OF_DATE          選填，YYYY-MM-DD，以該日收盤計算
"""
import os
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from io import StringIO

import pandas as pd
import requests
import yfinance as yf
from openpyxl import load_workbook

# ===== 參數 =====
MIN_PRICE = 1000
NEAR_HIGH_PCT = 0.05
HIGH_WITHIN = 20
MAIL_FROM = "Mileschen.working@gmail.com"
MAIL_TO = "tc8888.cc@gmail.com"
AS_OF = os.environ.get("AS_OF_DATE", "").strip()
OUT_FILE = f"screener_{datetime.now():%Y%m%d}.xlsx"

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


# ===== 輸出 / 寄信 =====
def save_excel(result):
    result.to_excel(OUT_FILE, index=False)
    wb = load_workbook(OUT_FILE)
    ws = wb.active
    code_col = COLUMNS.index("代號") + 1
    for row in range(2, ws.max_row + 1):
        c = ws.cell(row=row, column=code_col)
        c.hyperlink = f"https://tw.stock.yahoo.com/quote/{c.value}/technical-analysis"
        c.style = "Hyperlink"
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 14
    wb.save(OUT_FILE)


def send_mail(n):
    pwd = os.environ.get("GMAIL_APP_PASSWORD")
    if not pwd:
        print("未設定 GMAIL_APP_PASSWORD，略過寄信")
        return
    msg = EmailMessage()
    msg["From"], msg["To"] = MAIL_FROM, MAIL_TO
    msg["Subject"] = f"台股篩選（右上角+季線上+千元以上）{datetime.now():%Y-%m-%d}：{n} 檔"
    msg.set_content(f"符合條件共 {n} 檔，詳見附件 Excel（點代號可開技術分析頁）。")
    with open(OUT_FILE, "rb") as f:
        msg.add_attachment(f.read(), maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           filename=OUT_FILE)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(MAIL_FROM, pwd)
        s.send_message(msg)
    print("已寄出")


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
    print(result.to_string(index=False) if not result.empty else "今日無符合股票")
    save_excel(result)
    send_mail(len(result))


if __name__ == "__main__":
    main()
