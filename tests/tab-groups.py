#!/usr/bin/env python3
"""Exercise tab groups, markers and existing per-space WebKit storage in a disposable app.

Build with `swift build`, then run `python3 tests/tab-groups.py`.
`--keep-running` leaves the owned probe open for visual acceptance; its PID,
profile, app and report paths are printed. No existing profile is reused.
"""

import argparse
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
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
BENCH = runpy.run_path(str(ROOT / "bench"))


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        label = html.escape(self.path)
        body = (f'<!doctype html><title>Spaces: {label}</title>'
                f'<h1>Spaces integration test</h1><p id="identity">{label}</p>'
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
        self.args = args
        self.origin = origin
        self.directory = Path(tempfile.mkdtemp(prefix="search-groups-"))
        self.profile = Path(BENCH["folder"](args.world))
        self.socket_path = str(self.profile / "bench.sock")
        if self.profile.exists():
            raise RuntimeError(f"refusing existing probe profile: {self.profile}")
        if len(self.socket_path.encode()) >= 104:
            raise RuntimeError("world name is too long for the Unix socket")
        self.suite = "com.officecommun.search.test." + args.world
        self.app = self.directory / "Search Groups Test.app"
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
        result = BENCH["ask"](self.socket_path, {"do": verb, **fields})
        if "error" in result:
            raise RuntimeError(f"{verb}: {result['error']}")
        return result

    def organize(self, action="state", **fields):
        return self.ask("organize", action=action, **fields)

    def reject(self, action, **fields):
        result = BENCH["ask"](self.socket_path, {"do": "organize", "action": action, **fields})
        self.check(bool(result.get("error")), action + " rejects invalid operation")

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

    @staticmethod
    def space(state, space_id):
        return next(space for space in state["spaces"] if space["id"] == space_id)

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
        info["CFBundleIdentifier"] = "com.officecommun.search.groups-test." + uuid.uuid4().hex
        info["CFBundleDisplayName"] = "Search Groups Test"
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
                {"url": self.origin + "/legacy-pin", "title": "Legacy pin", "pin": "Legacy", "name": "Pinned custom"},
                {"url": self.origin + "/legacy-selected", "title": "Legacy selected", "name": "Selected custom"},
                {"url": self.origin + "/legacy-last", "title": "Legacy last"},
            ], "active": 1,
        }))
        self.first = "00000000-0000-0000-0000-000000000001"
        self.shared = str(uuid.uuid4()).upper()
        self.isolated = str(uuid.uuid4()).upper()
        self.downloads = str(self.directory / "Downloads")
        self.legacy_spaces = [
            {"id": self.first, "name": "Personal", "colour": 0, "icon": "house", "sharesSignIns": True},
            {"id": self.shared, "name": "Shared legacy", "colour": 3, "icon": "terminal", "sharesSignIns": True, "downloads": self.downloads},
            {"id": self.isolated, "name": "Separate legacy", "colour": 5, "icon": "leaf", "sharesSignIns": False},
        ]
        (self.profile / "spaces.json").write_text(json.dumps(self.legacy_spaces))
        for space, path, name in [(self.shared, "/legacy-shared", "Shared custom"),
                                  (self.isolated, "/legacy-isolated", "Separate custom")]:
            (self.profile / ("session-" + space + ".json")).write_text(json.dumps({
                "tabs": [{"url": self.origin + path, "title": name, "name": name}], "active": 0,
            }))
        prefs = self.directory / "prefs.plist"
        prefs.write_bytes(plistlib.dumps({"bench": True, "welcomed": True, "spaces": True,
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

    def group(self, state, group):
        return next(item for item in state["groups"] if item["id"] == group)

    def restart(self):
        self.organize("save")
        self.stop()
        self.launch()
        return self.organize()

    def storage(self, tab, key, expected):
        actual = self.js(tab, f"({{cookie:document.cookie.split('; ').find(x=>x.startsWith('{key}='))?.split('=')[1] ?? null, "
                             f"local:localStorage.getItem('{key}')}})")
        self.check(actual == {"cookie": expected, "local": expected},
                   "real WebKit cookie and localStorage match " + repr(expected))

    def execute(self):
        self.prepare()
        self.launch()
        state = self.organize()
        self.check(state["spaceID"] == self.first and len(state["tabs"]) == 3,
                   "legacy first-space session restores without merging parked rows")
        self.check(self.tab(state, state["activeID"])["url"].endswith("/legacy-selected"),
                   "legacy numeric active selection restores correctly")
        self.check(self.tab(state, state["activeID"])["name"] == "Selected custom",
                   "legacy tab custom name survives migration")
        self.check(any(tab["pin"] == "Legacy" and tab["name"] == "Pinned custom" for tab in state["tabs"]),
                   "legacy pinned tab keeps its pin and custom name")
        self.check(state["groups"] == [], "sessions without groups remain ungrouped")
        self.legacy_metadata(state)
        for space, path, name in [(self.shared, "/legacy-shared", "Shared custom"),
                                  (self.isolated, "/legacy-isolated", "Separate custom")]:
            state = self.organize("select-space", space=space)
            self.check(len(state["tabs"]) == 1 and state["groups"] == []
                       and self.tab(state, state["activeID"])["name"] == name,
                       "legacy per-space session preserves only its own named tab")
            self.page(state["activeID"], path)
        self.organize("select-space", space=self.first)
        if self.args.only == "reopen":
            self.reopen_available()
            return
        if self.args.only == "close-neighbor":
            self.close_neighbor()
            return
        self.groups_and_storage()
        self.reopen_available()
        self.close_neighbor()
        self.report["finalState"] = self.organize()

    def close_neighbor(self):
        self.organize("create-space", name="Close order", separate=False)
        first = self.open("/neighbor-first")
        state = self.organize("create-group", name="Neighbors", id=first)
        group = self.tab(state, first)["groupID"]
        ungrouped = self.open("/neighbor-ungrouped")
        self.organize("assign-group", id=ungrouped, group=None)
        last = self.open("/neighbor-last")
        state = self.organize("assign-group", id=last, group=group)
        owned = {first, ungrouped, last}
        self.check([tab["id"] for tab in state["tabs"] if tab["id"] in owned] == [first, ungrouped, last]
                   and [tab_id for tab_id in state["presented"] if tab_id in owned] == [ungrouped, first, last],
                   "close-neighbor setup has different backing and displayed orders")
        self.organize("select-tab", id=first)
        state = self.organize("close-tab", id=first)
        self.check(state["activeID"] == last,
                   "closing grouped tab selects its displayed neighbor instead of backing-array neighbor")
        self.organize("save")

    def reopen_available(self):
        self.organize("select-space", space=self.first)
        eligible = self.open("/eligible-ghost")
        self.organize("rename-tab", id=eligible, name="Eligible named tab")
        state = self.organize("create-group", name="Reopened group", id=eligible)
        group = self.tab(state, eligible)["groupID"]
        self.organize("close-tab", id=eligible)
        self.organize("select-space", space=self.shared)
        unavailable = self.open("/disabled-space-ghost")
        self.organize("close-tab", id=unavailable)
        self.organize("enable-spaces", enabled=False)
        state = self.organize("reopen")
        active = self.tab(state, state["activeID"])
        self.check(state["spaceID"] == self.first and active["url"].endswith("/eligible-ghost")
                   and active["name"] == "Eligible named tab" and active["groupID"] == group,
                   "reopen skips unavailable-space ghosts and restores the newest eligible grouped tab")
        self.page(state["activeID"], "/eligible-ghost")
        self.organize("enable-spaces", enabled=True)
        self.organize("save")

    def legacy_metadata(self, state):
        persisted = json.loads((self.profile / "spaces.json").read_text())
        for legacy in self.legacy_spaces:
            current = self.space(state, legacy["id"])
            self.check(all(current.get(key) == legacy.get(key)
                           for key in ("name", "icon", "sharesSignIns", "downloads")),
                       "legacy space icon/sign-ins/downloads retained: " + legacy["name"])
            saved = next(item for item in persisted if item["id"] == legacy["id"])
            self.check(all(saved.get(key) == legacy.get(key)
                           for key in ("name", "colour", "icon", "sharesSignIns", "downloads")),
                       "spaces.json preserves existing legacy fields: " + legacy["name"])

    def groups_and_storage(self):
        key = "groups_" + uuid.uuid4().hex
        member = self.open("/group-member")
        self.organize("rename-tab", id=member, name="My research")
        self.js(member, f"document.cookie='{key}=shared; Path=/; Max-Age=3600'; "
                        f"localStorage.setItem('{key}', 'shared'); true")
        group_mark = {"kind": "emoji", "value": "👨‍👩‍👧‍👦"}
        state = self.organize("create-group", name="Research", id=member, mark=group_mark)
        group = self.tab(state, member)["groupID"]
        self.check(group is not None and self.group(state, group)["mark"] == group_mark,
                   "group creation assigns the tab and composed emoji marker")
        peer = self.open("/group-peer")
        self.organize("assign-group", id=peer, group=group)
        outside = self.open("/ungrouped")
        self.organize("assign-group", id=outside, group=None)
        state = self.organize("toggle-group", group=group)
        self.check(member not in state["presented"] and peer not in state["presented"]
                   and outside in state["presented"], "collapse hides members while retaining ungrouped tabs")
        for sidebar in (True, False):
            state = self.organize("sidebar", enabled=sidebar)
            self.check(member not in state["presented"], "collapse persists across sidebar and tab strip")
        state = self.organize("select-tab", id=member)
        self.check(member in state["presented"] and not self.group(state, group)["collapsed"],
                   "direct tab selection reveals its collapsed group")
        self.organize("select-tab", id=outside)
        self.organize("toggle-group", group=group)
        state = self.organize("group-tab", group=group)
        blank = state["activeID"]
        self.check(self.tab(state, blank)["groupID"] == group and blank in state["presented"],
                   "new group tab belongs to its revealed group")
        self.organize("close-tab", id=blank)
        state = self.organize("pin", id=peer)
        self.check(self.tab(state, peer)["pin"] is not None and self.tab(state, peer)["groupID"] is None,
                   "pinning removes group membership")
        self.organize("unpin", id=peer)
        state = self.organize("assign-group", id=peer, group=group)
        ordered = [tab["id"] for tab in state["tabs"]]
        for _ in range(2):
            target_index = ordered.index(member)
            expected = [tab_id for tab_id in ordered if tab_id != peer]
            expected.insert(target_index, peer)
            state = self.organize("reorder", id=peer, index=target_index)
            ordered = [tab["id"] for tab in state["tabs"]]
            self.check(ordered == expected and ordered.index(peer) == target_index,
                       "group member canonical reorder reaches requested index in both directions")
        target_index = ordered.index(outside)
        expected = [tab_id for tab_id in ordered if tab_id != peer]
        expected.insert(target_index, peer)
        state = self.organize("reorder", id=peer, index=target_index)
        ordered = [tab["id"] for tab in state["tabs"]]
        self.check(ordered == expected and self.tab(state, peer)["groupID"] == group,
                   "canonical tab movement crosses group boundaries without changing membership")
        self.organize("select-tab", id=member)
        state = self.organize("duplicate")
        duplicate = state["activeID"]
        self.check(duplicate != member and self.tab(state, duplicate)["groupID"] == group
                   and self.tab(state, duplicate)["name"] == "My research",
                   "duplicate preserves group and custom tab name")
        self.organize("close-tab", id=duplicate)
        state = self.organize("reopen")
        reopened = state["activeID"]
        self.check(self.tab(state, reopened)["groupID"] == group
                   and self.tab(state, reopened)["name"] == "My research",
                   "reopen restores group and custom tab name")
        state = self.organize("replace", id=reopened, url=self.origin + "/replaced-member")
        replaced = state["activeID"]
        self.check(self.tab(state, replaced)["groupID"] == group
                   and self.tab(state, replaced)["name"] == "My research",
                   "replacement preserves group and custom tab name")
        self.page(replaced, "/replaced-member")
        self.organize("select-tab", id=outside)
        self.organize("toggle-group", group=group)
        state = self.organize("select-space", space=self.shared)
        self.check(state["groups"] == [] and member not in {tab["id"] for tab in state["tabs"]},
                   "space switching exposes only the destination's own groups and tabs")
        shared_tab = self.open("/shared-storage")
        self.storage(shared_tab, key, "shared")
        state = self.organize("create-group", name="Shared group", id=shared_tab,
                              mark={"kind": "symbol", "value": "book"})
        shared_group = self.tab(state, shared_tab)["groupID"]
        self.reject("assign-group", id=shared_tab, group=group)
        self.check(self.tab(self.organize(), shared_tab)["groupID"] == shared_group,
                   "foreign group assignment preserves destination group")
        state = self.organize("select-space", space=self.isolated)
        self.check(state["groups"] == [], "separate-sign-in space has an independent group collection")
        isolated_tab = self.open("/isolated-storage")
        self.storage(isolated_tab, key, None)
        self.js(isolated_tab, f"document.cookie='{key}=isolated; Path=/; Max-Age=3600'; "
                             f"localStorage.setItem('{key}', 'isolated'); true")
        self.storage(isolated_tab, key, "isolated")
        state = self.organize("select-space", space=self.first)
        self.check(self.group(state, group)["collapsed"] and state["activeID"] == outside
                   and {item["id"] for item in state["groups"]} == {group},
                   "parked first-space groups, collapse and selection return intact")
        self.storage(member, key, "shared")
        self.organize("enable-spaces", enabled=False)
        state = self.organize("enable-spaces", enabled=True)
        self.check(state["spaceID"] == self.first and self.group(state, group)["collapsed"],
                   "toggling existing spaces feature preserves first-space grouping")
        state = self.organize("create-space", name="New shared", separate=False,
                              mark={"kind": "emoji", "value": "🚀"})
        created_shared = state["spaceID"]
        self.check(self.space(state, created_shared)["sharesSignIns"] is True
                   and self.space(state, created_shared)["mark"] == {"kind": "emoji", "value": "🚀"},
                   "new shared space accepts an emoji while retaining shared-sign-in semantics")
        self.storage(self.open("/created-shared"), key, "shared")
        state = self.organize("create-space", name="New separate", separate=True,
                              mark={"kind": "symbol", "value": "house"})
        created_separate = state["spaceID"]
        self.check(self.space(state, created_separate)["sharesSignIns"] is False
                   and self.space(state, created_separate)["mark"] == {"kind": "symbol", "value": "house"},
                   "new separate space accepts a symbol while retaining isolated-sign-in semantics")
        self.storage(self.open("/created-separate"), key, None)
        self.organize("select-space", space=self.first)
        self.markers(group, member)
        self.persistence(group, member, outside, shared_group, key)

    def markers(self, group, member):
        symbol = {"kind": "symbol", "value": "book"}
        flag = {"kind": "emoji", "value": "🇹🇷"}
        state = self.organize("set-space-mark", space=self.shared, mark=flag)
        self.check(self.space(state, self.shared)["mark"] == flag, "legacy space accepts a flag emoji without changing icon")
        self.organize("set-group-mark", group=group, mark=symbol)
        state = self.organize("rename-group", group=group, name="Renamed research")
        self.check(self.group(state, group)["mark"] == symbol, "group rename retains its marker")
        self.legacy_metadata(state)
        for invalid in [{"kind": "emoji", "value": "hello"}, {"kind": "emoji", "value": "1"},
                        {"kind": "emoji", "value": "🚀😀"}, {"kind": "symbol", "value": "unknown.symbol"}]:
            before = self.organize()
            self.reject("set-group-mark", group=group, mark=invalid)
            self.reject("set-space-mark", space=self.shared, mark=invalid)
            self.reject("create-group", name="Invalid", id=member, mark=invalid)
            self.reject("create-space", name="Invalid", separate=False, mark=invalid)
            state = self.organize()
            self.check(state["groups"] == before["groups"] and state["spaces"] == before["spaces"],
                       "invalid marker updates are atomic")
        self.organize("set-group-mark", group=group, mark=None)
        state = self.organize("set-space-mark", space=self.shared, mark=None)
        self.check(self.group(state, group)["mark"] is None and self.space(state, self.shared)["mark"] is None,
                   "clear marker restores existing group and legacy icon defaults")
        self.organize("set-space-mark", space=self.shared, mark=flag)
        self.organize("set-group-mark", group=group, mark={"kind": "emoji", "value": "👩🏽‍💻"})

    def persistence(self, group, member, outside, shared_group, key):
        private = self.organize("private-tab")["activeID"]
        self.ask("go", id=private, url=self.origin + "/private")
        self.page(private, "/private")
        self.storage(private, key, None)
        self.ask("open", url=self.origin + "/bench")
        # A persisted ordinary selection behind filtered entries must not use
        # its index in the unfiltered live list when restoring the session.
        selected = self.open("/selected-after-private")
        self.check(not self.tab(self.organize(), selected)["shy"],
                   "ordinary tab created after private entries remains persistable")
        self.organize("rename-tab", id=selected, name="Persisted selection")
        self.open("/after-selected")
        self.organize("select-tab", id=selected)
        for index in range(20):
            self.organize("rename-group", group=group, name=f"Write order {index}")
            self.organize("save")
        self.organize("rename-group", group=group, name="Final saved name")
        self.organize("save")
        time.sleep(1.5)
        saved = json.loads((self.profile / "session.json").read_text())
        self.check(next(item for item in saved["groups"] if item["id"] == group)["name"] == "Final saved name",
                   "serialized session writes preserve the latest grouping mutation")
        self.check(not any(tab["url"].endswith(("/private", "/bench")) for tab in saved["tabs"]),
                   "session file excludes private and bench tabs")
        state = self.restart()
        self.check(state["spaceID"] == self.first
                   and self.tab(state, state["activeID"])["url"].endswith("/selected-after-private")
                   and self.tab(state, state["activeID"])["name"] == "Persisted selection",
                   "restart restores filtered active tab and custom name")
        self.check(not any(tab["shy"] or tab["bench"] for tab in state["tabs"]),
                   "private and bench tabs never return on restart")
        self.check(self.group(state, group)["name"] == "Final saved name"
                   and self.group(state, group)["collapsed"]
                   and self.group(state, group)["mark"] == {"kind": "emoji", "value": "👩🏽‍💻"},
                   "per-space session restores group identity, collapse and composed emoji")
        restored_member = next(tab for tab in state["tabs"] if tab["url"].endswith("/group-member"))
        self.check(restored_member["name"] == "My research" and restored_member["groupID"] == group,
                   "named tab restores group membership")
        self.legacy_metadata(state)
        state = self.organize("select-space", space=self.shared)
        self.check({item["id"] for item in state["groups"]} == {shared_group}
                   and self.space(state, self.shared)["mark"] == {"kind": "emoji", "value": "🇹🇷"},
                   "parked per-space group session and space emoji restore independently")
        self.page(state["activeID"], "/shared-storage")
        self.storage(state["activeID"], key, "shared")
        state = self.organize("select-space", space=self.isolated)
        self.page(state["activeID"], "/isolated-storage")
        self.storage(state["activeID"], key, "isolated")
        state = self.organize("select-space", space=self.first)
        before = {tab["id"] for tab in state["tabs"]}
        state = self.organize("remove-group", group=group)
        self.check({tab["id"] for tab in state["tabs"]} == before
                   and all(tab["groupID"] != group for tab in state["tabs"]),
                   "removing group keeps its member tabs and names")
        self.organize("save")
        self.report["finalState"] = self.organize()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default=str(ROOT / ".build/debug/Search"))
    parser.add_argument("--world", default="groups-" + uuid.uuid4().hex[:8])
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--only", choices=("reopen", "close-neighbor"), help="run a focused regression after legacy migration")
    args = parser.parse_args()
    if not args.world or args.world in ("1", "test") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.world):
        parser.error("world must be a unique lowercase ASCII name (not 1 or test)")
    socket.setdefaulttimeout(30)
    server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = Run(args, f"http://127.0.0.1:{server.server_port}")
    code = 0
    try:
        run.execute()
        run.report["ok"] = True
    except (Exception, SystemExit) as error:
        run.report.update(ok=False, error=str(error))
        print("FAIL " + str(error), flush=True)
        code = 1
    finally:
        if not args.keep_running:
            if run.process and run.process.poll() is None:
                try:
                    run.organize("save")
                except (Exception, SystemExit):
                    pass
            run.stop()
        run.report["keptRunning"] = bool(args.keep_running and run.process and run.process.poll() is None)
        report_path = run.directory / "report.json"
        report_path.write_text(json.dumps(run.report, indent=2))
        print(json.dumps({"ok": run.report["ok"], "report": str(report_path),
                          "pid": run.report.get("pid"), "keptRunning": run.report["keptRunning"]}), flush=True)
        server.shutdown()
        server.server_close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
