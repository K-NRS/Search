#!/usr/bin/env python3
"""Exercise real WebKit extension tabs in an isolated, production-policy run.

Start Search with SEARCH_PROBE=<world> SEARCH_MEASURE=1 and enable its bench,
then run: python3 tests/background-tabs.py --world <world>
No page evaluation is performed on background targets: that would wake them
and hide suspension bugs. The temporary extension uses real browser APIs.
"""

import argparse
import json
from pathlib import Path
import runpy
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
BENCH = runpy.run_path(str(ROOT / "bench"))

HOST = r"""
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const updates = [];
const removed = [];
chrome.tabs.onUpdated.addListener((id, info) => updates.push({id, ...info}));
chrome.tabs.onRemoved.addListener(id => removed.push(id));
window.start = async function(url, delay) {
  const original = (await chrome.tabs.query({active: true, currentWindow: true}))[0]?.id;
  const created = [];
  const result = {checks: [], errors: []};
  const check = (ok, message) => {
    result.checks.push({ok: !!ok, message});
    if (!ok) result.errors.push(message);
  };
  try {
    for (let i = 0; i < 2; ++i) {
      const tab = await chrome.tabs.create({url: url + '/page?case=' + i + '&delay=' + delay, active: false});
      created.push(tab.id);
      check(tab.active === false, 'created tab stays inactive');
    }
    for (let i = 0; i < 100; ++i) {
      if (created.every(id => updates.some(e => e.id === id && e.status === 'complete'))) break;
      await pause(100);
    }
    check(created.every(id => updates.some(e => e.id === id && e.status === 'complete')),
      'both background tabs emit loading completion');
  } catch (error) {
    result.errors.push(String(error));
  }
  window.started = {created, original, ...result};
};
window.finish = async function(result) {
  const {created, original} = result;
  const check = (ok, message) => {
    result.checks.push({ok: !!ok, message});
    if (!ok) result.errors.push(message);
  };
  try {
    for (const id of created) {
      const reply = await Promise.race([
        chrome.tabs.sendMessage(id, {type: 'BACKGROUND_PROBE'}),
        pause(8000).then(() => ({timeout: true}))
      ]);
      check(reply?.ready === true, 'delayed content work completes without selecting the tab');
      check(reply?.title === 'Background work completed', 'background DOM timer runs after load');
      check((await chrome.tabs.get(id)).active === false, 'messaging does not activate the tab');
    }
    check((await chrome.tabs.query({active: true, currentWindow: true}))[0]?.id === original,
      'foreground selection remains unchanged');
  } catch (error) {
    result.errors.push(String(error));
  } finally {
    for (const id of created) await chrome.tabs.remove(id).catch(() => {});
    for (let i = 0; i < 50 && !created.every(id => removed.includes(id)); ++i) await pause(100);
    check(created.every(id => removed.includes(id)), 'temporary tabs emit removal');
    const remaining = await chrome.tabs.query({});
    check(created.every(id => !remaining.some(tab => tab.id === id)), 'temporary tabs are released');
  }
  window.result = result;
};
"""

