#!/usr/bin/env python3
"""每日抓取台股指數與關注個股收盤資料（證交所／櫃買中心 OpenAPI）。
只用 Python 內建模組。輸出 data/latest.json 並累積 data/history.json。"""
import json, ssl, sys, urllib.request, datetime, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
TWSE = "https://openapi.twse.com.tw/v1"
TPEX = "https://www.tpex.org.tw/openapi/v1"
URLS = {
    "twse_stocks": [f"{TWSE}/exchangeReport/STOCK_DAY_ALL"],
    "twse_index": [f"{TWSE}/exchangeReport/MI_INDEX"],
    "tpex_stocks": [f"{TPEX}/tpex_mainboard_daily_close_quotes", f"{TPEX}/tpex_mainboard_quotes"],
    "tpex_index": [f"{TPEX}/tpex_index", f"{TPEX}/tpex_reward_index"],
}

def get(urls):
    """依序嘗試網址，回傳 (資料, 使用的網址)。"""
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT  # 證交所憑證在新版 Python 嚴格模式下會失敗
    last = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 tw-stock-tracker", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                rows = json.loads(r.read().decode("utf-8-sig"))
            if rows:
                print(f"[ok] {url} 共 {len(rows)} 筆；欄位：{list(rows[0].keys())}")
                return rows, url
            last = "空資料"
        except Exception as e:  # noqa
            last = repr(e)
        print(f"[失敗] {url}：{last}")
    return [], None

def pick(row, *names):
    for n in names:
        if n in row and str(row[n]).strip() not in ("", "--", "---"):
            return str(row[n]).strip()
    return None

def num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("+", "").replace("X", "").strip()
    try:
        return float(s)
    except ValueError:
        return None

def roc_date(s):
    """民國日期（1151117 或 115/11/17）或西元日期轉 YYYY-MM-DD。"""
    if not s:
        return None
    d = "".join(c for c in str(s) if c.isdigit())
    if len(d) == 7:
        return f"{int(d[:3]) + 1911}-{d[3:5]}-{d[5:7]}"
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None

def stock_row(row, code_keys, close_keys, change_keys, extra):
    close = num(pick(row, *close_keys))
    chg = num(pick(row, *change_keys))
    pct = None
    if close is not None and chg is not None and close - chg:
        pct = round(chg / (close - chg) * 100, 2)
    out = {"close": close, "change": chg, "pct": pct, "date": roc_date(pick(row, "Date", "日期"))}
    for k, keys in extra.items():
        out[k] = num(pick(row, *keys))
    return out

def parse_twse_stocks(rows, codes):
    res = {}
    for r in rows:
        c = pick(r, "Code", "證券代號")
        if c in codes:
            res[c] = stock_row(r, ["Code"], ["ClosingPrice", "收盤價"], ["Change", "漲跌價差"],
                               {"open": ["OpeningPrice"], "high": ["HighestPrice"], "low": ["LowestPrice"], "volume": ["TradeVolume"]})
    return res

def parse_tpex_stocks(rows, codes):
    res = {}
    for r in rows:
        c = pick(r, "SecuritiesCompanyCode", "Code", "代號")
        if c in codes:
            res[c] = stock_row(r, ["SecuritiesCompanyCode"], ["Close", "收盤"], ["Change", "漲跌"],
                               {"open": ["Open"], "high": ["High"], "low": ["Low"], "volume": ["TradingShares"]})
    return res

def parse_twse_index(rows):
    for r in rows:
        if pick(r, "指數", "Index") == "發行量加權股價指數":
            pts = num(pick(r, "漲跌點數"))
            sign = pick(r, "漲跌")
            if pts is not None and sign == "-":
                pts = -abs(pts)
            pct = num(pick(r, "漲跌百分比"))
            if pct is not None and pts is not None and pts < 0:
                pct = -abs(pct)
            return {"close": num(pick(r, "收盤指數")), "change": pts, "pct": pct, "date": roc_date(pick(r, "日期", "Date"))}
    return None

def parse_tpex_index(rows):
    """櫃買指數：取日期最新一筆。欄位名稱以實際回傳為準，容錯處理。"""
    best = None
    for r in rows:
        name = pick(r, "IndexName", "指數名稱", "Index")
        if name and "櫃" not in name and "TPEx" not in name and "OTC" not in name.upper():
            continue
        close = num(pick(r, "Close", "CloseIndex", "收市指數", "收盤指數", "TPExIndex", "Index_Close"))
        if close is None:
            continue
        chg = num(pick(r, "Change", "漲跌", "漲跌點數"))
        pct = num(pick(r, "ChangePercent", "漲跌幅", "漲跌百分比", "Percent"))
        if pct is None and chg is not None and close - chg:
            pct = round(chg / (close - chg) * 100, 2)
        cand = {"close": close, "change": chg, "pct": pct, "date": roc_date(pick(r, "Date", "日期"))}
        if best is None or (cand["date"] or "") >= (best["date"] or ""):
            best = cand
    return best

def main():
    wl = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
    twse_codes = {s["code"] for s in wl["stocks"] if s["market"] == "twse"}
    tpex_codes = {s["code"] for s in wl["stocks"] if s["market"] == "tpex"}
    stocks, indices, problems = {}, {}, []

    rows, _ = get(URLS["twse_stocks"]); stocks.update(parse_twse_stocks(rows, twse_codes))
    rows, _ = get(URLS["tpex_stocks"]); stocks.update(parse_tpex_stocks(rows, tpex_codes))
    rows, _ = get(URLS["twse_index"]); idx = parse_twse_index(rows)
    if idx: indices["TAIEX"] = idx
    else: problems.append("抓不到加權指數")
    rows, _ = get(URLS["tpex_index"]); idx = parse_tpex_index(rows)
    if idx: indices["TPEX"] = idx
    else: problems.append("抓不到櫃買指數")

    missing = sorted((twse_codes | tpex_codes) - {c for c, v in stocks.items() if v["close"] is not None})
    if missing:
        problems.append("抓不到股價：" + "、".join(missing))

    dates = [v["date"] for v in list(stocks.values()) + list(indices.values()) if v.get("date")]
    trade_date = max(dates) if dates else None
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    latest = {"trade_date": trade_date, "fetched_at": now.isoformat(timespec="seconds"),
              "indices": indices, "stocks": stocks, "problems": problems}

    DATA.mkdir(exist_ok=True)
    (DATA / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
    if trade_date:
        hp = DATA / "history.json"
        hist = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
        for key, v in list(indices.items()) + list(stocks.items()):
            if v.get("date") and v.get("close") is not None:  # 各項目依自己的資料日期入帳
                hist.setdefault(v["date"], {})[key] = v["close"]
        hist = dict(sorted(hist.items())[-260:])  # 保留約一年
        hp.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")

    print(f"交易日 {trade_date}｜指數 {len(indices)}/2｜個股 {len(stocks) - len(missing)}/{len(twse_codes | tpex_codes)}")
    for p in problems:
        print("[注意]", p)
    if not stocks and not indices:
        sys.exit("完全沒有抓到資料")

if __name__ == "__main__":
    main()
