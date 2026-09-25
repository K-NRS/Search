#!/usr/bin/env python3
"""Check video layout and float/return in an isolated Search bench world.

Uses a real canvas MediaStream and HTMLVideoElement, with the centering
transform observed on Netflix. No playback state or browser APIs are mocked.
Run: python3 tests/float-video.py --world video-regression
"""

import argparse
import json
from pathlib import Path
import runpy
import socket
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
BENCH = runpy.run_path(str(ROOT / "bench"))
PAGE = b"""<!doctype html><title>Float video regression</title>
<style>
html,body { margin:0; height:100%; background:#182030 }
.player { position:absolute; inset:0; overflow:hidden }
video { position:absolute; top:50%; left:50%; width:100%; height:100%;
        transform:translate(-50%,-50%); object-fit:contain }
.player-timedtext { position:absolute; inset:9px 0; color:white;
                   font:18px sans-serif; text-shadow:0 1px 3px black }
.player-timedtext-text-container { position:absolute; bottom:8%; width:100%; text-align:center }
</style>
<div class="player"><video muted autoplay playsinline></video>
<div class="player-timedtext"><div class="player-timedtext-text-container"><span>Visible subtitles</span></div></div>
<button id="site-controls">Player controls</button></div>
<aside><div class="player-timedtext">Unrelated captions</div></aside>
<script>
const canvas = document.createElement('canvas');
canvas.width = 640; canvas.height = 360;
const ctx = canvas.getContext('2d');
let frame = 0;
setInterval(() => {
  ctx.fillStyle = '#23b0a0'; ctx.fillRect(0,0,640,360);
  ctx.fillStyle = '#fff'; ctx.font = '40px sans-serif';
  ctx.fillText('Real video frame ' + (++frame),30,180);
}, 40);
const video = document.querySelector('video');
video.srcObject = canvas.captureStream(25);
video.play();
</script>"""
STATE = """(() => {
  const v = document.querySelector('video'), r = v.getBoundingClientRect();
  return {x:r.x,y:r.y,w:r.width,h:r.height,viewport:[innerWidth,innerHeight],
    hidden:document.hidden,paused:v.paused,time:v.currentTime,
    ready:v.readyState,transform:getComputedStyle(v).transform,
    floating:document.documentElement.classList.contains('office-floating')};
})()"""
CAPTIONS = """(() => {
  const c = document.querySelector('.player > .player-timedtext');
  const s = c.querySelector('span'), v = document.querySelector('video');
  return {visibility:getComputedStyle(s).visibility,z:getComputedStyle(c).zIndex,
    videoZ:getComputedStyle(v).zIndex,font:getComputedStyle(s).fontSize,
    controls:getComputedStyle(document.querySelector('#site-controls')).visibility,
    unrelated:getComputedStyle(document.querySelector('aside .player-timedtext')).visibility};
})()"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True)
    args = parser.parse_args()
    if not args.world or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.world):
        parser.error("world must be a named isolated test world")
    socket.setdefaulttimeout(30)
    path = str(Path(BENCH["folder"](args.world)) / "bench.sock")
    failures = []

    def ask(verb, **fields):
        result = BENCH["ask"](path, {"do": verb, **fields})
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def evaluate(js):
        return ask("eval", id=tab, js=js).get("value")

    def poll(js):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = evaluate(js)
            if result:
                return result
            time.sleep(0.1)
        raise AssertionError("Timed out: " + js)

    def check(ok, message, state):
        print(json.dumps({"ok": bool(ok), "check": message, "state": state}))
        if not ok:
            failures.append(message)

    def script(name):
        source = (ROOT / "Sources/Search/Float.swift").read_text()
        return textwrap.dedent(source.split(f'static let {name} = """', 1)[1].split('"""', 1)[0])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tab = None
    try:
        tab = ask("open", url=f"http://127.0.0.1:{server.server_port}/")["id"]
        ask("wait", id=tab)
        ask("ui", welcome=False)
        ask("select", id=tab)
        poll("document.querySelector('video')?.readyState >= 2 && !document.querySelector('video').paused")
        before = evaluate(STATE)
        captions_before = evaluate(CAPTIONS)
        assert evaluate(script("on")) == "floating"
        state = evaluate(STATE)
        check(abs(state["x"]) < 1 and abs(state["y"]) < 1 and
              abs(state["w"] - state["viewport"][0]) < 1 and
              abs(state["h"] - state["viewport"][1]) < 1,
              "floating video fills viewport without inherited centering offset", state)
        captions = evaluate(CAPTIONS)
        check(captions["visibility"] == "visible" and captions["z"] != "auto" and
              int(captions["z"]) >= int(captions["videoZ"]),
              "subtitle sibling stays visible above the video", captions)
        check(captions["controls"] == "hidden" and captions["unrelated"] == "hidden",
              "revealing subtitles does not expose controls or other players", captions)
        evaluate("""const caption = document.querySelector('.player > .player-timedtext');
          caption.replaceWith(caption.cloneNode(true)); 'replaced'""")
        captions = evaluate(CAPTIONS)
        check(captions["visibility"] == "visible",
              "replacement subtitle layer remains visible", captions)
        assert evaluate(script("off")) == "landed"
        state = evaluate(STATE)
        check(state["transform"] == before["transform"] and not state["floating"],
              "return restores original player layout", state)
        captions = evaluate(CAPTIONS)
        check(captions == captions_before, "return restores original subtitle styles", captions)

        for cycle in range(2):
            evaluate("document.querySelector('video').play(); 'resumed'")
            poll("!document.querySelector('video').paused")
            ask("press", code=35, chars="p", mods=["cmd", "shift"])
            poll("document.documentElement.classList.contains('office-floating')")
            state = evaluate(STATE)
            check(state["viewport"] == [440, 247] and not state["hidden"],
                  f"cycle {cycle}: video is attached to floating window", state)
            check(abs(state["x"]) < 1 and abs(state["y"]) < 1,
                  f"cycle {cycle}: compiled app keeps video inside floating window", state)
            captions = evaluate(CAPTIONS)
            check(captions["visibility"] == "visible" and captions["z"] != "auto" and
                  int(captions["z"]) >= int(captions["videoZ"]),
                  f"cycle {cycle}: compiled app shows subtitles above video", captions)
            time.sleep(0.2)
            playing = evaluate(STATE)
            check(not playing["paused"] and playing["time"] > state["time"],
                  f"cycle {cycle}: video continues playing in floating window", playing)
            if cycle == 1:
                ask("shot", id=tab, path="/tmp/search-float-regression.png")
            ask("press", code=35, chars="p", mods=["cmd", "shift"])
            poll("!document.documentElement.classList.contains('office-floating')")
            time.sleep(0.5)
            state = evaluate(STATE)
            check(state["hidden"] == before["hidden"] and state["viewport"] == before["viewport"],
                  f"cycle {cycle}: page reattaches at its original size", state)
            if cycle == 1:
                ask("shot", id=tab, path="/tmp/search-float-return-regression.png")
    finally:
        if tab:
            ask("close", id=tab)
        server.shutdown()
        server.server_close()
    if failures:
        raise SystemExit("FAILED: " + "; ".join(failures))


if __name__ == "__main__":
    main()