CONTENT = r"""
let ready = false;
window.addEventListener('load', () => {
  const params = new URLSearchParams(location.search);
  setTimeout(() => {
    document.title = 'Background work completed';
    ready = true;
    fetch('/completed?case=' + params.get('case'), {method: 'POST'});
  }, Number(params.get('delay')));
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type !== 'BACKGROUND_PROBE') return;
  setTimeout(() => respond({ready, title: document.title}), 1500);
  return true;
});
"""


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>Background probe</title><p>Background work probe</p>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        from urllib.parse import parse_qs, urlparse
        case = parse_qs(urlparse(self.path).query).get('case', [''])[0]
        with self.server.completed_lock:
            self.server.completed.add(case)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True, help="isolated SEARCH_PROBE world")
    parser.add_argument("--delay-seconds", type=float, default=25, help="autonomous work delay; use 300 for long idle coverage")
    args = parser.parse_args()
    socket.setdefaulttimeout(30)
    if not 0 < args.delay_seconds <= 600:
        parser.error("delay-seconds must be between 0 and 600")
    if not args.world or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.world):
        parser.error("world must contain lowercase ASCII letters, digits or hyphens")
    socket_path = str(Path(BENCH["folder"](args.world)) / "bench.sock")

    def ask(verb, **fields):
        response = BENCH["ask"](socket_path, {"do": verb, **fields})
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    def poll(js, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = ask("eval", id=host_id, js=js).get("value")
            if value:
                return value
            time.sleep(0.2)
        raise RuntimeError("test controller timed out: " + js)

    def open_host():
        nonlocal host_id
        host_id = ask("ext-page", id=extension_id, path="host.html")["id"]
        poll("typeof window.start === 'function'")

    server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
    server.completed = set()
    server.completed_lock = threading.Lock()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    extension_id = None
    host_id = None
    try:
        with tempfile.TemporaryDirectory(prefix="search-background-tabs-") as folder:
            directory = Path(folder)
            manifest = {
                "manifest_version": 3,
                "name": "Search background tab regression",
                "description": "Verify inactive tab loading, background work, messaging and cleanup.",
                "version": "1.0",
                "permissions": ["tabs"],
                "host_permissions": ["http://127.0.0.1/*"],
                "content_scripts": [{
                    "matches": ["http://127.0.0.1/*"],
                    "js": ["content.js"],
                    "run_at": "document_start",
                }],
            }
            (directory / "manifest.json").write_text(json.dumps(manifest))
            (directory / "host.html").write_text('<!doctype html><title>Regression controller</title><script src="host.js"></script>')
            (directory / "host.js").write_text(HOST)
            (directory / "content.js").write_text(CONTENT)
            ask("ext-folder", path=folder, yes=True)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                extensions = ask("extensions")
                match = next((item for item in extensions["extensions"] if item.get("source") == folder), None)
                if match:
                    extension_id = match["id"]
                    if match["loaded"]:
                        break
                time.sleep(0.2)
            else:
                raise RuntimeError("test extension did not load")
            open_host()
            ask("eval", id=host_id, js=f"window.start({json.dumps(origin)}, {args.delay_seconds * 1000}); true")
            result = poll("window.started || null", 15)
            # A housed/evaluated controller could keep a shared WebKit process
            # alive. Close it during the idle interval, not just the targets.
            ask("close", id=host_id)
            host_id = None
            time.sleep(args.delay_seconds + 5)
            with server.completed_lock:
                autonomous = server.completed == {"0", "1"}
            message = "both tabs perform autonomous work while the controller is closed"
            result["checks"].append({"ok": autonomous, "message": message})
            if not autonomous:
                result["errors"].append(message)
            open_host()
            ask("eval", id=host_id, js=f"window.finish({json.dumps(result)}); true")
            result = poll("window.result || null", 30)
            print(json.dumps(result, indent=2))
            return 1 if result["errors"] else 0
    finally:
        # The run's own finally closes its targets. On a timeout, remove only
        # this test's local-server tabs, using the extension's real tab API.
        cleanup_errors = []
        if extension_id and not host_id:
            try:
                open_host()
            except Exception as error:
                cleanup_errors.append(str(error))
        if host_id:
            try:
                ask("eval", id=host_id, js="window.cleanup = null; chrome.tabs.query({}).then(tabs => Promise.all(tabs.filter(tab => tab.url?.startsWith(" + json.dumps(origin + "/") + ")).map(tab => chrome.tabs.remove(tab.id)))).then(() => window.cleanup = {done: true}, error => window.cleanup = {error: String(error)}); true")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    cleanup = ask("eval", id=host_id, js="window.cleanup").get("value")
                    if cleanup:
                        if cleanup.get("error"):
                            raise RuntimeError(cleanup["error"])
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("temporary tab cleanup timed out")
            except Exception as error:
                cleanup_errors.append(str(error))
            try:
                ask("close", id=host_id)
            except Exception as error:
                cleanup_errors.append(str(error))
        try:
            if extension_id:
                ask("ext-remove", id=extension_id)
        except Exception as error:
            cleanup_errors.append(str(error))
        finally:
            server.shutdown()
            server.server_close()
        if cleanup_errors:
            raise RuntimeError("cleanup failed: " + "; ".join(cleanup_errors))


if __name__ == "__main__":
    raise SystemExit(main())
