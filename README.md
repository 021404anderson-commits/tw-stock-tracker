# 台股每日追蹤系統

每個交易日自動追蹤台股指數與關注個股，並整理法說會、供應鏈位置與產品比重。

網頁：https://021404anderson-commits.github.io/tw-stock-tracker/

## 這個系統怎麼運作

| 項目 | 來源 | 更新方式 |
|---|---|---|
| 加權指數、櫃買指數、個股開高低收與成交量 | 證交所、櫃買中心開放資料 | GitHub Actions 每個交易日 15:37、18:07、21:07（台北時間）自動執行 `fetch.py` |
| 法說會日期、產品比重 | 財經媒體的法說會整理，附來源連結 | Claude 排程任務每週六 08:43 檢查並更新 `fundamentals.json` |
| 供應鏈位置、關注清單 | 人工設定 | 修改 `watchlist.json` |

## 檔案說明

- `watchlist.json`：關注的指數與個股（代號、名稱、上市 `twse`／上櫃 `tpex`、產業分組、供應鏈位置）
- `fundamentals.json`：每檔的法說會與產品比重
- `fetch.py`：抓取官方行情的程式，只用 Python 內建模組
- `data/latest.json`：最新一次抓到的行情
- `data/history.json`：累積的每日收盤（約保留一年），用來畫走勢線
- `index.html`：網頁
- `.github/workflows/daily.yml`：每日自動執行的排程

## 常見調整

- **增減個股**：在 `watchlist.json` 的 `stocks` 加一筆或刪一筆，下次自動更新就會生效。
- **手動立刻更新**：到 GitHub 的 Actions 頁面，選「每日更新」，按 Run workflow。
- **資料沒更新**：網頁上方若出現紅色提示，代表有項目抓不到；到 Actions 頁面看最近一次執行的紀錄。

行情為官方盤後資料，非即時報價。本系統僅供研究參考，非投資建議。
