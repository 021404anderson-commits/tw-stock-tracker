#!/usr/bin/env python3
"""每日抓取台股指數與關注個股收盤資料（證交所／櫃買中心 OpenAPI）。
只用 Python 內建模組。輸出 data/latest.json 並累積 data/history.json。
加上 --intraday 參數時改抓盤中即時報價（證交所基本市況報導網站），只寫 data/intraday.json。"""
import json, re, ssl, sys, urllib.request, datetime, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
TWSE = "https://openapi.twse.com.tw/v1"
TPEX = "https://www.tpex.org.tw/openapi/v1"
URLS = {
    "twse_stocks": [f"{TWSE}/exchangeReport/STOCK_DAY_ALL"],
    # 開放資料平台的上市行情晚一天才更新，改以證交所官網報表為主（當天傍晚就有），開放資料當備援
    "twse_daily": ["https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999"],
    "twse_daily_index": ["https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=IND"],
    "twse_index": [f"{TWSE}/exchangeReport/MI_INDEX"],
    "tpex_stocks": [f"{TPEX}/tpex_mainboard_daily_close_quotes", f"{TPEX}/tpex_mainboard_quotes"],
    "tpex_index": [f"{TPEX}/tpex_index", f"{TPEX}/tpex_reward_index"],
    # 三大法人買賣超（單位：股）。證交所的開放資料平台沒有這張表，改用官網的 JSON 報表
    "twse_inst": ["https://www.twse.com.tw/rwd/zh/fund/T86?selectType=ALLBUT0999&response=json"],
    "tpex_inst": [f"{TPEX}/tpex_3insti_daily_trading"],
}

def get(urls, table=False):
    """依序嘗試網址，回傳 (資料, 使用的網址)。table=True 表示回傳的是 {fields, data} 形式的報表。"""
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT  # 證交所憑證在新版 Python 嚴格模式下會失敗
    last = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 tw-stock-tracker", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                rows = json.loads(r.read().decode("utf-8-sig"))
            if table == "tables":
                if rows.get("stat") == "OK" and rows.get("tables"):
                    print(f"[ok] {url} 日期 {rows.get('date')}")
                    return rows, url
            elif table:
                if rows.get("data") and rows.get("fields"):
                    print(f"[ok] {url} 共 {len(rows['data'])} 筆；日期 {rows.get('date')}")
                    return rows, url
            elif rows:
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
                               {"open": ["OpeningPrice"], "high": ["HighestPrice"], "low": ["LowestPrice"], "volume": ["TradeVolume"], "value": ["TradeValue"]})
    return res

def parse_tpex_stocks(rows, codes):
    res = {}
    for r in rows:
        c = pick(r, "SecuritiesCompanyCode", "Code", "代號")
        if c in codes:
            res[c] = stock_row(r, ["SecuritiesCompanyCode"], ["Close", "收盤"], ["Change", "漲跌"],
                               {"open": ["Open"], "high": ["High"], "low": ["Low"], "volume": ["TradingShares"], "value": ["TransactionAmount"]})
    return res

def find_table(js, first_field):
    for t in (js or {}).get("tables") or []:
        f = [str(x).strip() for x in t.get("fields") or []]
        if f and f[0] == first_field:
            yield f, t.get("data") or []

def signed(row, f, sign_col, pts_col):
    """官網報表的漲跌符號是 HTML（例如 <p style=color:green>-</p>），去掉標籤後判斷正負。"""
    pts = num(row[f.index(pts_col)]) if pts_col in f else None
    if pts is not None and sign_col in f and "-" in re.sub(r"<[^>]*>", "", str(row[f.index(sign_col)])):
        pts = -abs(pts)
    return pts

def parse_twse_daily(js, codes):
    res, date = {}, roc_date((js or {}).get("date"))
    for f, data in find_table(js, "證券代號"):
        col = lambda r, n: num(r[f.index(n)]) if n in f else None
        for r in data:
            c = str(r[0]).strip()
            if c in codes:
                close, chg = col(r, "收盤價"), signed(r, f, "漲跌(+/-)", "漲跌價差")
                pct = round(chg / (close - chg) * 100, 2) if close is not None and chg is not None and close - chg else None
                res[c] = {"close": close, "change": chg, "pct": pct, "date": date, "open": col(r, "開盤價"), "high": col(r, "最高價"),
                          "low": col(r, "最低價"), "volume": col(r, "成交股數"), "value": col(r, "成交金額")}
    return res

