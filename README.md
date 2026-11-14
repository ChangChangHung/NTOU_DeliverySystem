# 海大餐飲外送系統：接單整合與測試

盧益甫｜Iteration 2｜`feature/yves20716-order-acceptance`

我接著組員的 `feature/driver-lobby-vue`（`bb80ed4`）做接單整合。大廳、接單 API、時間戳和接單鎖沿用組員的版本，我補上獨立接單頁、防連點、錯誤與清單更新處理，再整理測試和報告資料。

## Demo 與設計資料

- [接單 Demo：52.44 秒，含資料庫查詢](docs/evidence/demo-captioned.mp4)
- [接單設計與測試 PDF](docs/接單設計與驗收素材.pdf)
- [API、資料模型、接單流程與狀態圖](docs/acceptance-handoff.md)
- [測試結果與紀錄](docs/acceptance-checklist.md)
- [影片時間軸與報告講稿](docs/acceptance-demo.md)
- [本機啟動與重跑測試](docs/acceptance-runbook.md)

影片前 36.44 秒是 11/14 的接單操作，最後 16 秒是 11/15 補錄的唯讀 MySQL 查詢。買家 #1 的兩筆餐點都由外送員 #2 接下，買家 #4 的一筆餐點由 #3 接下，三筆狀態都是 `PROCESSING`。[查詢 SQL](docs/evidence/demo-database-query.sql)、[回傳結果](docs/evidence/browser/demo-database-query.json)和[截圖](docs/evidence/browser/demo-database-query.png)都有保留。

## 測試結果

2026-11-14 的紀錄：後端 10/10、前端 16/16、本機資料庫設定檢查 5/5 通過，typecheck 和 build 也通過。兩人同時接單用 MySQL 跑了 10 輪，每輪都是一個成功、一個 `409`；另在程式副本移除接單鎖，確認測試能抓到重複接單。

前端測試有區分真實 API 和刻意模擬的錯誤，詳細條件與尚未測到的部分列在[測試結果](docs/acceptance-checklist.md)。

## 執行範圍

接單頁入口是 `/driver.html`。請依[執行說明](docs/acceptance-runbook.md)準備 MySQL、Python 和前端套件，再建立新的測試資料。

這份 Demo 使用本機測試資料與示範外送員 ID；登入、角色權限及買家點餐到接單的完整流程仍要和組員整合。MySQL 資料目錄、帳密及本機依賴不放進 GitHub。
