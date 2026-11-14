# 接單模組驗收對照表

需求：`DS-US-06`、`FR-DLV-01`。來源與 API 契約見 [交接素材](acceptance-handoff.md)，重跑方式見 [執行說明](acceptance-runbook.md)。

## 我跑過的後端測試

2026-11-14 的[測試報告](evidence/acceptance-test-20261114T134411.114410Z-d7dd06.json)顯示 **10 個測試全部通過，0 failures、0 errors**。我使用 Python `3.13.2`、MySQL `8.0.46`／InnoDB、`127.0.0.1:3307`；隔離 schema 為 `ntou_acceptance_test_20261114_20bce73066`，全為合成資料。

我用 Flask test client 跑自動測試，呼叫實際接單處理函式並連到真 MySQL，沒有經過瀏覽器或公開 HTTP 服務。以下名稱對應報告的 `evidence[].case` 與 [測試程式](../backend/tests/test_order_acceptance_mysql.py)。

| 驗收項目 | 實測結果 | 證據 case |
| --- | --- | --- |
| 多餐點整單成功、提交後可讀 | `200`；全部餐點 `PROCESSING`、接單外送員正確；移除 session、dispose pool 後以獨立連線讀取仍一致 | `test_multi_meal_acceptance_is_committed_and_readable_after_engine_disposal` |
| 同一人重複接單 | 第一次 `200`，第二次 `409`，接單外送員不變 | `test_same_courier_cannot_accept_twice` |
| 兩人競爭同單 | 10 輪請求交疊，每輪使用 2 條不同 MySQL 連線，結果皆為一個 `200`、一個 `409`；整單記錄的外送員與接單成功的人一致 | `test_two_couriers_compete_on_mysql_row_locks_ten_rounds` |
| 缺外送紀錄 | `404`；兩筆餐點仍為 `PENDING` | `test_missing_delivery_rejects_without_updating_any_meal` |
| 不存在的外送員、買家或訂單時間 | 三種請求皆 `404`，原資料不變 | `test_nonexistent_courier_and_order_return_404_without_changes` |
| 非待接與混合狀態 | `CANCELLED`、`COMPLETED`、`PROCESSING`，以及 `PENDING + PROCESSING` 均 `409`，整單不局部更新 | `test_non_pending_and_mixed_state_orders_reject_as_a_whole` |
| 已指派其他外送員 | 即使餐點仍為 `PENDING`，也回 `409`，不更換接單外送員 | `test_other_courier_assignment_is_not_overwritten` |
| 無效輸入 | JSON 陣列、空物件、`buyer_id=0`、布林外送員 ID、非數字外送員 ID、錯誤時間字串共六種，皆 `400`，資料不變 | `test_invalid_payloads_return_400_without_changes` |
| 雙表更新後 rollback | 實際執行 `ORDERS`、`DELIVERY` 的 UPDATE 後注入例外，回 `500`；新連線查得餐點回到 `PENDING`、外送員回到 `null` | `test_sql_failure_rolls_back_updates_in_both_tables` |
| 秒級時間戳 | 注入微秒值 `0`、`499999`、`500000`、`999999`；下單回傳的 timestamp 均可建立對應 Delivery，兩個 API 皆 `201` | `test_returned_order_timestamp_can_create_delivery_at_fractional_boundaries` |

並發測試確認了請求交疊、使用不同連線，但我沒有讀取 MySQL 鎖等待表，所以沒有直接觀察到兩條連線同時等待鎖。持久化測試只驗證新連線能否讀到資料，沒有重啟作業系統或資料庫。rollback 則是在測試中刻意注入例外，這份紀錄只包含測試結果。

報告中的 `git_head` 是 **2026-11-14 測試當時**的 PR #1 head `bb80ed42ba398f3bf801aebdc127b7fdfdaebf3e`，當時接單補強、測試和工具還沒提交。這個欄位保留當時的紀錄，不代表目前分支的 HEAD。`metadata.source_sha256` 記錄當次 `server.py`、`utils.py`、`tests/mysql_support.py` 的實際雜湊，整理測試文件時已確認三檔都與紀錄一致。

我另外做了[反向實驗](evidence/negative-control.json)，只在隔離副本移除 `process_order()` 的 `FOR UPDATE`，測試程式不變。單次執行的 10 輪都變成雙 `200`，產生 10 個失敗斷言，原檔 SHA-256 未變。這表示測試能抓到移除行鎖造成的問題，但還沒有涵蓋所有競爭情境。

本機設定的 [5/5 單元測試](evidence/mysql-safety.log)也通過，檢查了主機、schema 名稱、必要帳密設定、port 與建庫 opt-in 限制；[Python 相容性檢查](evidence/python-dependencies.log)確認 19 個已安裝套件相容。這兩份結果與 10 項 MySQL 整合測試分開計算。

