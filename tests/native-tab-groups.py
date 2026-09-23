#!/usr/bin/env python3
"""Native group controls and scroll regression in an owned disposable app.

Build first, then run `python3 tests/native-tab-groups.py --binary PATH`.
Uses the launcher and localhost pages from tab-groups.py; never a personal
profile, external Accessibility permission, or cross-application screenshot.
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
SUITE = runpy.run_path(str(ROOT / "tests/tab-groups.py"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default=str(ROOT / ".build/debug/Search"))
    parser.add_argument("--world", default="native-groups-" + uuid.uuid4().hex[:7])
    args = parser.parse_args()
    if not args.world or args.world in ("1", "test") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.world):
        parser.error("world must be a unique lowercase ASCII name")
    socket.setdefaulttimeout(30)
    server = ThreadingHTTPServer(("127.0.0.1", 0), SUITE["PageHandler"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = SUITE["Run"](args, f"http://127.0.0.1:{server.server_port}")
    report = run.directory / "native-report.json"
    run.report["screenshots"] = []

    def native(action="state", **fields):
        return run.ask("organize-ui", action=action, **fields)

    def until(read, accepted, timeout=6):
        deadline = time.monotonic() + timeout
        value = None
        while time.monotonic() < deadline:
            try:
                value = read()
                if accepted(value):
                    return value
            except RuntimeError as error:
                if "No window" not in str(error):
                    raise
            time.sleep(0.1)
        raise AssertionError(f"native state never arrived: {value}")

    def control(state, *, identifier=None, title=None):
        return next(c for c in state["controls"]
                    if (identifier is not None and c["identifier"] == identifier)
                    or (title is not None and c["title"] == title))

    def shot(name):
        time.sleep(0.4)
        result = native("shot", path=str(run.directory / (name + ".png")))
        run.report["screenshots"].append(result)

    def group(state, identity):
        return next(g for g in state["groups"] if g["id"] == identity)

    try:
        run.prepare()
        run.launch()
        state = until(native, lambda s: bool(s.get("controls") or s.get("nodes")))
        run.check(not state["missingSymbols"], "every offered icon resolves to a native SF Symbol")
        run.ask("ui", sidebar=False, look="light", hides=False, folded=False)
        run.ask("resize", width=1180, height=780, steps=1)

        native("new-group")
        sheet = until(native, lambda s: s["sheet"] and any(c["identifier"] == "organization-name" and c["focused"] for c in s["controls"]))
        run.check(control(sheet, identifier="organization-name")["focused"], "native group sheet focuses its name field")
        run.check(not control(sheet, title="Create")["enabled"], "blank group name disables Create")
        native("field", identifier="organization-name", value="Native group")
        native("choose", identifier="organization-marker-type", title="Emoji")
        native("field", identifier="organization-emoji", value="A")
        run.check(not control(native(), title="Create")["enabled"], "ordinary letter cannot be saved as an emoji")
        native("field", identifier="organization-emoji", value="🇹🇷")
        run.check(control(native(), title="Create")["enabled"], "flag grapheme enables Create")
        run.report["groupSheet"] = native()
        native("button", title="Create")
        until(native, lambda s: not s["sheet"])
        state = run.organize()
        made = next(g for g in state["groups"] if g["name"] == "Native group")
        run.check(made["mark"] == {"kind": "emoji", "value": "🇹🇷"}, "actual sheet Create stores its flag marker")
        member = run.open("/native-member")
        run.organize("assign-group", id=member, group=made["id"])

        for sidebar in (False, True):
            run.ask("ui", sidebar=sidebar)
            time.sleep(0.4)
            native("click-group", group=made["id"])
            state = until(run.organize, lambda s: group(s, made["id"])["collapsed"])
            run.check(member not in state["presented"], f"actual {'sidebar' if sidebar else 'strip'} heading click collapses its tabs")
            shot("collapsed-" + ("sidebar" if sidebar else "strip"))
            native("click-group", group=made["id"])
            state = until(run.organize, lambda s: not group(s, made["id"])["collapsed"])
            run.check(member in state["presented"], f"actual {'sidebar' if sidebar else 'strip'} heading click expands its tabs")

        native("rename-group", group=made["id"])
        sheet = until(native, lambda s: s["sheet"])
        run.check(control(sheet, identifier="organization-emoji")["value"] == "🇹🇷", "rename sheet preserves the existing emoji")
        native("choose", identifier="organization-marker-type", title="Icon")
        native("choose", identifier="organization-icon", title="Reading")
        native("button", title="Save")
        until(native, lambda s: not s["sheet"])
        run.check(group(run.organize(), made["id"])["mark"] == {"kind": "symbol", "value": "book"}, "actual native Save replaces emoji with selected icon")

        # Pin the sole grouped tab already first in the canonical list: no
        # array reorder can accidentally hide a missing publication of the change.
        state = run.organize("create-space", name="Pin count")
        state = run.organize("replace", id=state["activeID"], url=run.origin + "/pin-first")
        first = state["activeID"]
        run.page(first, "/pin-first")
        state = run.organize("create-group", name="One", id=first)
        run.check(state["tabs"][0]["id"] == first, "pin regression starts with grouped tab at canonical index zero")
        shot("one-before-pin")
        state = run.organize("pin", id=first)
        run.check(state["tabs"][0]["pin"] is not None and state["tabs"][0]["groupID"] is None,
                  "pinning first grouped tab preserves its index and removes membership")
        run.report["afterPinNative"] = native()
        run.check(any(n["label"] == "One, 0 tabs" for n in run.report["afterPinNative"]["nodes"]),
                  "rendered native group count immediately updates after pinning without reorder")
        shot("one-after-pin")

        run.organize("create-space", name="Long list", mark={"kind": "emoji", "value": "🧭"})
        for number in range(1, 7):
            state = run.organize("create-group", name=f"Group {number}")
            identity = state["groups"][-1]["id"]
            for item in range(1, 4):
                selected = run.open(f"/group-{number}-tab-{item}")
                run.organize("assign-group", id=selected, group=identity)
        run.ask("ui", sidebar=False)
        time.sleep(0.3)
        run.organize("select-tab", id=selected)
        run.ask("ui", sidebar=True)

        def selected_visible(state):
            for scroll in state["scrolls"]:
                clip, document = scroll["clip"], scroll["document"]
                if scroll["frame"][2] < 450 and document[3] > clip[3]:
                    gap = document[1] + document[3] - clip[1] - clip[3] if scroll["flipped"] else clip[1] - document[1]
                    if gap <= 32:
                        return True
            return False

        state = until(native, selected_visible)
        run.report["scrollEvidence"] = state["scrolls"]
        run.check(selected_visible(state), "layout switch scrolls selected last grouped tab into its actual native viewport")
        shot("selected-last-and-space-emoji")
        run.ask("ui", sidebar=False, look="dark")
        run.ask("resize", width=640, height=780, steps=1)
        shot("groups-strip-640-dark")
        run.report["narrowStripNative"] = native()
        horizontal = [s for s in run.report["narrowStripNative"]["scrolls"]
                      if s["frame"][3] <= 60 and s["frame"][2] < 640 and s["document"][2] > s["clip"][2]]
        run.check(any(s["document"][0] + s["document"][2] - s["clip"][0] - s["clip"][2] <= 36
                      for s in horizontal),
                  "narrowing an already overflowing strip keeps its selected last tab visible")
        run.report["finalState"] = run.organize()
        run.report["ok"] = True
    except BaseException as error:
        run.report.update(ok=False, error=str(error))
        raise
    finally:
        run.stop()
        server.shutdown()
        server.server_close()
        report.write_text(json.dumps(run.report, indent=2))
        print(f"Native report: {report}", flush=True)


if __name__ == "__main__":
    main()
