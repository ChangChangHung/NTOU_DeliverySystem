# 11/15 接單聯測與後續串接

這份保留新增「我的已接訂單」時的聯測。當晚後續找到的資料庫與操作 bug、修正後的 50 項後端測試及前端檢查，另整理在 [bug 檢查紀錄](acceptance-bug-review.md)。

新版大廳已加上「我的已接訂單」。這次瀏覽器聯測 **8/8 通過、0 頁面錯誤**，另外以獨立 MySQL 連線核對 **4 張訂單，全數一致**。typecheck 和 build 也通過。下面先整理新增的頁面行為，再保留先前 7 案聯測與後端 12/12 的紀錄。

## 我的已接訂單

接單後，訂單會從待接清單移到該外送員的「我的已接訂單」，可以看到買家、下單時間、地址、每筆餐點與數量、餐點小計。#2 和 #3 各有自己的頁面，重新整理後仍會重新查回各自的單。

資料沿用三個查詢 API：`GET /api/view_order_by_stamp` 取得整單識別，再用 `POST /api/view_meals_of_order`、`POST /api/view_delivery` 查明細與歸屬。整單所有餐點都是 `processing`，而且 `Delivery.deliverer_id` 等於目前 `VITE_DELIVERER_ID`，才列入「我的已接訂單」。只有 `pending` 且未分配外送員的單會進待接清單。這是前端篩選，後端登入與角色授權仍待整合。

每次進頁面、手動刷新或接單結束，都更新兩份清單。載入中和查詢錯誤時不顯示已接區塊，避免把失敗顯示成「目前沒有已接訂單」；整批載入成功才取代結果。已確認的接單成功提示會保留，查詢失敗另外顯示。

| 本次情境 | 實測結果 |
| --- | --- |
| 各自接單與刷新 | #2 接第一單，#3 接另一單；兩邊各自顯示正確買家、時間與兩筆餐點，刷新後仍在，也沒有混入另一人的單 |
| 第二頁點到同一張舊單 | #2 先成功，#3 的舊清單再送出收到真實 `409`；該單只出現在 #2 的已接清單 |
| 兩頁競爭同一單 | 等兩個請求都準備好才送出；這輪 #3 成功、#2 收到 `409`，已接單只歸 #3 |
| 成功後回應中斷 | API 真正完成接單，再刻意中斷瀏覽器回應；只送一筆 POST，提示結果未確認，刷新後在 #2 的已接清單找到該單 |
| 已接清單查詢失敗及恢復 | 瀏覽器將 `/api/view_order_by_stamp` 回應注入 JSON `500`；出現錯誤、已接區塊隱藏。移除注入後正常恢復，沒有送出額外接單請求 |

8 案還包含兩頁的初始設定、Delivery 晚建立、無效示範 ID，以及 `IN_CART` 過濾。上表是情境整理，不是測試項數。完整 [browser-results.json](evidence/accepted-orders-20261115/browser-results.json)記錄了每案的請求、回應與檢查結果；回應中斷與 JSON `500` 是刻意注入，沒有把它們當成自然發生的後端故障。

本輪 schema 為 `ntou_acceptance_demo_20261115_d977c29aad`，四張都是新建的合成訂單，每張兩筆餐點。以下資料是測試結束後另開 MySQL 連線查得，八筆餐點全部為 `PROCESSING`。

| 情境 | 買家 | 下單時間 | 接單外送員 |
| --- | --- | --- | --- |
| Delivery 晚建立、第二頁使用舊清單 | #1 | `2026-11-15 21:59:39` | #2 |
| 另一位外送員接自己的單 | #1 | `2026-11-15 21:59:41` | #3 |
| 兩頁競爭同一單 | #1 | `2026-11-15 21:59:42` | #3 |
| 成功後回應中斷 | #1 | `2026-11-15 21:59:43` | #2 |

