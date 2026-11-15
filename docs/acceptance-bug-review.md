# 接單流程的 bug 檢查與修正

2026-11-15 晚上，我把下單、建立外送資料、接單、取消、完成和棄單接著測了一輪，也檢查兩位外送員的頁面。這次確實找到會把訂單混在一起、改錯狀態，以及資料成功寫入卻回報失敗的問題，已修正並重跑測試。下面整理修正內容和實測結果。

## 這次修了什麼

| 問題 | 怎麼發生 | 修正後 |
| --- | --- | --- |
| 同一買家在同一秒下兩次單，餐點被合成一張 | 現在用 `buyer_id + timestamp` 辨識整單，時間只到秒；原本兩次請求都會成功，接單時可能連第二次的餐點一起接走 | 同一買家建立訂單時先取得行鎖，再檢查這組識別是否已存在。撞到同一秒就回 `409`，不加入任何餐點；時間仍取實際下單時間 |
| 完成、取消、棄單互相覆蓋 | 兩個請求先讀到相同舊狀態，後送出的更新可能把已完成或已取消的訂單改回待接 | 先鎖定同單餐點，再檢查狀態。另一個請求必須等前一個結束，依最新狀態判斷能不能操作 |
| 接單讀到舊的外送員資料 | 查使用者時已建立 MySQL 讀取快照，後面一般查詢 Delivery 可能仍讀到棄單前的歸屬 | Orders 和 Delivery 都使用鎖定查詢，按固定順序讀取最新資料 |
| 刪除訂單後還留下 Delivery | 原本只刪餐點明細，外送員和地址紀錄仍留著 | Orders 與 Delivery 在同一交易刪除；中途失敗會一起還原。也測了刪除與建立 Delivery 交錯的情況 |
| 明明成功，API 卻回 `500` | 原操作已 commit，另一個刪除請求接著刪掉資料；原操作再讀 ORM 物件準備回應時出錯 | 在 commit 前先保存回應內容，成功後直接回傳。下單、建立外送資料、接單、取消、完成、棄單，以及新增餐廳／餐點都已補測 |
| 輸入超過資料庫欄位上限時回 `500` | 餐廳名稱、地址、餐點名稱太長，或價格、數量超過 MySQL INT 上限 | 寫入前回 `400`；名稱上限分別是 40、80、40 字，價格與數量上限為 `2147483647`。實際寫入後若提交失敗，也會 rollback 並回 JSON 錯誤 |
| 前端載入時短暫漏單，或接受錯配的回應 | 餐點與 Delivery 分兩次查；剛好有人接單或棄單時，兩次結果可能不一致。原本也沒有核對回傳的是不是同一張訂單 | 核對買家和時間；狀態與外送員不一致時只重查一次，仍不一致就顯示錯誤。未知狀態、無法精確計算的金額也不當成正常訂單 |
| 長餐點名稱、地址和備註撐破手機畫面 | 390px 畫面被長英數字撐到 1065px，內容超出卡片 | 卡片內文字可以換行，金額和接單按鈕保留空間。手機 390px、桌機 1100px 都重新檢查過 |

同秒下單目前採「第二次請求被拒絕」，沒有偷偷把時間往後加一秒。若組內要支援同一人一秒內建立多張單，就需要獨立的整單 ID，買家端、接單端和資料庫一起調整。

這次沒有改取消、完成、棄單原本允許的狀態。例如棄單後變回 `PENDING`，再取消仍可成功；完成後再棄單則會被拒絕。刪除訂單原本允許的狀態也保留，只先修正兩張表沒有一起刪的問題。

## 修完後重跑的結果

這輪使用 MySQL `8.0.46`／InnoDB，每組都建立新的本機測試資料庫。測試中的資料寫入、交易衝突與 rollback 都有實際跑到 MySQL；部分案例會刻意控制請求先後或注入失敗，方便穩定重現。

| 測試 | 結果 | 紀錄 |
| --- | --- | --- |
| 下單與接單 | 17/17；包含 10 輪雙連線搶單，每輪只有一個 `200`、一個 `409` | [MySQL 接單測試](evidence/database-audit-20261115/mysql-acceptance.json) |
| 狀態變更與刪除 | 23/23；包含交錯操作、兩表 rollback，以及 commit 後被刪除的回應 | [MySQL 狀態測試](evidence/database-audit-20261115/mysql-lifecycle.json) |
| 餐廳與餐點 | 10/10；包含輸入界線、寫入失敗回滾及提交後刪除 | [MySQL 餐廳／餐點測試](evidence/database-audit-20261115/mysql-catalog.json) |
| 兩位外送員頁面聯測 | 8/8；實際建單、建立 Delivery、接單和 `409` 衝突，另測回應中斷與載入失敗 | [瀏覽器聯測](evidence/database-audit-20261115/browser-results.json) |
| 前端回應與版面 | 13/13；錯配回應、兩次查詢不同步、未知狀態、金額和長文字版面 | [前端檢查](evidence/database-audit-20261115/browser-audit-results.json) |
| 獨立查詢資料庫 | 4/4 張訂單一致；八筆餐點皆為 `PROCESSING`，外送員與各自頁面相符 | [資料庫核對](evidence/database-audit-20261115/integration-results.json) |
| 型別檢查與打包 | 通過 | [typecheck](evidence/database-audit-20261115/typecheck.log)、[build](evidence/database-audit-20261115/build.log) |

