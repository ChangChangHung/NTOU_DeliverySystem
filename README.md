# 海大餐飲外送系統：接單整合與測試

盧益甫｜Iteration 2｜`feature/yves20716-order-acceptance`

我接著組員的 `feature/driver-lobby-vue`（`bb80ed4`）往下做，原本的大廳、接單 API、時間戳處理和接單鎖都沿用組員的版本。我這次加了可以直接開啟的接單頁，處理按鈕連點、接單失敗時的提示，以及接單後的清單更新，也把測試結果和報告要用的資料整理在這裡。

## Demo 與設計資料

- [接單 Demo：52.44 秒，含資料庫查詢](docs/evidence/demo-captioned.mp4)
- [接單設計與測試 PDF](docs/接單設計與驗收素材.pdf)
- [API、資料模型、接單流程與狀態圖](docs/acceptance-handoff.md)
- [測試結果與紀錄](docs/acceptance-checklist.md)
- [影片時間軸與報告講稿](docs/acceptance-demo.md)
- [本機啟動與重跑測試](docs/acceptance-runbook.md)

影片前 36秒是 11/14 錄的接單操作。為了看清楚訂單最後是誰接走的，我在 11/15 補錄了最後 16 秒的 MySQL 查詢，這段只查資料，沒有改訂單。

查詢結果可以看到，買家 #1 的兩筆餐點都是外送員 #2 接的，買家 #4 的一筆餐點則是 #3 接的，三筆狀態都變成 `PROCESSING`。要對照資料的話，可以看[查詢 SQL](docs/evidence/demo-database-query.sql)、[查詢結果](docs/evidence/browser/demo-database-query.json)和[截圖](docs/evidence/browser/demo-database-query.png)。

## 測試結果

2026-11-14 跑的測試都通過了：後端 10/10、前端 16/16、本機資料庫設定檢查 5/5，typecheck 和 build 也通過。

我用 MySQL 跑了 10 輪兩人同時搶同一張單的測試，每輪都只有一人接成功，另一人收到 `409`，不會把同一張單接走兩次。另外也用一份程式副本拿掉接單鎖，確認測試能抓出重複接單的問題。

前端除了測實際的 API，也有模擬錯誤回應，檢查畫面會不會正確提示。各項測法、結果和還沒測的部分都寫在[測試紀錄](docs/acceptance-checklist.md)。

## 怎麼開啟接單頁

啟動後，開啟 `/driver.html` 就能進接單大廳。要在自己的電腦上跑，可以照[啟動說明](docs/acceptance-runbook.md)準備 MySQL、Python 和前端套件，再建立測試訂單。

這次先在我電腦上用測試訂單跑接單流程，外送員用 #2 和 #3。後續還要跟組員把登入、外送員權限和買家下單接起來，再一起測完整流程。
