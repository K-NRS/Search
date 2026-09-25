#!/usr/bin/env python3
"""Exercise extension shortcuts through real WebKit and native key events.

Start Search with SEARCH_PROBE=<world>, enable its bench, then run:
python3 tests/extension-shortcuts.py --world <world>
Only this test's temporary extensions and tabs are removed afterwards.
"""

import argparse
import json
from pathlib import Path
import runpy
import socket
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
BENCH = runpy.run_path(str(ROOT / "bench"))
BACKGROUND = r"""
let pending = Promise.resolve();
chrome.commands.onCommand.addListener(command => {
  pending = pending.then(async () => {
    const {events = []} = await chrome.storage.local.get('events');
    events.push(command);
    await chrome.storage.local.set({events});
  });
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message !== 'SHORTCUT_READY') return;
  pending.then(() => respond({ready: true}));
  return true;
});
"""
HOST = r"""
window.readShortcuts = async () => ({
  commands: await chrome.commands.getAll(),
  events: (await chrome.storage.local.get('events')).events || [],
});
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True, help="isolated SEARCH_PROBE world")
    parser.add_argument("--reserved-only", action="store_true", help="run the preexisting API regression alone")
    args = parser.parse_args()
    if not args.world or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.world):
        parser.error("world must contain lowercase ASCII letters, digits or hyphens")
    socket.setdefaulttimeout(30)
    socket_path = str(Path(BENCH["folder"](args.world)) / "bench.sock")
    extensions = []
    hosts = []
    private_tabs = []
    checks = []

    def ask(verb, **fields):
        response = BENCH["ask"](socket_path, {"do": verb, **fields})
        if response.get("error") and verb != "ext-shortcuts":
            raise RuntimeError(response["error"])
        return response

    def check(ok, message, detail=None):
        checks.append({"ok": bool(ok), "message": message})
        if not ok:
            raise AssertionError(message + (": " + json.dumps(detail) if detail is not None else ""))

    def until(read, predicate=bool, seconds=10):
        deadline = time.monotonic() + seconds
        value = None
        while time.monotonic() < deadline:
            value = read()
            if predicate(value):
                return value
            time.sleep(0.1)
        raise RuntimeError("timed out; last value: " + json.dumps(value))

    def evaluate(host, js):
        return ask("eval", id=host, js=js).get("value")

    def resolve(host, expression):
        evaluate(host, "window.answer = null; Promise.resolve(" + expression + ").then(value => window.answer = {value}, error => window.answer = {error: String(error)}); true")
        result = until(lambda: evaluate(host, "window.answer"))
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["value"]

    def state(host):
        return resolve(host, "window.readShortcuts()")

    def shortcut(extension, command=None, action=None):
        fields = {"id": extension}
        if command is not None:
            fields["command"] = command
        if action is not None:
            fields["action"] = action
        return ask("ext-shortcuts", **fields)

    def row(extension, command):
        rows = shortcut(extension)
        return next(item for item in rows["commands"] if item["id"] == command)

    def api_shortcut(host, command):
        return next(item["shortcut"] for item in state(host)["commands"] if item["name"] == command)

    def begin(extension, command):
        value = shortcut(extension, command, "record")
        check(value.get("recording") == command and ask("probe")["settings"],
              "recording starts inside Extensions settings", value)

    def assign(extension, command, code, chars, *mods):
        begin(extension, command)
        press(code, chars, *mods)
        value = shortcut(extension)
        check(not value.get("recording") and not value.get("error"),
              command + ": valid capture finishes without an error", value)
        expected_flags = sum({"cmd": 1 << 20, "shift": 1 << 17, "ctrl": 1 << 18, "opt": 1 << 19}[mod] for mod in mods)
        binding = row(extension, command)
        check(binding["key"].lower() == chars.lower() and binding["modifiers"] == expected_flags and binding["customized"],
              command + ": native command uses the captured key and modifiers", binding)
        ask("ui", settings=False)

    def expect_event(host, command, code, chars, *mods):
        before = state(host)["events"]
        press(code, chars, *mods)
        after = until(lambda: state(host)["events"], lambda events: len(events) > len(before))
        check(after == before + [command], "shortcut reaches the real worker exactly once", after)

    def expect_no_event(host, message, code, chars, *mods):
        before = state(host)["events"]
        press(code, chars, *mods)
        # Watch for delayed worker dispatch rather than treating one immediate
        # empty read as proof that a key did nothing.
        deadline = time.monotonic() + 0.7
        while time.monotonic() < deadline:
            after = state(host)["events"]
            if after != before:
                check(False, message, {"before": before, "after": after})
            time.sleep(0.1)
        check(True, message)

    def loaded(extension):
        return next((item for item in ask("extensions")["extensions"] if item["id"] == extension and item["loaded"]), None)

    def close_host(host):
        ask("close", id=host)
        hosts.remove(host)

    def open_host(extension):
        host = ask("ext-page", id=extension, path="host.html")["id"]
        hosts.append(host)
        until(lambda: evaluate(host, "typeof window.readShortcuts === 'function'"))
        check(resolve(host, "chrome.runtime.sendMessage('SHORTCUT_READY')").get("ready"),
              "real background worker is ready")
        return host

    def install(folder, name, commands, action=False):
        directory = Path(folder)
        directory.mkdir()
        manifest = {
            "manifest_version": 3, "name": name, "version": "1.0",
            "permissions": ["storage"],
            "background": {"service_worker": "background.js"},
            "commands": commands,
        }
        if action:
            manifest["action"] = {"default_popup": "popup.html"}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        (directory / "background.js").write_text(BACKGROUND)
        (directory / "host.js").write_text(HOST)
        (directory / "host.html").write_text('<!doctype html><title>Shortcut controller</title><script src="host.js"></script>')
        (directory / "popup.html").write_text('<!doctype html><title>Shortcut action opened</title><p>Real extension action popup</p>')
        ask("ext-folder", path=folder, yes=True)
        def find():
            return next((item for item in ask("extensions")["extensions"] if item.get("source") == folder), None)
        item = until(find, lambda item: item and item["loaded"], seconds=20)
        extensions.append(item["id"])
        return item["id"]

    def press(code, chars, *mods):
        return ask("press", code=code, chars=chars, mods=list(mods))

    try:
        with tempfile.TemporaryDirectory(prefix="search-extension-shortcuts-") as temporary:
            first = install(str(Path(temporary) / "first"), "Search shortcut regression A", {
                "address-conflict": {"description": "Reserved address key", "suggested_key": {"mac": "Command+L"}},
                "primary": {"description": "Primary command", "suggested_key": {"mac": "Command+Shift+Y"}},
                "secondary": {"description": "Secondary command"},
            }, action=True)
            host = open_host(first)
            ask("ui", welcome=False, settings=False)
            ask("select", id=host)
            press(53, "\u001b")
            before = state(host)["events"]
            press(37, "l", "cmd")
            probe = ask("probe")
            after = state(host)["events"]
            check(probe["field"] and after == before,
                  "Command+L opens the address field without dispatching the extension's conflicting command",
                  {"field": probe["field"], "before": before, "after": after})
            press(53, "\u001b")
            if args.reserved_only:
                return 0

            defaults = row(first, "primary")
            default_api = api_shortcut(host, "primary")
            check(defaults["key"].lower() == "y" and not defaults["customized"] and bool(default_api),
                  "manifest command is shown with its default shortcut", defaults)
            check(row(first, "secondary")["key"] == "" and not row(first, "secondary")["customized"],
                  "commands without a default remain assignable")
            expect_event(host, "primary", 16, "y", "cmd", "shift")

            assign(first, "primary", 14, "e", "ctrl", "shift")
            assigned_api = api_shortcut(host, "primary")
            check(bool(assigned_api) and assigned_api != default_api,
                  "chrome.commands.getAll reflects the edited shortcut", assigned_api)
            expect_event(host, "primary", 14, "e", "ctrl", "shift")
            expect_no_event(host, "old manifest shortcut no longer dispatches", 16, "y", "cmd", "shift")
            expect_no_event(host, "missing Shift does not dispatch a stronger binding", 14, "e", "ctrl")
            expect_no_event(host, "extra Option does not dispatch a weaker binding", 14, "e", "ctrl", "shift", "opt")

            assign(first, "secondary", 43, "ö", "ctrl", "shift")
            expect_event(host, "secondary", 43, "ö", "ctrl", "shift")
            shortcut(first, "secondary", "clear")

            second = install(str(Path(temporary) / "second"), "Search shortcut regression B", {
                "other": {"description": "Other extension command"},
            })
            second_host = open_host(second)
            ask("select", id=host)
            for owner, command, code, chars, mods, description in [
                (first, "secondary", 14, "e", ["ctrl", "shift"], "same-extension conflict"),
                (second, "other", 14, "e", ["ctrl", "shift"], "cross-extension conflict"),
                (first, "secondary", 37, "l", ["cmd"], "reserved browser shortcut"),
                (first, "secondary", 12, "q", ["cmd"], "reserved macOS Quit shortcut"),
                (first, "secondary", 18, "1", ["ctrl"], "reserved space shortcut"),
                (first, "secondary", 2, "d", [], "ordinary typing"),
                (first, "secondary", 27, "-", ["ctrl", "shift"], "unsupported punctuation"),
                (first, "secondary", 14, "🙂", ["ctrl", "shift"], "unsupported Unicode key"),
            ]:
                previous = row(owner, command)
                before_events = state(host)["events"]
                begin(owner, command)
                press(code, chars, *mods)
                value = shortcut(owner)
                check(bool(value.get("error")) and value.get("recording") == command,
                      description + " is rejected while capture remains active", value)
                check(row(owner, command) == previous, description + " preserves the old assignment")
                check(state(host)["events"] == before_events, "recording never dispatches the rejected key")
                press(53, "\u001b")
                value = shortcut(owner)
                check(not value.get("recording") and not value.get("error") and ask("probe")["settings"],
                      "Escape cancels recording without closing Settings", value)
                ask("ui", settings=False)

            previous = row(first, "secondary")
            begin(first, "secondary")
            press(48, "\t")
            value = shortcut(first)
            check(not value.get("recording") and not value.get("error") and ask("probe")["settings"],
                  "Tab cancels recording and leaves Settings open", value)
            check(row(first, "secondary") == previous, "Tab leaves the shortcut unchanged")
            ask("ui", settings=False)

            shortcut(first, "primary", "clear")
            check(row(first, "primary")["key"] == "" and row(first, "primary")["customized"],
                  "clear is an explicit empty override")
            check(api_shortcut(host, "primary") == "", "getAll reports the cleared shortcut")
            expect_no_event(host, "cleared shortcut no longer dispatches", 14, "e", "ctrl", "shift")

            assign(second, "other", 16, "y", "cmd", "shift")
            cleared = row(first, "primary")
            rejected = shortcut(first, "primary", "reset")
            check(bool(rejected.get("error")), "reset rejects a manifest default now occupied by another extension", rejected)
            check(row(first, "primary") == cleared and api_shortcut(host, "primary") == "",
                  "rejected reset preserves the explicit clear override")
            before_events = state(host)["events"]
            expect_event(second_host, "other", 16, "y", "cmd", "shift")
            check(state(host)["events"] == before_events, "rejected reset leaves delivery with the existing owner")
            shortcut(second, "other", "clear")
            shortcut(first, "primary", "reset")
            restored = row(first, "primary")
            check(restored["key"] == defaults["key"] and restored["modifiers"] == defaults["modifiers"] and not restored["customized"],
                  "reset restores the manifest default", restored)
            check(api_shortcut(host, "primary") == default_api, "getAll reports the restored default")
            expect_event(host, "primary", 16, "y", "cmd", "shift")

            collision_commands = {
                "collision": {"description": "Shared default", "suggested_key": {"mac": "Command+Shift+E"}},
            }
            third = install(str(Path(temporary) / "third"), "Search shortcut regression C", collision_commands)
            fourth = install(str(Path(temporary) / "fourth"), "Search shortcut regression D", collision_commands)
            third_host, fourth_host = open_host(third), open_host(fourth)
            ask("select", id=host)
            third_default, fourth_default = row(third, "collision"), row(fourth, "collision")
            check(third_default["key"].lower() == fourth_default["key"].lower() == "e"
                  and third_default["modifiers"] == fourth_default["modifiers"] == (1 << 20 | 1 << 17)
                  and not third_default["customized"] and not fourth_default["customized"],
                  "new extensions retain their conflicting manifest defaults for editing")
            before_collision = [state(third_host)["events"], state(fourth_host)["events"]]
            press(14, "e", "cmd", "shift")
            deadline = time.monotonic() + 0.7
            while time.monotonic() < deadline:
                after_collision = [state(third_host)["events"], state(fourth_host)["events"]]
                if after_collision != before_collision:
                    check(False, "a conflicting default dispatches neither extension", after_collision)
                time.sleep(0.1)
            check(True, "a conflicting default dispatches neither extension")
            shortcut(fourth, "collision", "clear")
            expect_event(third_host, "collision", 14, "e", "cmd", "shift")
            check(state(fourth_host)["events"] == before_collision[1],
                  "clearing one conflict leaves delivery only with the remaining owner")
            assign(fourth, "collision", 38, "j", "ctrl", "shift")
            before_third = state(third_host)["events"]
            expect_event(fourth_host, "collision", 38, "j", "ctrl", "shift")
            check(state(third_host)["events"] == before_third,
                  "editing the cleared command gives it an independent working shortcut")

            assign(first, "primary", 14, "e", "ctrl", "shift")
            shortcut(first, "address-conflict", "clear")
            customized = row(first, "primary")
            for action in ("disable and re-enable", "reload"):
                before_events = state(host)["events"]
                close_host(host)
                if action == "disable and re-enable":
                    ask("ext-enable", id=first, on=False)
                    check(not loaded(first), "disabled extension is unloaded")
                    # Its companion remains real and active while the first is off.
                    ask("select", id=second_host)
                    press(14, "e", "ctrl", "shift")
                    ask("ext-enable", id=first, on=True)
                else:
                    ask("ext-reload", id=first)
                until(lambda: loaded(first), seconds=20)
                host = open_host(first)
                ask("select", id=host)
                check(state(host)["events"] == before_events, action + " preserves stored command history without unintended dispatch")
                check(row(first, "primary") == customized, action + " preserves the edited binding")
                cleared = row(first, "address-conflict")
                check(cleared["key"] == "" and cleared["customized"], action + " preserves an explicit empty override")
                check(api_shortcut(host, "primary") == assigned_api and api_shortcut(host, "address-conflict") == "",
                      action + " restores native getAll values")
                expect_event(host, "primary", 14, "e", "ctrl", "shift")

            before_tabs = {item["id"] for item in ask("tabs")["tabs"]}
            press(45, "n", "cmd", "shift")
            private = [item for item in ask("tabs")["tabs"] if item["id"] not in before_tabs]
            private_tabs.extend(item["id"] for item in private)
            check(len(private) == 1 and private[0]["active"], "Command+Shift+N creates one active private tab")
            expect_no_event(host, "private tabs do not dispatch extension shortcuts", 14, "e", "ctrl", "shift")
            press(13, "w", "cmd")
            check(not any(item["id"] == private[0]["id"] for item in ask("tabs")["tabs"]), "test private tab closes")
            private_tabs.remove(private[0]["id"])
            ask("select", id=host)
            expect_event(host, "primary", 14, "e", "ctrl", "shift")

            assign(first, "_execute_action", 32, "u", "ctrl", "shift")
            before_events = state(host)["events"]
            press(32, "u", "ctrl", "shift")
            def popup_title():
                response = BENCH["ask"](socket_path, {"do": "ext-popup", "id": first, "js": "document.title"})
                return response.get("value")
            check(until(popup_title, lambda value: value == "Shortcut action opened") == "Shortcut action opened",
                  "an edited action shortcut opens its real extension popup")
            check(state(host)["events"] == before_events,
                  "the action shortcut does not emit a custom onCommand event")
            press(53, "\u001b")
            return 0
    finally:
        cleanup_errors = []
        for private in reversed(private_tabs):
            try:
                if any(item["id"] == private for item in ask("tabs")["tabs"]):
                    ask("select", id=private)
                    press(13, "w", "cmd")
            except Exception as error:
                cleanup_errors.append(str(error))
        for host in reversed(hosts):
            try:
                ask("close", id=host)
            except Exception as error:
                cleanup_errors.append(str(error))
        for extension in reversed(extensions):
            try:
                ask("ext-remove", id=extension)
            except Exception as error:
                cleanup_errors.append(str(error))
        print(json.dumps({"checks": checks, "cleanupErrors": cleanup_errors}, indent=2), flush=True)
        if cleanup_errors:
            raise RuntimeError("cleanup failed: " + "; ".join(cleanup_errors))


if __name__ == "__main__":
    raise SystemExit(main())