13 項前端檢查是在瀏覽器刻意提供不同回應，沒有把假的資料送到後端。真正兩人搶單的結果看 MySQL 測試和雙頁聯測，兩種測法分開記錄。

畫面可對照 [#2 的已接訂單](evidence/database-audit-20261115/own-orders-0.png)、[#3 的已接訂單](evidence/database-audit-20261115/own-orders-1.png)、[同單衝突](evidence/database-audit-20261115/stale-second-courier.png)和[修好後的手機版面](evidence/database-audit-20261115/audit/mobile-long-pending.png)。

修正前的 [同秒下單紀錄](evidence/database-audit-20261115/before/order-contracts.json)、[狀態覆蓋與孤立 Delivery](evidence/database-audit-20261115/before/lifecycle.json)、[提交後錯回失敗](evidence/database-audit-20261115/before/lifecycle-response-races.json)也保留了。自動測試中有固定年份的時間，用來重現同秒碰撞；影片和正常下單仍使用 API 當下產生的時間。

[版本紀錄](evidence/database-audit-20261115/manifest.json)保存這輪實際測到的檔案雜湊。原本 88 秒影片是在這次 bug 檢查前錄的，仍可展示接單與查詢流程，但沒有把它當成這批新修正的錄影證據。

## 還要和組員確認的部分

- **刪單規則，待組長確認。** 已確認組內目前尚未約定。現在連已接單、已完成的訂單都能呼叫刪除 API；哪些狀態可刪、誰能刪、是否保留紀錄，先列給組長討論。這次保留原有的允許狀態，只修好 Orders 與 Delivery 一起刪除、失敗時一起還原。
- **登入與操作權限。** 接單 API 只確認傳入的使用者存在，沒有確認請求者就是那位外送員；取消、完成、棄單也沒有綁定操作者。舊外送員延遲送來的棄單請求，可能在另一人重新接單後才到達，光靠行鎖不能辨識這種情況，還需要登入身分與訂單版本或歸屬檢查。
- **帳號模組的既有問題。** 讀程式時看到密碼直接儲存且會被印到日誌；兩個角色切換方法寫的是 `user_status`，模型欄位卻是 `user_state`。這兩項未納入本輪修正與實測，需要交給帳號模組負責人一併處理。
- **買家端還沒接現有路由。** `App.vue` 使用的 `/api/restaurants`、餐點清單和 `/api/orders`，與目前 Flask 路由不同。這輪是從現有下單 API 開始聯測，還不是從買家購物車一路操作。下單與建立 Delivery 分兩次呼叫，第二步失敗後怎麼補建或取消，也要一起接好。
- **建立 Delivery 的狀態限制。** 目前 API 對已取消、已完成或購物車狀態的訂單也能新增 Delivery。修正前檢查有重現，這次先保留既有行為，等組內確定允許的狀態再補限制。

## 重跑方式

先照[本機執行說明](acceptance-runbook.md)準備環境，再從專案根目錄執行：

```sh
backend/.venv/bin/python -B backend/scripts/local_acceptance.py test
backend/.venv/bin/python -B backend/scripts/local_acceptance.py test-lifecycle
backend/.venv/bin/python -B backend/scripts/local_acceptance.py test-catalog
backend/.venv/bin/python -B backend/scripts/local_integration.py \
  --node /Users/luyifu/.nvm/versions/node/v22.22.2/bin/node \
  --playwright-module .local-runtime/npm-cache/_npx/31e32ef8478fbf80/node_modules/playwright \
  --audit
```

Node 和 Playwright 路徑需換成自己電腦上的位置。聯測會使用 `5100`、`5180`、`5181`、`5182`，同一組 port 的 Demo 要先關掉。結果放在新的 `artifacts` 資料夾，確認三組後端都是 `successful: true`，聯測為 `passed: true`，並且有四張資料庫核對結果。

這輪型別檢查與打包分別用 `npm run type-check` 和 `npm run build-only -- --outDir ../artifacts/frontend-build-audit-20261115-2226`。一開始透過 `npm run build` 轉傳 `--outDir` 時參數沒有正確帶入，改用 `build-only` 後成功，沒有為了通過而改產品程式。
