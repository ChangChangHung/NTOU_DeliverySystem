# 接單模組執行說明

我把重跑測試和 Demo 的步驟整理在下面，所有命令都從專案根目錄執行。API 與負責範圍見 [交接素材](acceptance-handoff.md)，這次 10/10 的測試結果見 [驗收對照表](acceptance-checklist.md)。

## 在我的 Mac 上重跑

我的 Mac 已準備好 `backend/.venv` 和未納入 Git 的 `.local-runtime`，裡面有 MySQL `8.0.46` macOS ARM64 程式、初始化完成的資料目錄及私有 `mysql-local.json`。runner **不會下載或初始化 MySQL**；缺少這些檔案時，請用下一節的方式準備環境。

[local_acceptance.py](../backend/scripts/local_acceptance.py) 會先核對版本、資料目錄、socket 與 `127.0.0.1:3307`，再啟動或停止這個專用 MySQL。帳密從私有設定讀取，只透過子程序的環境變數傳入，不放進命令、文件或 Git。

```sh
backend/.venv/bin/python -B backend/scripts/local_acceptance.py status
backend/.venv/bin/python -B backend/scripts/local_acceptance.py mysql-start
backend/.venv/bin/python -B backend/scripts/local_acceptance.py test
backend/.venv/bin/python -B backend/scripts/local_acceptance.py seed
backend/.venv/bin/python -B backend/scripts/local_acceptance.py serve
```

每步成功後再做下一步。`test` 會建立全新的測試 schema，並顯示帶時間戳的 JSON／stderr 紀錄路徑。`seed` 另外建立新的 Demo schema，更新 `artifacts/freshseed.json` 與私有 `.local-runtime/demo.json`；`serve` 讀取這份設定，在 `http://127.0.0.1:5000` 啟動 Flask，debug 與 reloader 都關閉。

在第二個終端機讀取 seed 的示範 ID，再開前端：

```sh
VITE_DELIVERER_ID="$(backend/.venv/bin/python -c 'import json; print(json.load(open("artifacts/freshseed.json"))["frontend_environment"]["VITE_DELIVERER_ID"])')" npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

開啟 [接單大廳](http://127.0.0.1:5173/driver.html)後，新 seed 應顯示兩張可接訂單。缺 Delivery、已承接、取消與完成的案例則應隱藏。實際買家 ID、外送員 ID 和時間戳都在 seed JSON，請依這份資料設定，不要寫死帳號數字。

重錄時，先以 `Ctrl+C` 停止 Flask，再重新 `seed`、`serve`，並用新 seed 的 ID 重開前端。舊 schema 會保留，方便回頭看測試資料，不會刪除或重置。結束時先停止前後端，再執行：

```sh
backend/.venv/bin/python -B backend/scripts/local_acceptance.py mysql-stop
```

`mysql-stop` 只會停止核對通過的專用 MySQL。無法確認是哪個服務時會拒絕執行，請先確認原因，不要強制終止其他程序。

## 同學要在自己的電腦跑

這個做法不需要 `.local-runtime` 或上面的 Mac runner。先準備可連線的 **MySQL 8.x／InnoDB**，以下使用 `127.0.0.1:3307`；測試程式不接受 MariaDB 或 SQLite。DB 使用者需要能建立新測試 schema、建表和讀寫資料。MySQL 的安裝與初始化依各自環境處理，不要求事先裝好容器工具。

我測試時使用 Python `3.13.2`。專案還沒有 `backend/.venv` 時，可先執行：

```sh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-lock.txt
```

已有環境就使用該環境的 Python。前端建議使用我這次測過的 **Node `22.22.2`、npm 10 以上**。專案宣告的 Node 下限雖然較低，lock 裡的 `npm-run-all2@9.0.3` 實際要求 `^22.22.2 || ^24.15.0 || >=26.0.0`，所以不能只看根目錄的 engines。第一次安裝前端套件可執行 `npm --prefix frontend ci`，完成後再往下跑。

接著設定本機資料庫。以下適用 bash／zsh，兩個 `read` 依序輸入本機 DB 使用者與密碼，輸入密碼時不會顯示字元。帳密只放在環境變數，不寫進指令列或文件。

```sh
export DB_HOST=127.0.0.1
export DB_PORT=3307
export NTOU_ACCEPTANCE_ALLOW_CREATE=1
read -r DB_USER
read -rs DB_PASSWORD
export DB_USER DB_PASSWORD
unset DB_NAME
```

[test／seed helper](../backend/tests/mysql_support.py) 從環境變數讀取設定。可以參考 `backend/.env.example` 的欄位，但只建立 `.env` 不會讓這兩支腳本讀到設定。`NTOU_ACCEPTANCE_ALLOW_CREATE=1` 表示允許建立新 schema；省略 `DB_NAME` 時會自動產生唯一的 `ntou_acceptance_*` 名稱。腳本拒絕重用既有 schema，也不會執行 DROP、TRUNCATE 或清除資料。

先跑測試並保存結果。`set -C` 可避免覆寫同名紀錄；檔案已存在時，請換一組新檔名。

```sh
mkdir -p artifacts
set -C
unset DB_NAME
backend/.venv/bin/python -B backend/tests/test_order_acceptance_mysql.py > artifacts/my-local-test.json 2> artifacts/my-local-test.stderr.log
```

退出碼需為 `0`，JSON 的 `successful` 需為 `true`。接著另建 Demo 資料庫：

```sh
unset DB_NAME
backend/.venv/bin/python -B backend/scripts/seed_acceptance_demo.py > artifacts/my-local-demo.json
export DB_NAME="$(backend/.venv/bin/python -c 'import json; print(json.load(open("artifacts/my-local-demo.json"))["metadata"]["database"])')"
backend/.venv/bin/python -B -c 'import sys; sys.path.insert(0, "backend"); from server import app; app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)'
```

最後一行持續執行 Flask。在另一個終端機，仍從專案根目錄開啟前端：

```sh
VITE_DELIVERER_ID="$(backend/.venv/bin/python -c 'import json; print(json.load(open("artifacts/my-local-demo.json"))["frontend_environment"]["VITE_DELIVERER_ID"])')" npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