證據：[獨立 MySQL 查詢](evidence/accepted-orders-20261115/integration-results.json)、[本輪 seed](evidence/accepted-orders-20261115/seed.json)、[#2 已接清單](evidence/accepted-orders-20261115/own-orders-0.png)、[#3 已接清單](evidence/accepted-orders-20261115/own-orders-1.png)、[載入錯誤](evidence/accepted-orders-20261115/own-orders-load-error.png)、[恢復後畫面](evidence/accepted-orders-20261115/own-orders-recovered.png)。原始輸出在 `artifacts/integration-20261115T135937Z-fce0dc`。

本次沒有修改後端接單邏輯，也沒有重跑下方的 12 項後端測試；新增的是前端已接清單、對應聯測和錄影入口。

## 前一輪聯測紀錄

前一輪接著 `feature/driver-lobby-vue` 的 `bb80ed4` 測，自己的分支基底是 `e7bcf8e`。11/15 核對 GitHub 時，隊友分支還是同一版。

前一輪補的是不同步和互相搶單的情況，當時**後端 12/12、瀏覽器聯測 7/7 都通過**，最後另外開 MySQL 連線查了三張訂單，結果都和畫面、API 一致。以下保留那一輪的結果。

## 前一輪的測試項目

| 測試 | 結果 |
| --- | --- |
| 訂單先建立，外送資料稍後才建立 | 沒有 Delivery 時，兩個外送員頁面都看不到這張單；用下單回傳的時間補建 Delivery，再重新整理就看得到。 |
| 另一位外送員還停在舊清單 | #2 先接成功，#3 再點同一張單收到真正的 `409`，提示已被接走，清單也會更新。 |
| 兩個外送員頁面一起搶單 | 兩邊都按接單，等兩個請求都準備好才送到後端。這次 #3 成功、#2 收到 `409`，沒有兩人都成功。 |
| 後端已接成功，前端卻收不到回應 | 先讓 API 真正完成接單，再刻意中斷回應。頁面提示還無法確認結果，不會自動重送；更新清單後，已接單的卡片消失。資料庫仍記錄 #2。 |
| 外送員 ID 設定錯誤 | 畫面會提示，接單按鈕停用，也沒有送出接單請求。 |
| 購物車和待接狀態混在一起 | `IN_CART`、`PENDING + IN_CART` 都不會顯示成可接訂單，直接呼叫接單 API 也會被拒絕。 |
| 相同買家或相同下單時間的其他訂單 | 接目標單後，「同買家、不同時間」和「不同買家、同時間」的兩張對照單都沒被改到。 |
| 下單時間的小數秒 | 沿用原本四個時間邊界，這次一路測到接單及查回資料庫，回傳的 timestamp 都能接下去用。 |
| 下單寫入失敗 | INSERT 後刻意讓 commit 失敗，API 回 JSON `500`，資料庫沒有留下部分餐點。 |

瀏覽器的 7 項包含兩個外送員頁面的設定確認；後端的 12 項則包含原本的回歸測試，所以表格列的是情境，不是把兩份測試數量相加。MySQL 的雙連線搶單也重跑了 10 輪，每輪都是一個 `200`、一個 `409`。

那一輪只修了一處後端邏輯：`add_new_order()` 的例外處理漏了 `return`，現在會把原本準備好的 JSON 錯誤送回前端。接單 API 和行鎖沿用組員與前次整合的版本。Vite 另加了 `NTOU_API_TARGET`，讓聯測能使用獨立的 API port；沒設定時仍連 `127.0.0.1:5000`。

## 前一輪的資料庫結果

那一輪使用 `ntou_acceptance_demo_20261115_2210e7b0b9`，以下都是當時新建的測試訂單。每張都有兩筆餐點，六筆最後都是 `PROCESSING`。

| 情境 | 買家 | 下單時間 | 接單外送員 |
| --- | --- | --- | --- |
| 外送資料晚建立、第二人使用舊清單 | #1 | `2026-11-15 20:26:46` | #2 |
| 兩個頁面搶同一單 | #1 | `2026-11-15 20:26:47` | #3 |
| 接單成功後回應中斷 | #1 | `2026-11-15 20:26:49` | #2 |

完整紀錄：[後端測試](evidence/integration-20261115/mysql-results.json)、[瀏覽器測試](evidence/integration-20261115/browser-results.json)、[獨立資料庫查詢](evidence/integration-20261115/integration-results.json)。兩個頁面的結果可看 [#2 的畫面](evidence/integration-20261115/compete-0.png)和 [#3 的畫面](evidence/integration-20261115/compete-1.png)。

## 11/15 串接狀況與介面需求

| 項目 | 當時狀況 | 串接需求與檢查項目 |
| --- | --- | --- |
| 登入後的身分與角色 | 接單頁使用 `VITE_DELIVERER_ID`；後端接單 API 尚未驗證登入身分與外送員權限。 | 登入資訊需提供 `user_id`、目前角色及驗證方式；測試需涵蓋未登入、非外送員及身分切換。 |
| 買家介面到接單大廳 | `App.vue` 呼叫 `/api/restaurants`、`/api/restaurants/:id/menu`、`/api/orders`，Flask 沒有這三條路由。既有點餐與購物車介面尚未接上資料庫 API。 | 買家頁需改接現有 API，確認下單後出現在大廳、接成功後整單狀態正確。本輪已驗證後端建單、建立外送資料到接單的流程。 |
| 接到單以後的訂單頁 | 大廳已顯示自己的 `processing` 訂單與餐點；尚未加入配送進度與完成操作。 | 訂單頁沿用 `buyer_id + timestamp` 識別整單，需確認頁面入口、後續狀態與完成操作。 |
| 大廳自動更新 | 11/15 版本只在進頁面、手動重新整理和接單結束時讀最新清單。 | 更新不能造成接單重送，舊卡片仍須正確處理 `409`。 |

接單端目前認整張訂單的方式是 **`buyer_id + timestamp`**。買家端要先呼叫 `POST /api/add_new_order`，再用回傳的 timestamp 呼叫 `POST /api/add_new_delivery`，外送資料建立好才會出現在大廳。時間直接沿用回傳值，不要在前端重新產生或轉時區。

`POST /api/process_order` 送 `buyer_id`、`timestamp`、`deliverer_id`，成功後所有餐點都變成 `processing`，Delivery 記錄承接的外送員。查詢用 `POST /api/view_meals_of_order` 和 `POST /api/view_delivery`，同樣帶 `buyer_id + timestamp`；取餐／送餐地址的定義和外送費計算規則尚未統一。

是否允許接自己的單、自取及取消／棄單的規則尚未定義。11/15 晚上的 [bug 檢查](acceptance-bug-review.md)補測了交易交錯，修正行鎖、舊快照和回應時機，但沒有新增取消／刪單的業務規則。

## 重跑這次聯測

11/15 晚上展示時，發現原 Demo 沿用了單元測試預設的 2030 年日期。我已將 Demo 建單改成呼叫現有 `add_new_order` API，時間直接取 API 回傳值，再用同一個時間建立 Delivery。重新跑的 7 項瀏覽器聯測、3 張訂單資料庫核對都通過，8 張 seed 訂單的時間也都在實際建單的當下。新版[建單紀錄](evidence/demo-date-fix-20261115/seed.json)、[聯測結果](evidence/demo-date-fix-20261115/integration-results.json)及[展示畫面](evidence/demo-date-fix-20261115/mysql-current-time.jpg)另附。前面的表格保留原輪次的結果。

先依[本機執行說明](acceptance-runbook.md)準備 MySQL 和前端套件。新的 [local_integration.py](../backend/scripts/local_integration.py) 會沿用本機 MySQL 設定，每次建立新 schema；原 Demo 的設定與資料會留著。

在我這台 Mac，從專案根目錄執行：

```sh
backend/.venv/bin/python -B backend/scripts/local_acceptance.py mysql-start
backend/.venv/bin/python -B backend/scripts/local_acceptance.py test
backend/.venv/bin/python -B backend/scripts/local_integration.py \
  --node /Users/luyifu/.nvm/versions/node/v22.22.2/bin/node \
  --playwright-module .local-runtime/npm-cache/_npx/31e32ef8478fbf80/node_modules/playwright
```

最後一個命令使用已安裝的 Playwright 和 Chrome，不會下載套件。API 使用 `127.0.0.1:5100`，三個前端使用 `5180`、`5181`、`5182`；port 已被占用會停止。測完會關掉這次開的前後端和測試瀏覽器，保留 MySQL 與測試資料。別台電腦需先準備相同的本機環境，再改成自己的 Node 和 Playwright 路徑。

目前 runner 會跑 8 案瀏覽器聯測，另以獨立 MySQL 連線查 4 張訂單。結果放到新的 `artifacts/integration-*` 資料夾，確認最後輸出 `passed: true`，並檢查 `integration-results.json` 的四張訂單都通過。測試不需要先把原本兩張待接單接完，也不把買家和外送員 ID 寫死在測試程式。

## 重錄雙頁 Demo

[local_demo.py](../backend/scripts/local_demo.py) 會建立新 Demo schema，啟動兩個實際 `/driver.html`，以 iframe 放在同一個錄影畫面，並提供這兩張示範訂單的 MySQL 唯讀查詢。這個工具是輔助錄影入口，不是產品首頁；接單仍由原本的大廳按鈕呼叫 Flask API。

啟動命令與操作順序見[執行說明](acceptance-runbook.md#兩位外送員與資料庫的錄影入口)。52.44 秒影片屬於前次操作與補錄，各版成品、字幕及講稿整理在 [Demo 素材](acceptance-demo.md)。