def parse_twse_daily_index(js):
    for f, data in find_table(js, "指數"):
        for r in data:
            if str(r[0]).strip() == "發行量加權股價指數":
                pts, pct = signed(r, f, "漲跌(+/-)", "漲跌點數"), num(r[f.index("漲跌百分比(%)")]) if "漲跌百分比(%)" in f else None
                if pct is not None and pts is not None and pts < 0:
                    pct = -abs(pct)
                return {"close": num(r[f.index("收盤指數")]), "change": pts, "pct": pct, "date": roc_date(js.get("date"))}
    return None

def newer(a, b):
    """兩份資料取日期較新的；一樣新時用 a。"""
    if not b or b.get("close") is None:
        return a
    if not a or a.get("close") is None or (b.get("date") or "") > (a.get("date") or ""):
        return b
    return a

def parse_twse_inst(tab, codes):
    """證交所三大法人買賣超日報：用欄位名稱找位置，避免欄位順序變動時取錯。"""
    res = {}
    if not tab:
        return res
    f = [str(x).strip() for x in tab["fields"]]
    date = roc_date(tab.get("date"))
    def col(row, name):
        return num(row[f.index(name)]) if name in f else None
    for r in tab["data"]:
        c = str(r[0]).strip()
        if c in codes:
            fo = [col(r, "外陸資買賣超股數(不含外資自營商)"), col(r, "外資自營商買賣超股數")]
            res[c] = {"date": date, "foreign": None if fo[0] is None else fo[0] + (fo[1] or 0),
                      "trust": col(r, "投信買賣超股數"), "dealer": col(r, "自營商買賣超股數"),
                      "total": col(r, "三大法人買賣超股數")}
    return res

def parse_tpex_inst(rows, codes):
    """櫃買三大法人買賣明細：官方欄位名稱夾雜不規則空白，先去掉空白再比對。"""
    res = {}
    for r in rows:
        k = {"".join(str(a).split()): v for a, v in r.items()}
        c = str(k.get("SecuritiesCompanyCode", "")).strip()
        if c in codes:
            res[c] = {"date": roc_date(k.get("Date")),
                      "foreign": num(k.get("ForeignInvestorsIncludeMainlandAreaInvestors-Difference")),
                      "trust": num(k.get("SecuritiesInvestmentTrustCompanies-Difference")),
                      "dealer": num(k.get("Dealers-Difference")), "total": num(k.get("TotalDifference"))}
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

MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0&ex_ch="

def intraday():
    """盤中即時報價：只寫 data/intraday.json，不動收盤歷史與族群排名用的資料。"""
    wl = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
    keys = {"tse_t00.tw": "TAIEX", "otc_o00.tw": "TPEX"}
    for s in wl["stocks"]:
        keys[("tse_" if s["market"] == "twse" else "otc_") + s["code"] + ".tw"] = s["code"]
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    req = urllib.request.Request(MIS + "|".join(keys), headers={"User-Agent": "Mozilla/5.0 tw-stock-tracker", "Referer": "https://mis.twse.com.tw/stock/index.jsp"})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        msgs = json.loads(r.read().decode("utf-8")).get("msgArray") or []
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    today = now.strftime("%Y-%m-%d")
    path = DATA / "intraday.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if prev.get("date") != today:
        prev = {}
    out = {"date": None, "time": None, "fetched_at": now.isoformat(timespec="seconds"), "indices": {}, "stocks": {}}
    for m in msgs:
        k = keys.get(f"{m.get('ex')}_{m.get('c')}.tw")
        if not k:
            continue
        d = roc_date(m.get("d"))
        if d != today:
            continue  # 非交易日時官方會回傳上一個交易日的資料，不採用
        y = num(m.get("y"))
        z = num(m.get("z"))  # 最近成交價；當下那一盤沒有成交時是「-」
        if z is None:
            z = num((m.get("trade") or {}).get("z"))  # 官方另附的「最後一筆實際成交」
        grp = "indices" if k in ("TAIEX", "TPEX") else "stocks"
        if z is None:
            z = (prev.get(grp, {}).get(k) or {}).get("close")  # 沿用今天稍早抓到的成交價，不用委買委賣價推估
        if z is None or y is None:
            continue
        v = {"close": z, "change": round(z - y, 2), "pct": round((z - y) / y * 100, 2) if y else None, "date": d,
             "open": num(m.get("o")), "high": num(m.get("h")), "low": num(m.get("l"))}
        if grp == "stocks" and num(m.get("v")) is not None:
            v["volume"] = num(m.get("v")) * 1000  # 官方單位是張，換成股，與收盤資料一致
        out[grp][k] = v
        out["date"] = d
        t = str(m.get("t") or "")
        if grp == "stocks" and t > (out["time"] or ""):  # 以個股成交時間為準（指數時間會晚幾分鐘）
            out["time"] = t
    if not out["date"]:
        print("今天沒有盤中資料（非交易日或尚未開盤），不更新")
        return
    DATA.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"盤中 {out['date']} {out['time']}｜指數 {len(out['indices'])}/2｜個股 {len(out['stocks'])}/{len(wl['stocks'])}")

