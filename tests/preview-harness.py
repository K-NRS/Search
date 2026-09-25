#!/usr/bin/env python3
"""Shared disposable-app harness for real WebKit Mini and Peek acceptance.

This module has no executable test flow and never uses the installed app.
Each run has a unique bundle, settings suite, WebKit container and probe profile.
"""

import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import plistlib
import runpy
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]
BENCH = runpy.run_path(str(ROOT / "bench"))


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        label = html.escape(self.path)
        body = (f'<!doctype html><title>Preview: {label}</title>'
                f'<h1>Preview integration test</h1><p id="identity">{label}</p>'
                '<a id="popup" href="/popup" target="_blank">Open another tab</a>').encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Run:
    def __init__(self, args, origin):
        if (not args.world or args.world in ("1", "test")
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.world)):
            raise RuntimeError("world must be a unique lowercase ASCII name (not 1 or test)")
        self.args = args
        self.origin = origin
        self.directory = Path(tempfile.mkdtemp(prefix="search-previews-"))
        self.profile = Path(BENCH["folder"](args.world))
        self.socket_path = str(self.profile / "bench.sock")
        if self.profile.exists():
            raise RuntimeError(f"refusing existing probe profile: {self.profile}")
        if len(self.socket_path.encode()) >= 104:
            raise RuntimeError("world name is too long for the Unix socket")
        self.suite = "com.officecommun.search.test." + args.world
        self.app = self.directory / "Search Preview Test.app"
        self.process = None
        self.log = None
        self.report = {"world": args.world, "directory": str(self.directory),
                       "profile": str(self.profile), "checks": []}

    def check(self, condition, message):
        self.report["checks"].append({"ok": bool(condition), "message": message})
        print(("PASS " if condition else "FAIL ") + message, flush=True)
        if not condition:
            raise AssertionError(message)

    def ask(self, verb, **fields):
        if verb == "native":
            verb = "preview-native"
        result = BENCH["ask"](self.socket_path, {"do": verb, **fields})
        if "error" in result:
            raise RuntimeError(f"{verb}: {result['error']}")
        return result

    def organize(self, action="state", **fields):
        return self.ask("preview", action="main-" + action, **fields)

    def js(self, tab, script):
        return self.ask("eval", id=tab, js=script).get("value")

    def page(self, tab, path):
        deadline = time.monotonic() + 20
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.js(tab, "({path:location.pathname, ready:document.readyState, "
                              "identity:document.querySelector('#identity')?.textContent})")
                if last and last.get("path") == path and last.get("ready") == "complete":
                    self.check(last.get("identity") == path, "real DOM loaded: " + path)
                    return
            except RuntimeError:
                pass
            time.sleep(0.1)
        raise AssertionError(f"page never loaded {path}: {last}")

    @staticmethod
    def tab(state, tab_id):
        return next(tab for tab in state["tabs"] if tab["id"] == tab_id)

    def open(self, path):
        state = self.organize("open", url=self.origin + path)
        tab = state["activeID"]
        self.page(tab, path)
        return tab

    def prepare(self):
        source = ROOT / "build/Search.app"
        binary = Path(self.args.binary).resolve()
        if not source.is_dir() or not binary.is_file():
            raise RuntimeError("build/Search.app and the selected executable must exist")
        subprocess.run(["ditto", str(source), str(self.app)], check=True, capture_output=True)
        shutil.copy2(binary, self.app / "Contents/MacOS/Search")
        self.report["binary"] = str(binary)
        self.report["binarySha256"] = hashlib.sha256((self.app / "Contents/MacOS/Search").read_bytes()).hexdigest()
        plist_path = self.app / "Contents/Info.plist"
        info = plistlib.loads(plist_path.read_bytes())
        info["CFBundleIdentifier"] = "com.officecommun.search.preview-test." + uuid.uuid4().hex
        info["CFBundleDisplayName"] = "Search Preview Test"
        # This test copy must never register itself as a candidate default browser.
        info.pop("CFBundleURLTypes", None)
        info.pop("CFBundleDocumentTypes", None)
        plist_path.write_bytes(plistlib.dumps(info))
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(self.app)],
                       check=True, capture_output=True)
        self.report["bundleID"] = info["CFBundleIdentifier"]
        self.profile.mkdir(parents=True)
        (self.profile / "session.json").write_text(json.dumps({
            "tabs": [
                {"url": self.origin + "/legacy-pin", "title": "Legacy pin", "pin": "Legacy"},
                {"url": self.origin + "/legacy-selected", "title": "Legacy selected"},
                {"url": self.origin + "/legacy-last", "title": "Legacy last"},
            ], "active": 1,
        }))
        prefs = self.directory / "prefs.plist"
        prefs.write_bytes(plistlib.dumps({"bench": True, "welcomed": True,
            "update.checked": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)}))
        subprocess.run(["defaults", "import", self.suite, str(prefs)], check=True, capture_output=True)

    def launch(self):
        self.log = (self.directory / "app.log").open("ab")
        self.process = subprocess.Popen([str(self.app / "Contents/MacOS/Search")],
            env={**os.environ, "SEARCH_PROBE": self.args.world}, stdout=self.log, stderr=self.log,
            start_new_session=True)
        self.report["pid"] = self.process.pid
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"owned app exited with {self.process.returncode}; see app.log")
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.connect(self.socket_path)
                self.ask("tabs")
                return
            except (OSError, ValueError, SystemExit):
                time.sleep(0.1)
        raise RuntimeError("owned app never opened its bench socket")

    def stop(self):
        if self.process and self.process.poll() is None:
            # Only the subprocess launched above is eligible for termination.
            self.process.terminate()
            self.process.wait(timeout=15)
        if self.log:
            self.log.close()
            self.log = None

    def storage(self, tab, key, expected):
        value = self.js(tab, f"({{cookie:document.cookie.split('; ').find(x=>x.startsWith('{key}='))?.split('=')[1] ?? null, "
                             f"local:localStorage.getItem('{key}')}})")
        self.check(value == {"cookie": expected, "local": expected},
                   "real cookie and localStorage match " + repr(expected))
