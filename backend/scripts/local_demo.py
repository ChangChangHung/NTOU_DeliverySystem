"""Start a fresh local demonstration, with two real courier pages and a MySQL viewer."""
import argparse
import shutil
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import html
import json
import os
import socket
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/scripts'))
from local_acceptance import Configuration, require_server, validate_demo
from local_integration import wait_http

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--node', default=shutil.which('node'), help='Installed Node executable')
args = parser.parse_args()
if not args.node or not Path(args.node).is_file():
    raise SystemExit('Provide the installed Node executable with --node.')
config = Configuration.load()
require_server(config)
for port in (5100, 5180, 5181, 8767):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
folder = ROOT / 'artifacts' / ('live-demo-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
folder.mkdir(parents=True)
result = subprocess.run([sys.executable, '-B', 'backend/scripts/seed_acceptance_demo.py'],
                        cwd=ROOT, env=config.environment(), capture_output=True, text=True, check=True)
seed = json.loads(result.stdout)
database = validate_demo(config, seed)
(folder / 'seed.json').write_text(json.dumps(seed, ensure_ascii=False, indent=2))
processes, logs = [], []

def start(command, env, cwd, name):
    log = (folder / (name + '.log')).open('x')
    logs.append(log)
    process = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    processes.append(process)
    return process

try:
    api = start([sys.executable, '-B', '-c', "from server import app; app.run(host='127.0.0.1', port=5100, debug=False, use_reloader=False)"],
                config.environment(database), ROOT / 'backend', 'backend')
    wait_http('http://127.0.0.1:5100/api/view_all_meals', api)
    for port, courier in zip((5180, 5181), seed['demo_courier_ids']):
        env = dict(os.environ, NTOU_API_TARGET='http://127.0.0.1:5100', VITE_DELIVERER_ID=str(courier))
        process = start([args.node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'),
                         '--host', '127.0.0.1', '--port', str(port), '--strictPort'], env, ROOT / 'frontend', f'frontend-{port}')
        wait_http(f'http://127.0.0.1:{port}/driver.html', process)

    fixtures = [item for item in seed['fixtures'] if item['scenario'].startswith('ready_')]
    (folder / 'shown-orders.json').write_text(json.dumps(fixtures, ensure_ascii=False, indent=2))

    def query():
        connection = config.connect()
        try:
            connection.select_db(database)
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
                cursor.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
                clauses = ' OR '.join('(o.buyer_id=%s AND o.timestamp=%s)' for _ in fixtures)
                params = tuple(value for item in fixtures for value in (item['buyer_id'], item['timestamp']))
                sql = ('SELECT o.buyer_id,o.timestamp,o.order_id,o.meal_id,o.order_state,d.deliverer_id '
                       'FROM orders o JOIN delivery d ON o.buyer_id=d.buyer_id AND o.timestamp=d.timestamp '
                       'WHERE ' + clauses + ' ORDER BY o.buyer_id,o.timestamp,o.order_id')
                cursor.execute(sql, params)
                columns = [column[0] for column in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                for row in rows:
                    row['timestamp'] = row['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
                payload = {'queried_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                           'database': database, 'sql': cursor.mogrify(sql, params), 'rows': rows}
            connection.rollback()
            stamp = datetime.now().strftime('%H%M%S-%f')
            (folder / f'query-{stamp}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            return payload
        finally:
            connection.close()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                content = (ROOT / 'backend/scripts/demo_dashboard.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if self.path not in ('/database', '/query'):
                self.send_error(404)
                return
            payload = query()
            if self.path == '/query':
                content = json.dumps(payload, ensure_ascii=False).encode()
                mime = 'application/json; charset=utf-8'
            else:
                rows = ''.join('<tr>' + ''.join('<td>' + html.escape(str(row[key]) if row[key] is not None else '尚未接單') + '</td>'
                    for key in ('buyer_id','timestamp','order_id','meal_id','order_state','deliverer_id')) + '</tr>' for row in payload['rows'])
                content = ('''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>這次接單的資料庫紀錄</title>
<style>body{font:21px system-ui;background:#f6f7f9;color:#243444;margin:32px auto;max-width:1500px;padding:0 20px}h1{font-size:30px}a{color:#175bb1}table{border-collapse:collapse;width:100%;background:white;margin:22px 0}th,td{padding:15px 12px;text-align:left;border-bottom:1px solid #d8e0e6}th{background:#263e51;color:white}td:last-child{font-size:24px;font-weight:700}small{color:#667}pre{font:13px monospace;white-space:pre-wrap;background:#edf0f3;padding:15px}nav{display:flex;gap:24px;margin:20px 0}</style>
<h1>這次接單的資料庫紀錄</h1><p>接完單後，我再查 MySQL，對照訂單狀態和接單的外送員。</p>
<nav><a href="http://127.0.0.1:5180/driver.html" target="_blank">外送員 #2 的接單頁</a><a href="http://127.0.0.1:5181/driver.html" target="_blank">外送員 #3 的接單頁</a><a href="/database">重新查詢資料庫</a></nav>
<table><thead><tr><th>買家</th><th>下單時間</th><th>明細</th><th>餐點</th><th>狀態</th><th>接單外送員</th></tr></thead><tbody>''' + rows + '''</tbody></table>
<p>一張訂單可以有多筆餐點；同買家、同下單時間的明細要一起看。</p><small>這是測試用的查詢頁，接單操作在上方連結的大廳。</small><p><small>127.0.0.1:3307 · ''' + html.escape(database) + ' · ' + payload['queried_at'] + '</small></p><details><summary>查看查詢 SQL</summary><pre>' + html.escape(payload['sql']) + '</pre></details></html>').encode()
                mime = 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    viewer = ThreadingHTTPServer(('127.0.0.1', 8767), Handler)
    print(json.dumps({'ready': True, 'folder': str(folder), 'database': database, 'orders': fixtures,
                      'couriers': seed['demo_courier_ids']}, ensure_ascii=False), flush=True)
    viewer.serve_forever()
finally:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    for log in logs:
        log.close()