def main():
    wl = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
    twse_codes = {s["code"] for s in wl["stocks"] if s["market"] == "twse"}
    tpex_codes = {s["code"] for s in wl["stocks"] if s["market"] == "tpex"}
    stocks, indices, problems = {}, {}, []

    rows, _ = get(URLS["twse_stocks"]); stocks.update(parse_twse_stocks(rows, twse_codes))
    js, _ = get(URLS["twse_daily"], table="tables")
    for c, v in parse_twse_daily(js, twse_codes).items():
        stocks[c] = newer(v, stocks.get(c))
    rows, _ = get(URLS["tpex_stocks"]); stocks.update(parse_tpex_stocks(rows, tpex_codes))
    rows, _ = get(URLS["twse_index"]); idx = parse_twse_index(rows)
    js, _ = get(URLS["twse_daily_index"], table="tables"); idx = newer(parse_twse_daily_index(js), idx)
    if idx: indices["TAIEX"] = idx
    else: problems.append("抓不到加權指數")
    rows, _ = get(URLS["tpex_index"]); idx = parse_tpex_index(rows)
    if idx: indices["TPEX"] = idx
    else: problems.append("抓不到櫃買指數")

    inst = {}
    tab, _ = get(URLS["twse_inst"], table=True); inst.update(parse_twse_inst(tab, twse_codes))
    rows, _ = get(URLS["tpex_inst"]); inst.update(parse_tpex_inst(rows, tpex_codes))
    for c, v in inst.items():
        if c in stocks and v.get("total") is not None:
            stocks[c]["inst"] = v
    no_inst = sorted(c for c in stocks if "inst" not in stocks[c])
    if no_inst:
        problems.append("抓不到三大法人買賣超：" + "、".join(no_inst))

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
        # 資金流向歷史：每日成交金額 v（元）與三大法人買賣超 i（股），各依自己的資料日期入帳
        fp = DATA / "flow.json"
        flow = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
        for c, v in stocks.items():
            if v.get("date") and v.get("value") is not None:
                flow.setdefault(v["date"], {}).setdefault(c, {})["v"] = v["value"]
            i = v.get("inst")
            if i and i.get("date"):
                flow.setdefault(i["date"], {}).setdefault(c, {})["i"] = i["total"]
        flow = dict(sorted(flow.items())[-260:])
        fp.write_text(json.dumps(flow, ensure_ascii=False), encoding="utf-8")

    total = len(twse_codes | tpex_codes)
    print(f"交易日 {trade_date}｜指數 {len(indices)}/2｜個股 {total - len(missing)}/{total}｜法人 {len(stocks) - len(no_inst)}/{total}")
    for p in problems:
        print("[注意]", p)
    if not stocks and not indices:
        sys.exit("完全沒有抓到資料")

if __name__ == "__main__":
    intraday() if "--intraday" in sys.argv else main()