## 前端測試與 Demo

| 項目 | 結果與紀錄 |
| --- | --- |
| 最終前端 typecheck／build | 通過；[日誌](evidence/frontend-build.log)含 vue-tsc 與 Vite `8.3.4` build、`dist/driver.html` |
| `/driver.html`、示範帳號與整單明細 | 通過；初始兩張可接單、外送員 `2`，見[大廳截圖](evidence/browser/01-lobby.png)與[七步紀錄](evidence/browser/demo-evidence.json) |
| 正常接單與點擊刷新 | 通過；買家 `1` 多餐點單回 `200`，刷新後仍隱藏；[獨立讀取 DB](evidence/browser/demo-database-state.json)確認兩筆餐點皆 `PROCESSING`、外送員 `2` |
| 舊清單衝突 | 通過；外送員 `3` 先透過真實本機 API 接買家 `4`，瀏覽器舊清單收到 `409` 並變成空清單；這是先後接單的衝突展示，不是雙瀏覽器並發 |
| 防重點擊 | [QA](evidence/browser/qa-results.json)通過：同頁八次同步 DOM click，只送出一筆真實接單 POST；路由僅刻意延遲 300 ms。API 查得兩筆餐點皆 `processing`、外送員 `2` |
| 真實離線、恢復與空清單 | 通過；瀏覽器離線後顯示載入錯誤，恢復連線並刷新後顯示空清單，見[離線](evidence/browser/04-offline.png)與[恢復](evidence/browser/05-recovered-empty.png) |
| 空資料與混合狀態 | QA 以瀏覽器回應注入空餐點、空 Delivery、`pending + processing`；均安全隱藏，移除注入後可恢復顯示 |
| 接單錯誤提示 | QA 的七種瀏覽器注入均通過：JSON `400`／`404` 顯示原因；`500`、非 JSON、`200` 缺識別、network abort、15 秒逾時顯示結果未確認。各只發起一筆 POST，未送達真實接單路由，未改資料 |
| 成功後刷新失敗 | 真實接單成功，僅後續清單 GET 注入 `500`；成功提示與載入錯誤並存，移除注入後恢復空清單 |
| 手機寬度 | 390×844 viewport 下 document／body 寬度均為 390，無水平溢出；[截圖](evidence/browser/qa-mobile-390x844.png)已檢視 |
| 資料庫查詢補錄（2026-11-15） | 真實讀取原 Demo MySQL；買家 `1` 的兩筆餐點由外送員 `2` 接下，買家 `4` 的一筆餐點由外送員 `3` 接下，三筆皆 `PROCESSING`。保留 [SQL](evidence/demo-database-query.sql)、[查詢結果](evidence/browser/demo-database-query.json)和[畫面](evidence/browser/demo-database-query.png)。只查詢資料，沒有重新接單 |
| Demo 影片 | [字幕 MP4](evidence/demo-captioned.mp4)已完成，52.44 秒、1280×900、H.264；前 36.44 秒保留 11/14 操作，後 16 秒是 11/15 補錄的資料庫查詢，已逐段檢查字幕與畫面。[時間軸與講稿](acceptance-demo.md)另附 |
| 給組長的模組素材 | 已整理 API、資料、順序／狀態圖與限制；是否收到、是否已放進全組文件，還要與組長確認 |

前端補強 QA 合計 **16/16 通過、0 pageerrors**，各項結果和真 API／注入模式都記在 [qa-results.json](evidence/browser/qa-results.json)，原始 CLI 輸出見 [qa-cli-output.json](evidence/browser/qa-cli-output.json)。我也保留了首輪的 [15/16 紀錄](evidence/browser/qa-first-run-results.json)：當時測試餐點的數量配反，小計是 260，預期卻寫 280。修正 fixture 後重跑就通過，沒有改產品程式。注入案例只測前端如何處理回應，沒有真的讓後端故障；單頁連點也和兩個交易競爭分開測。

## 還沒測到與需要討論的地方

我還沒逐一測試 `IN_CART`、所有非法數值／時間型別、無關訂單完全不變、真正程序重啟、無效 `VITE_DELIVERER_ID`、所有缺餐點／餐廳的組合、刻意重排刷新回應，以及接單與取消／棄單之間的競爭。這些項目仍保留為未驗證。

正式登入與角色權限、同買家同秒兩單的識別、付款／取消退款、無人接單與未收餐、地址及外送費的規則，還要和團隊討論。是否達到課程要求的 40% 實作，也要合併其他模組一起確認。

文件連到的 `docs/evidence` 保留這次測試結果；重跑的新紀錄會放在 `artifacts`，不會自動覆蓋這一份。
