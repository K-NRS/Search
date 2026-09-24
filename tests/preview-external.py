#!/usr/bin/env python3
"""Deliver real warm/cold external URLs to an isolated disposable Search app.

Build with ./build.sh debug, then run python3 tests/preview-external.py. LaunchServices
is always given the unique app's absolute path; no default handler is changed.
A bundle launcher sets SEARCH_PROBE before the copied executable starts, so
isolation does not depend on LaunchServices honoring LSEnvironment.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import plistlib
import runpy
import signal
import socket
import subprocess
import threading
import time
import uuid
from http.server import ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
SUITE = runpy.run_path(str(ROOT / "tests/preview-harness.py"))
PROC = ctypes.CDLL("/usr/lib/libproc.dylib")
PROC.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
PROC.proc_pidpath.restype = ctypes.c_int


def executable(pid):
    buffer = ctypes.create_string_buffer(4096)
    if PROC.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        return None
    return Path(os.fsdecode(buffer.value)).resolve()


class External(SUITE["Run"]):
    def __init__(self, args, origin):
        super().__init__(args, origin)
        self.app = self.directory / ("Search External " + args.world + ".app")
        self.owned_pid = None
        self.report["launches"] = []

    def prepare(self):
        super().prepare()
        info_path = self.app / "Contents/Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info["CFBundleIdentifier"] = "com.officecommun.search.external-test." + uuid.uuid4().hex
        info["CFBundleDisplayName"] = self.app.stem
        info["CFBundleName"] = self.app.stem
        info["CFBundleExecutable"] = "SearchProbe"
        info["LSEnvironment"] = {"SEARCH_PROBE": self.args.world}
        info.pop("CFBundleURLTypes", None)
        info.pop("CFBundleDocumentTypes", None)
        info_path.write_bytes(plistlib.dumps(info))
        self.actual_executable = (self.app / "Contents/MacOS/Search").resolve()
        launcher = self.app / "Contents/MacOS/SearchProbe"
        # LS requires a native executable; the tiny launcher guarantees profile
        # isolation even when the LaunchServices environment is not inherited.
        source = ('#include <stdlib.h>\n#include <unistd.h>\n'
                  'int main(int argc, char **argv) {\n'
                  '  if (setenv("SEARCH_PROBE", ' + json.dumps(self.args.world) + ', 1)) return 125;\n'
                  '  argv[0] = ' + json.dumps(str(self.actual_executable)) + ';\n'
                  '  execv(argv[0], argv);\n  return 126;\n}\n')
        (self.directory / "launcher.c").write_text(source)
        subprocess.run(["/usr/bin/clang", "-x", "c", "-o", str(launcher), "-"],
                       input=source, text=True, check=True, capture_output=True)
        subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(self.app)],
                       check=True, capture_output=True)
        self.launcher_sha256 = hashlib.sha256(launcher.read_bytes()).hexdigest()
        self.report.update(app=str(self.app), bundleID=info["CFBundleIdentifier"],
                           launcher=str(launcher), executable=str(self.actual_executable))
        self.verify_isolation()

    def verify_isolation(self):
        info = plistlib.loads((self.app / "Contents/Info.plist").read_bytes())
        if (info.get("CFBundleIdentifier") != self.report["bundleID"]
                or not info["CFBundleIdentifier"].startswith("com.officecommun.search.external-test.")
                or info.get("CFBundleExecutable") != "SearchProbe"
                or info.get("LSEnvironment", {}).get("SEARCH_PROBE") != self.args.world
                or "CFBundleURLTypes" in info or "CFBundleDocumentTypes" in info
                or hashlib.sha256((self.app / "Contents/MacOS/SearchProbe").read_bytes()).hexdigest() != self.launcher_sha256
                or self.actual_executable.parent != (self.app / "Contents/MacOS").resolve()):
            raise RuntimeError("refusing launch: disposable bundle isolation is not proven")
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", str(self.app)],
                       check=True, capture_output=True)

    def pids(self):
        output = subprocess.check_output(["/bin/ps", "-axo", "pid="], text=True)
        return [int(value) for value in output.split()
                if executable(int(value)) == self.actual_executable]

    def deliver(self, *paths):
        self.verify_isolation()
        urls = [self.origin + path for path in paths]
        opened = subprocess.run(["/usr/bin/open", "-a", str(self.app), *urls],
                                capture_output=True, text=True, timeout=15)
        if opened.returncode:
            raise RuntimeError("explicit-app URL delivery failed: " + opened.stderr.strip())
        self.report["launches"].append({"urls": urls, "cold": self.owned_pid is None})

    def ready(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            pids = self.pids()
            if len(pids) > 1:
                raise RuntimeError("more than one process owns the disposable executable")
            if pids:
                self.owned_pid = pids[0]
                self.report["pid"] = self.owned_pid
                try:
                    state = self.preview()
                    self.check(state["miniLinks"] in (True, False),
                               "owned app responds through its unique probe socket")
                    return state
                except (OSError, ValueError, RuntimeError, SystemExit):
                    pass
            time.sleep(.1)
        raise RuntimeError("owned LaunchServices app never opened its unique probe socket")

    def preview(self, action="state", **fields):
        return self.ask("preview", action=action, **fields)

    def delivered(self, paths, surface):
        expected = {self.origin + path for path in paths}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self.preview()
            matches = [tab for tab in state["tabs"] if tab["url"] in expected]
            if {tab["url"] for tab in matches} == expected:
                self.check(len(matches) == len(expected), "external URLs each create exactly one page")
                self.check(all(tab["surface"] == surface for tab in matches),
                           "external delivery uses " + surface + " surfaces")
                for tab in matches:
                    path = tab["url"][len(self.origin):]
                    self.page(tab["id"], path)
                if surface == "mini":
                    windows = {window["id"]: window["number"] for window in state["miniWindows"]}
                    self.check(all(tab["id"] in windows for tab in matches)
                               and len({windows[tab["id"]] for tab in matches}) == len(matches),
                               "each external Mini owns a distinct native window")
                return matches
            time.sleep(.1)
        raise AssertionError("external URLs were lost: " + json.dumps({"expected": sorted(expected), "state": state}))

    def stop(self):
        # LaunchServices is not our subprocess. Confirm its kernel-reported
        # executable immediately before sending any signal; never use a name.
        for pid in self.pids():
            if executable(pid) != self.actual_executable:
                raise RuntimeError("refusing to terminate a PID whose executable changed")
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and executable(pid) == self.actual_executable:
                time.sleep(.1)
            if executable(pid) == self.actual_executable:
                raise RuntimeError("owned app did not terminate; refusing an unverified forced kill")
        self.owned_pid = None

    def execute(self):
        self.prepare()
        self.check(not self.pids(), "unique disposable bundle has no existing process")
        # Both URLs are delivered by the real cold-launch Apple Event path.
        self.deliver("/cold-first", "/cold-second")
        state = self.ready()
        self.check(state["miniLinks"] and state["peekLinks"], "fresh defaults enable Mini and Peek")
        cold = self.delivered(["/cold-first", "/cold-second"], "mini")
        first = cold[0]["id"]
        self.js(first, "window.externalDraft='keep this page'; true")
        initial_ids = {tab["id"] for tab in cold}

        self.deliver("/warm-first", "/warm-second")
        warm = self.delivered(["/warm-first", "/warm-second"], "mini")
        self.check(initial_ids.issubset({tab["id"] for tab in self.preview()["tabs"]})
                   and self.js(first, "window.externalDraft") == "keep this page",
                   "later external links preserve earlier Mini pages and live state")
        self.check(len({tab["id"] for tab in cold + warm}) == 4,
                   "cold and warm external URLs retain four independent pages")

        self.preview("settings", miniLinks=False)
        self.deliver("/ordinary-first", "/ordinary-second")
        self.delivered(["/ordinary-first", "/ordinary-second"], "tab")
        self.check(len(self.preview()["miniWindows"]) == 4,
                   "disabled Mini routing preserves existing Minis without creating another")

        self.preview("settings", miniLinks=True)
        self.ask("native", action="close-window")
        self.deliver("/main-closed")
        self.delivered(["/main-closed"], "mini")
        self.check(len(self.preview()["miniWindows"]) == 5,
                   "external link creates Mini after the main window closes")

        self.preview("settings", miniLinks=False)
        self.organize("save")
        self.stop()
        self.deliver("/cold-disabled-first", "/cold-disabled-second")
        state = self.ready()
        self.check(not state["miniLinks"], "disabled Mini routing persists into cold launch")
        self.delivered(["/cold-disabled-first", "/cold-disabled-second"], "tab")
        self.check(not self.preview()["miniWindows"],
                   "cold delivery with routing disabled creates no Minis and restores no temporary pages")
        self.report["finalState"] = self.preview()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default=str(ROOT / ".build/debug/Search"))
    args = parser.parse_args()
    args.world = "external-" + uuid.uuid4().hex[:8]
    socket.setdefaulttimeout(30)
    server = ThreadingHTTPServer(("127.0.0.1", 0), SUITE["PageHandler"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = External(args, f"http://127.0.0.1:{server.server_port}")
    code = 0
    try:
        run.execute()
        run.report["ok"] = True
    except BaseException as error:
        run.report.update(ok=False, error=str(error))
        print("FAIL " + str(error), flush=True)
        code = 1
    finally:
        try:
            if hasattr(run, "actual_executable"):
                run.stop()
        except BaseException as error:
            run.report.update(ok=False, cleanupError=str(error))
            print("FAIL cleanup: " + str(error), flush=True)
            code = 1
        server.shutdown()
        server.server_close()
        report = run.directory / "external-report.json"
        report.write_text(json.dumps(run.report, indent=2))
        print("External delivery report: " + str(report), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
