#!/usr/bin/env python3
"""Exercise real WKWebExtension Mini/Peek membership in a disposable app.

Build with ./build.sh debug, then python3 tests/preview-extensions.py. The isolated
launcher and local HTTP fixture come from preview-harness.py. No browser APIs are mocked.
"""
import argparse
import json
from pathlib import Path
import runpy
import socket
import threading
import time
import uuid
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
SUITE = runpy.run_path(str(ROOT / "tests/preview-harness.py"))


class PreviewExtensions(SUITE["Run"]):
    def preview(self, action="state", **fields):
        return self.ask("preview", action=action, **fields)

    def wait_value(self, read, predicate, description):
        deadline = time.monotonic() + 15
        value = None
        while time.monotonic() < deadline:
            value = read()
            if predicate(value):
                return value
            time.sleep(.1)
        raise AssertionError(description + ": " + json.dumps(value))

    def api(self, expression):
        self.js(self.host, "window.apiResult = null; Promise.resolve().then(() => ("
                + expression + ")).then(value => {window.apiResult = {value};}, "
                "error => {window.apiResult = {error:String(error)};}); true")
        result = self.wait_value(lambda: self.js(self.host, "window.apiResult"),
                                 lambda value: value is not None, "extension API timed out")
        if "error" in result:
            raise AssertionError("extension API failed: " + result["error"])
        return result.get("value")

    def snapshot(self):
        value = self.api("Promise.all([chrome.windows.getAll({populate:true}), chrome.tabs.query({})])"
                         ".then(([windows,tabs])=>({windows,tabs}))")
        self.report["lastExtensionState"] = value
        return value

    def by_path(self, snapshot, path):
        found = next((tab for tab in snapshot["tabs"] if tab.get("url") == self.origin + path), None)
        if found is None:
            raise AssertionError("extension did not enumerate " + path + ": " + json.dumps(snapshot))
        return found

    def events(self):
        return self.js(self.host, "window.lifecycle")

    def clear_events(self):
        self.js(self.host, "window.lifecycle = []; true")

    def event(self, kind, identity):
        return self.wait_value(self.events,
            lambda events: any(e["kind"] == kind and e["id"] == identity for e in events),
            f"missing {kind} event for {identity}")

    def mini(self, path, source=None):
        before = {tab["id"] for tab in self.preview()["tabs"]}
        fields = {"url": self.origin + path}
        if source:
            fields["source"] = source
        state = self.preview("mini", **fields)
        tab = next(tab["id"] for tab in state["tabs"] if tab["id"] not in before)
        self.page(tab, path)
        return tab

    def install_observer(self, host_access=True):
        extension = self.directory / ("preview-observer" if host_access else "tabs-only-observer")
        extension.mkdir()
        manifest = {
            "manifest_version": 3, "name": "Preview membership acceptance", "version": "1.0",
            "permissions": ["tabs"],
        }
        if host_access:
            manifest["host_permissions"] = ["http://127.0.0.1/*"]
        (extension / "manifest.json").write_text(json.dumps(manifest))
        (extension / "host.html").write_text(
            '<!doctype html><title>Preview observer</title><script src="host.js"></script>')
        (extension / "host.js").write_text("""
window.lifecycle = [];
chrome.tabs.onCreated.addListener(tab => lifecycle.push({kind:'created',id:tab.id,windowId:tab.windowId}));
chrome.tabs.onRemoved.addListener((id,info) => lifecycle.push({kind:'removed',id,...info}));
chrome.tabs.onDetached.addListener((id,info) => lifecycle.push({kind:'detached',id,...info}));
chrome.tabs.onAttached.addListener((id,info) => lifecycle.push({kind:'attached',id,...info}));
chrome.tabs.onActivated.addListener(info => lifecycle.push({kind:'activated',id:info.tabId,...info}));
chrome.windows.onCreated.addListener(win => lifecycle.push({kind:'window-created',id:win.id}));
chrome.windows.onRemoved.addListener(id => lifecycle.push({kind:'window-removed',id}));
window.ready = true;
""")
        self.ask("ext-folder", path=str(extension), yes=True)
        metadata = self.wait_value(
            lambda: next((item for item in self.ask("extensions")["extensions"]
                          if item.get("source") == str(extension) and item.get("loaded")), None),
            lambda value: value is not None, "observer extension did not load")
        self.host = self.ask("ext-page", id=metadata["id"], path="host.html")["id"]
        self.wait_value(lambda: self.js(self.host, "window.ready === true"),
                        bool, "observer extension listeners did not initialize")

    def execute(self):
        self.prepare()
        self.launch()
        if self.args.only == "last-blank":
            self.last_blank_main()
            return
        source = self.open("/extension-source")
        self.install_observer()
        if self.args.only == "private":
            self.private_visibility()
            return
        baseline = self.snapshot()
        self.check(len(baseline["windows"]) == 1, "extension initially enumerates one real main window")
        main_id = self.by_path(baseline, "/extension-source")["windowId"]
        self.check(self.by_path(baseline, "/extension-source")["active"], "main source initially active")

        self.clear_events()
        calibration = self.open("/calibration")
        calibration_id = self.by_path(self.snapshot(), "/calibration")["id"]
        self.event("created", calibration_id)
        self.organize("close-tab", id=calibration)
        self.event("removed", calibration_id)
        self.check(True, "real extension listeners observe calibration tab creation and removal")
        self.organize("select-tab", id=source)

        mini = self.mini("/mini-promote")
        state = self.snapshot()
        mini_tab = self.by_path(state, "/mini-promote")
        mini_id, mini_window = mini_tab["id"], mini_tab["windowId"]
        self.event("window-created", mini_window)
        self.check(len(state["windows"]) == 2 and mini_window != main_id,
                   "Mini is reported in its own real extension window")
        mini_description = next(win for win in state["windows"] if win["id"] == mini_window)
        self.check(mini_description["type"] == "popup" and
                   [tab["id"] for tab in mini_description["tabs"]] == [mini_id],
                   "Mini popup window contains only its own page")
        self.check(mini_tab["index"] == 0 and mini_tab["active"] and
                   self.by_path(state, "/extension-source")["active"],
                   "Mini and main each retain their own active page and local index")

        # Without host access WebKit can withhold Mini metadata. The fallback
        # must leave it withheld, never borrow a main tab with the same index.
        full_host = self.host
        self.install_observer(host_access=False)
        tabs_only = self.snapshot()
        mini_only_window = next(win for win in tabs_only["windows"] if win["type"] == "popup")
        mini_only = next(tab for tab in tabs_only["tabs"] if tab["windowId"] == mini_only_window["id"])
        self.check(mini_only.get("url", "") in ("", self.origin + "/mini-promote") and
                   mini_only.get("title", "") in ("", "Preview: /mini-promote"),
                   "tabs-only extension never labels Mini with a main tab's metadata")
        self.report["tabsOnlyMiniMetadata"] = mini_only
        after_mini = self.open("/main-after-mini")
        self.check(self.by_path(self.snapshot(), "/main-after-mini")["windowId"] != mini_only_window["id"],
                   "main metadata indexes stay correct after Mini interleaves in browser ownership")
        self.organize("close-tab", id=after_mini)
        self.organize("select-tab", id=source)
        before_order = [tab["id"] for tab in self.preview()["tabs"]]
        for operation in ("discard", "move"):
            extra = ",{index:1}" if operation == "move" else ""
            result = self.api("chrome.tabs." + operation + "(" + str(mini_only["id"]) + extra
                              + ").then(()=>({rejected:false}),error=>({rejected:true,message:String(error)}))")
            self.check(result["rejected"], "Mini " + operation + " cannot act on the main tab at index zero")
        self.check([tab["id"] for tab in self.preview()["tabs"]] == before_order,
                   "rejected Mini operations preserve every page and its order")
        self.api("chrome.tabs.highlight({windowId:" + str(mini_only_window["id"]) + ",tabs:0})")
        self.check(self.preview()["activeID"] == source,
                   "highlighting Mini leaves the underlying main selection intact")
        self.host = full_host
        rejected = self.api("chrome.tabs.create({windowId:" + str(mini_window)
                            + ",url:'about:blank'}).then(()=>({rejected:false}),"
                            "error=>({rejected:true,message:String(error)}))")
        self.check(rejected["rejected"] and "Mini" in rejected["message"],
                   "extension cannot silently insert an extra tab into a Mini")

        self.clear_events()
        self.preview("promote", id=mini)
        self.event("detached", mini_id)
        self.event("attached", mini_id)
        self.event("window-removed", mini_window)
        time.sleep(.4)
        events = self.events()
        self.report["miniPromotionEvents"] = events
        after = self.snapshot()
        promoted = self.by_path(after, "/mini-promote")
        self.check(promoted["id"] == mini_id and promoted["windowId"] == main_id and promoted["active"],
                   "Mini promotion preserves extension tab identity and moves it to main")
        self.check(not any(event["kind"] in ("created", "removed") and event["id"] == mini_id
                           for event in events), "Mini promotion emits no false tab creation or removal")
        detached = next(event for event in events if event["kind"] == "detached" and event["id"] == mini_id)
        attached = next(event for event in events if event["kind"] == "attached" and event["id"] == mini_id)
        self.check(detached["oldWindowId"] == mini_window and attached["newWindowId"] == main_id,
                   "Mini promotion events identify the real old and new windows")
        self.check(len(after["windows"]) == 1, "promoted Mini window disappears from enumeration")

        self.cross_space_promotion(source, main_id)

        closed = self.mini("/mini-close")
        closed_tab = self.by_path(self.snapshot(), "/mini-close")
        self.clear_events()
        self.api("chrome.windows.remove(" + str(closed_tab["windowId"]) + ")")
        self.event("removed", closed_tab["id"])
        self.event("window-removed", closed_tab["windowId"])
        removal = next(event for event in self.events() if event["kind"] == "removed"
                       and event["id"] == closed_tab["id"])
        self.check(removal["isWindowClosing"] and not any(tab["id"] == closed for tab in self.preview()["tabs"]),
                   "extension windows.remove closes Mini with a truthful window-closing tab event")

        self.organize("select-tab", id=source)
        peek = self.preview("peek", source=source, url=self.origin + "/peek-active")["peekID"]
        self.page(peek, "/peek-active")
        state = self.snapshot()
        peek_tab = self.by_path(state, "/peek-active")
        main = next(win for win in state["windows"] if win["id"] == main_id)
        self.check(peek_tab["windowId"] == main_id and peek_tab["active"] and
                   not self.by_path(state, "/extension-source")["active"] and
                   [tab["id"] for tab in main["tabs"] if tab["active"]] == [peek_tab["id"]],
                   "Peek is the main window's sole active extension tab")
        self.check(self.preview()["activeID"] == source, "Peek leaves underlying main selection intact")
        self.clear_events()
        self.preview("promote", id=peek)
        time.sleep(.4)
        promoted_peek = self.by_path(self.snapshot(), "/peek-active")
        self.check(promoted_peek["id"] == peek_tab["id"] and promoted_peek["windowId"] == main_id,
                   "Peek promotion preserves its identity and main-window membership")
        self.check(not any(event["kind"] in ("created", "removed", "detached", "attached")
                           and event["id"] == peek_tab["id"] for event in self.events()),
                   "Peek promotion emits no synthetic tab lifecycle or cross-window events")

        self.organize("select-tab", id=source)
        peek = self.preview("peek", source=source, url=self.origin + "/peek-dismiss")["peekID"]
        self.page(peek, "/peek-dismiss")
        self.preview("close", id=peek)
        state = self.snapshot()
        self.check(self.by_path(state, "/extension-source")["active"],
                   "dismissing Peek restores the underlying extension-active tab")

        self.private_visibility()
        self.report["finalExtensionState"] = self.snapshot()
        self.last_blank_main()

    def private_visibility(self):
        self.preview("settings", extensionsInPrivate=False)
        private = self.organize("private-tab")["activeID"]
        self.ask("go", id=private, url=self.origin + "/private-source")
        self.page(private, "/private-source")
        private_mini = self.mini("/private-mini", source=private)
        private_peek = self.preview("peek", source=private, url=self.origin + "/private-peek")["peekID"]
        self.page(private_peek, "/private-peek")
        private_state = self.snapshot()
        self.report["privateDisabledState"] = private_state
        self.check(len(private_state["windows"]) == 1 and
                   not any("/private-" in tab.get("url", "") for tab in private_state["tabs"]),
                   "private source, Mini and Peek are excluded from extension enumeration")
        self.preview("close", id=private_peek)

        self.preview("settings", extensionsInPrivate=True)
        enabled = self.organize("private-tab")["activeID"]
        self.ask("go", id=enabled, url=self.origin + "/enabled-private-source")
        self.page(enabled, "/enabled-private-source")
        enabled_mini = self.mini("/enabled-private-mini", source=enabled)
        enabled_peek = self.preview("peek", source=enabled, url=self.origin + "/enabled-private-peek")["peekID"]
        self.page(enabled_peek, "/enabled-private-peek")
        opted_in = self.snapshot()
        self.report["privateEnabledState"] = opted_in
        source_tab = self.by_path(opted_in, "/enabled-private-source")
        mini_tab = self.by_path(opted_in, "/enabled-private-mini")
        peek_tab = self.by_path(opted_in, "/enabled-private-peek")
        self.check(mini_tab["windowId"] != source_tab["windowId"] and
                   peek_tab["windowId"] == source_tab["windowId"] and len(opted_in["windows"]) == 2,
                   "explicit private-extension opt-in includes new private main, Mini and Peek pages")
        self.check(not any(tab.get("url") in (self.origin + "/private-source", self.origin + "/private-mini")
                           for tab in opted_in["tabs"]),
                   "private-extension opt-in never retroactively exposes pages created without a controller")
        self.preview("close", id=enabled_peek)
        self.preview("close", id=enabled_mini)
        self.organize("close-tab", id=enabled)
        self.preview("settings", extensionsInPrivate=False)
        self.preview("close", id=private_mini)
        self.organize("close-tab", id=private)

    def cross_space_promotion(self, source, main_id):
        original_space = self.preview()["activeSpaceID"]
        origin_space = self.organize("create-space", name="Preview origin", separate=True)["activeSpaceID"]
        origin_source = self.open("/cross-space-source")
        mini = self.mini("/cross-space-mini", source=origin_source)
        self.js(mini, "document.body.insertAdjacentHTML('beforeend','<input id=cross-space-form>'); "
                "document.querySelector('#cross-space-form').value='unsubmitted across spaces'; true")
        mini_tab = self.by_path(self.snapshot(), "/cross-space-mini")
        mini_id, mini_window = mini_tab["id"], mini_tab["windowId"]
        self.organize("select-space", space=original_space)
        self.organize("select-tab", id=source)
        peek = self.preview("peek", source=source, url=self.origin + "/other-space-peek")["peekID"]
        self.page(peek, "/other-space-peek")
        before = self.preview()
        self.check(before["activeSpaceID"] == original_space and before["peekID"] == peek and
                   self.tab(before, mini)["spaceID"] == origin_space,
                   "cross-space regression has an origin-space Mini and another space's Peek")
        self.check(self.by_path(self.snapshot(), "/cross-space-mini")["id"] == mini_id,
                   "switching main spaces retains Mini's extension identity")
        time.sleep(.2)
        self.clear_events()
        self.preview("promote", id=mini)
        self.event("detached", mini_id)
        self.event("attached", mini_id)
        self.event("window-removed", mini_window)
        time.sleep(.4)
        events = self.events()
        self.report["crossSpacePromotionEvents"] = events
        after = self.preview()
        promoted = self.by_path(self.snapshot(), "/cross-space-mini")
        self.check(after["activeSpaceID"] == origin_space and after["activeID"] == mini and
                   after["peekID"] is None and not any(tab["id"] == peek for tab in after["tabs"]),
                   "Mini promotion selects its original space and closes the other space's Peek")
        self.check(promoted["id"] == mini_id and promoted["windowId"] == main_id and promoted["active"],
                   "cross-space promotion preserves the extension adapter and selects the promoted page")
        self.check(not any(event["kind"] in ("created", "removed") and event["id"] == mini_id
                           for event in events),
                   "nested Peek close never emits false Mini tab creation or removal during promotion")
        self.check(self.js(mini, "document.querySelector('#cross-space-form').value")
                   == "unsubmitted across spaces", "cross-space promotion preserves the edited form")
        attached_at = next(index for index, event in enumerate(events)
                           if event["kind"] == "attached" and event["id"] == mini_id)
        closed_at = next(index for index, event in enumerate(events)
                         if event["kind"] == "window-removed" and event["id"] == mini_window)
        self.check(attached_at < closed_at, "cross-space promotion moves the page before closing Mini's extension window")
        self.organize("select-space", space=original_space)
        self.organize("select-tab", id=source)

    def last_blank_main(self):
        blank = self.organize("new-tab")["activeID"]
        initial = self.organize()
        for tab in initial["tabs"]:
            if tab["id"] not in initial["visible"] or tab["id"] == blank:
                continue
            if tab["pin"] is not None:
                self.organize("unpin", id=tab["id"])
            self.organize("close-tab", id=tab["id"])
        state = self.organize()
        self.check(state["visible"] == [blank] and state["activeID"] == blank,
                   "close-owner regression starts with exactly one blank main tab")
        mini = self.mini("/last-blank-mini")
        self.js(mini, "document.body.insertAdjacentHTML('beforeend','<input id=kept-form>'); "
                "document.querySelector('#kept-form').value='unfinished draft'; true")
        self.check(any(window["id"] == mini and window["key"] for window in self.preview()["miniWindows"]),
                   "Mini owns the keyboard before background main-tab close")
        self.organize("close-tab", id=blank)
        state = self.preview()
        self.check(any(tab["id"] == mini for tab in state["tabs"]) and
                   any(window["id"] == mini and window["visible"] for window in state["miniWindows"]),
                   "closing last blank main tab never closes the focused Mini")
        self.check(self.js(mini, "document.querySelector('#kept-form').value") == "unfinished draft",
                   "background main-tab close preserves Mini's edited form")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default=str(ROOT / ".build/debug/Search"))
    parser.add_argument("--only", choices=("last-blank", "private"), help="run one ownership regression")
    args = parser.parse_args()
    args.world = "previewext-" + uuid.uuid4().hex[:8]
    socket.setdefaulttimeout(30)
    server = ThreadingHTTPServer(("127.0.0.1", 0), SUITE["PageHandler"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = PreviewExtensions(args, f"http://127.0.0.1:{server.server_port}")
    code = 0
    try:
        run.execute()
        run.report["ok"] = True
    except (Exception, SystemExit) as error:
        run.report.update(ok=False, error=str(error))
        print("FAIL " + str(error), flush=True)
        code = 1
    finally:
        run.stop()
        server.shutdown()
        server.server_close()
        report = run.directory / "preview-extensions-report.json"
        report.write_text(json.dumps(run.report, indent=2))
        print("Preview extension report: " + str(report), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
