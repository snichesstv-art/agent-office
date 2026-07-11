# -*- coding: utf-8 -*-
"""AI 중앙 통제실 서버 — 대시보드 서빙 + 현황 API + 명령 큐/실행/결과회수.
실행:  python control_room.py                → http://127.0.0.1:8787 (안전: 명령은 큐 저장만)
       python control_room.py --allow-launch → 명령 즉시/수동 실행(headless claude) 활성
  GET  /                    대시보드          GET /graph        그래프 뷰
  GET  /api/status          세션 집계(20초 캐시)
  GET  /api/commands        명령 큐(+실행상태·결과 미리보기)
  GET  /api/result?id=CID   명령 실행 로그 전문
  POST /api/command         {topic, prompt, mode:'queue'|'launch'}
  POST /api/run             {id} — 큐에 있는 명령을 지금 실행 (--allow-launch 필요)
"""
import os, sys, json, time, subprocess, threading, datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import aggregate

DIR = os.path.dirname(os.path.abspath(__file__))
CMD_FILE = os.path.join(DIR, "commands.jsonl")
PORT = 8787
ALLOW_LAUNCH = ("--allow-launch" in sys.argv) or (os.environ.get("CR_ALLOW_LAUNCH") == "1")
_cache = {"t": 0, "data": None}
_procs = {}          # cid -> Popen (이 서버 프로세스 수명 동안)
_lock = threading.Lock()

def status(force=False):
    if force or time.time() - _cache["t"] > 20 or _cache["data"] is None:
        _cache["data"] = aggregate.build(write=True)
        _cache["t"] = time.time()
    return _cache["data"]

# ---------- 명령 저장소 ----------
def _read_all():
    out = []
    if os.path.exists(CMD_FILE):
        for line in open(CMD_FILE, encoding="utf-8"):
            try: out.append(json.loads(line))
            except Exception: pass
    return out

def _write_all(cmds):
    with open(CMD_FILE, "w", encoding="utf-8") as f:
        for c in cmds:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

def _update(cid, **kw):
    with _lock:
        cmds = _read_all()
        for c in cmds:
            if c.get("id") == cid: c.update(kw)
        _write_all(cmds)

def _log_path(cid): return os.path.join(DIR, f"agent_{cid}.log")

def _tail(path, n=600):
    try:
        txt = open(path, encoding="utf-8", errors="ignore").read().strip()
        return txt[-n:] if len(txt) > n else txt
    except Exception: return ""

# ---------- 실행 ----------
ALLOWED_MODELS = {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"}
def launch(cid, prompt, sid=None, model=None):
    """headless claude 에이전트 기동(+resume, +모델선택) + 종료 감시 스레드."""
    logf = open(_log_path(cid), "w", encoding="utf-8")
    resume = f'--resume {sid} ' if sid else ''
    mflag = f'--model {ALLOWED_MODELS[model]} ' if model in ALLOWED_MODELS else ''
    p = subprocess.Popen(f'claude -p {resume}{mflag}"{prompt.replace(chr(34), chr(39))}"',
                         cwd=os.path.expanduser('~'), stdout=logf, stderr=subprocess.STDOUT,
                         shell=True)
    _procs[cid] = p
    _update(cid, status="running", pid=p.pid, started=dt.datetime.now(dt.timezone.utc).isoformat())
    def watch():
        rc = p.wait()
        logf.close()
        _update(cid, status=("done" if rc == 0 else "error"), rc=rc,
                ended=dt.datetime.now(dt.timezone.utc).isoformat(),
                result=_tail(_log_path(cid)))
        _procs.pop(cid, None)
    threading.Thread(target=watch, daemon=True).start()

def add_command(topic, prompt, mode, sid=None, model=None):
    cid = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    rec = dict(id=cid, ts=dt.datetime.now(dt.timezone.utc).isoformat(),
               topic=topic or "(무제)", prompt=prompt, mode=mode, status="queued",
               sid=(sid or None), model=(model or None))
    with _lock:
        with open(CMD_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if mode == "launch":
        if not ALLOW_LAUNCH:
            _update(cid, status="queued", note="기동 비활성 — 서버를 --allow-launch로 실행하면 즉시 기동됩니다")
        else:
            launch(cid, prompt, sid, model)
    return rec

def run_queued(cid):
    if not ALLOW_LAUNCH:
        return dict(error="서버가 --allow-launch 모드가 아닙니다")
    cmds = _read_all()
    tgt = next((c for c in cmds if c.get("id") == cid), None)
    if not tgt: return dict(error="해당 명령 없음")
    if tgt.get("status") == "running": return dict(error="이미 실행 중")
    launch(cid, tgt["prompt"], tgt.get("sid"), tgt.get("model"))
    return dict(ok=True, id=cid)

def read_commands():
    cmds = _read_all()
    # 실행중 항목은 로그 미리보기 실시간 갱신
    for c in cmds:
        if c.get("status") == "running":
            c["result"] = _tail(_log_path(c["id"]), 300)
    return cmds[-60:][::-1]

# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a): pass
    def _file(self, name):
        with open(os.path.join(DIR, name), "rb") as f:
            self._send(200, f.read(), "text/html; charset=utf-8")
    def do_GET(self):
        p, _, q = self.path.partition("?")
        if p in ("/", "/index.html", "/dashboard.html"): self._file("dashboard.html")
        elif p in ("/graph", "/graph.html"): self._file("graph.html")
        elif p == "/api/status": self._send(200, json.dumps(status("f=1" in q), ensure_ascii=False))
        elif p == "/api/commands": self._send(200, json.dumps(read_commands(), ensure_ascii=False))
        elif p == "/api/result":
            cid = dict(kv.split("=") for kv in q.split("&") if "=" in kv).get("id", "")
            self._send(200, json.dumps(dict(id=cid, log=_tail(_log_path(cid), 20000)), ensure_ascii=False))
        else: self._send(404, json.dumps({"error": "not found"}))
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except Exception: body = {}
        p = self.path.split("?")[0]
        if p == "/api/command":
            prompt = (body.get("prompt") or "").strip()
            if not prompt: self._send(400, json.dumps({"error": "prompt required"})); return
            self._send(200, json.dumps(add_command(body.get("topic",""), prompt, body.get("mode","queue"), body.get("sid"), body.get("model")), ensure_ascii=False))
        elif p == "/api/run":
            self._send(200, json.dumps(run_queued(body.get("id","")), ensure_ascii=False))
        elif p == "/api/open":
            # 산출물 열기 — 사용자 홈 하위만 허용
            path = os.path.abspath(body.get("path", ""))
            base = os.path.expanduser('~')
            if path.startswith(base) and os.path.exists(path):
                try: os.startfile(path); self._send(200, json.dumps({"ok": True}))
                except Exception as e: self._send(500, json.dumps({"error": str(e)}))
            else:
                self._send(403, json.dumps({"error": "허용 범위(홈 폴더) 밖 경로"}))
        elif p == "/api/task":
            self._send(501, json.dumps({"error": "할일 연동은 config로 확장하세요 (README 참고)"}))
        else: self._send(404, json.dumps({"error": "not found"}))

if __name__ == "__main__":
    print(f"■ AI 중앙 통제실 가동 →  http://127.0.0.1:{PORT}")
    print(f"  헤드리스 기동: {'허용(--allow-launch)' if ALLOW_LAUNCH else '비활성(명령은 큐 저장, 실행하려면 --allow-launch)'}")
    status(force=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