開啟 `/driver.html`。重新 seed 時，先停止 Flask、`unset DB_NAME`，換新的輸出檔名，再從新 JSON 讀出 DB 名稱與示範 ID。已接過的舊訂單會回 `409`，重跑時要確認使用的是新資料。

## Build 與測試紀錄

```sh
npm --prefix frontend run build
```

這個命令會跑 typecheck 和 Vite build。開發模式透過 Vite proxy 將 `/api` 轉給 `127.0.0.1:5000`；build 只產生靜態檔，公開部署和 API 路由還要由團隊設定。

我這次的 [build](evidence/frontend-build.log)已通過，也保留了 [5/5 本機設定測試](evidence/mysql-safety.log)和[19 個 Python 套件相容性檢查](evidence/python-dependencies.log)的紀錄。

## 重跑瀏覽器補強 QA

[acceptance-browser.js](../frontend/tests/acceptance-browser.js) 是瀏覽器自動測試，執行前要先開啟本機前後端。我使用的工具是 `@playwright/cli 0.1.24`、Playwright／core `1.66.0-alpha-1794252756000`，瀏覽器回報的版本是 Chrome `157.0.8103.51`。

使用全新的隔離 seed，確認有買家 `1`、餐點 `1` 單價 `80`、餐點 `2` 單價 `100`、示範外送員 `2`。**先完成 seed 最初兩張待接訂單，使清單為空**，再執行腳本。腳本使用這組 fixture，自己的 ID 不同時，要先調整測試設定，不能直接對任意資料庫執行。

從專案根目錄開啟 `ntou-qa` session，再執行檔案。`npx` 會按需要將指定 CLI 下載至 npm 快取，不將測試依賴加入專案 `package.json`：

```sh
mkdir -p artifacts/browser
npx --yes --package @playwright/cli@0.1.24 playwright-cli --session ntou-qa open http://127.0.0.1:5173/driver.html --browser chrome --headed --idle-timeout 0
npx --yes --package @playwright/cli@0.1.24 playwright-cli --session ntou-qa run-code --filename frontend/tests/acceptance-browser.js
```

腳本會新增並接下兩張合成訂單，也會攔截瀏覽器回應來測錯誤提示，不會重置資料庫。重跑前請另存原有的 `qa-results.json` 和截圖，保留每次結果。最後要確認 JSON 是 `passed: true`、16 個 case 都通過，並檢查 `page_errors`；CLI 正常返回不一定代表測試通過。這次的結果與限制見[驗收對照表](acceptance-checklist.md)。

請保留 JSON、stderr、seed JSON、build 日誌和瀏覽器／影片紀錄。seed 裡的帳號是合成資料的 ID，**登入驗證還沒完成**。接單程式和文件整理在 `feature/yves20716-order-acceptance` 分支，這次測試結果的固定副本放在 `docs/evidence`，可提供給組長。重跑產生的 `artifacts` 和本機設定 `.local-runtime` 仍需各自保存，私有連線設定及密碼不要分享。

[字幕 Demo](evidence/demo-captioned.mp4)共 52.44 秒，[時間軸／講稿](acceptance-demo.md)也已更新。前 36.44 秒保留 2026-11-14 的接單操作，後 16 秒是 2026-11-15 補錄的資料庫查詢，兩段並非連續拍攝。

補錄使用[這份 SQL](evidence/demo-database-query.sql)，只讀取 `127.0.0.1:3307` 上的原 Demo schema `ntou_acceptance_demo_20261114_b8877ae024`，沒有重新接單或修改訂單資料。買家 `1` 的兩筆餐點由外送員 `2` 接下，買家 `4` 的一筆餐點由外送員 `3` 接下，三筆都是 `PROCESSING`。結果見[查詢紀錄](evidence/browser/demo-database-query.json)與[畫面](evidence/browser/demo-database-query.png)。若要在自己的新 seed 查詢，需改用那次產生的 schema、買家 ID 和時間戳。

這些資料只涵蓋我負責的接單模組，課程作業的繳交狀態需要另外確認。
