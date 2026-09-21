"""Local scorecard viewer: run the baseline and read the result from a browser.

This is deliberately a standalone, dependency-free process. It does not import
``runtime`` and the Runtime does not import it, so the evaluator keeps the
independence ``FRAMEWORK.md`` requires and neither side needs a rebuild to change.

Only the Python standard library is used, for three reasons: the evaluation
package must not depend on the Runtime image, the viewer must start even when the
Runtime is stopped, and a scoring UI should never be the reason an evaluation
cannot run.

What it does:

* renders the current ``scorecard.json`` — gates, capability groups, coverage,
  per-case checks with their verifier verdicts, and why each slot is unknown;
* can start a baseline run and poll its progress, so the loop from "change the
  system" to "see the score" needs no terminal;
* refuses to guess. A missing or unparseable scorecard is shown as such rather
  than rendered as a zero score.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "evaluation" / "work" / "baseline"
KNOWN_TRACKS = ("agent", "engineering", "ui")

# One run at a time: the baseline mutates trial directories and spends model calls.
_RUN_LOCK = threading.Lock()
_RUN_STATE: dict = {
    "status": "idle",
    "tracks": [],
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "stdout": "",
    "stderr": "",
    "error": None,
    "failure": None,
}


def read_json(path: Path) -> tuple[dict | None, str | None]:
    """Return (payload, error). A missing file is not an error, it is 'not run yet'."""
    if not path.is_file():
        return None, None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"cannot read {path.name}: {exc}"
    if raw.startswith(b"\xef\xbb\xbf"):
        return None, f"{path.name} starts with a UTF-8 BOM; re-save it as plain UTF-8"
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{path.name} is not valid JSON: {exc}"


def load_view(root: Path) -> dict:
    """Collect everything the page needs, without interpreting the numbers."""
    scorecard, scorecard_error = read_json(root / "scorecard.json")
    records, records_error = read_json(root / "records.json")
    if isinstance(records, dict):
        records = None
    digest = None
    if scorecard is not None:
        digest = {
            "suite_version": scorecard.get("suite_version"),
            "qualification": scorecard.get("qualification"),
            "gates": scorecard.get("gates"),
        }
    return {
        "root": str(root),
        "scorecard_path": str(root / "scorecard.json"),
        "records_path": str(root / "records.json"),
        "scorecard": scorecard,
        "scorecard_error": scorecard_error,
        "record_count": len(records) if isinstance(records, list) else None,
        "records_error": records_error,
        "summary": digest,
        "run": current_run(),
    }


def current_run() -> dict:
    with _RUN_LOCK:
        return {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in _RUN_STATE.items()
        }


def failure_reason(returncode: int | None, stderr: str, error: str | None) -> str | None:
    """Explain a failed run, distinguishing a crash from a scorecard verdict.

    Exit code 1 means ``blocked`` when the scorecard was produced, but the same
    code comes back when the runner raised. Reporting "gate blocked" for a crash
    sends the reader to the wrong place -- in practice a leftover trial directory
    aborted a run and the page still showed the previous run's gates.
    """
    if error:
        return error
    if returncode is None:
        return "the run did not report a return code"
    if returncode == 0:
        return None
    if "Traceback (most recent call last)" in (stderr or ""):
        last = [line for line in (stderr or "").strip().splitlines() if line.strip()]
        return (
            "the run crashed before producing a scorecard, so the result below is "
            "from an earlier run: " + (last[-1] if last else "no detail")
        )
    if returncode == 1:
        return None  # a real gate failure; the scorecard explains it
    if returncode == 2:
        return "evidence is incomplete: not every preregistered slot has evidence yet"
    return f"unexpected exit code {returncode}"


def _execute(tracks: list[str], cases: list[str] | None, repeats: list[int] | None,
             root: Path) -> None:
    command = [
        sys.executable, "-m", "evaluation.run_baseline", "--root", str(root),
    ]
    if tracks:
        command += ["--tracks", *tracks]
    if cases:
        command += ["--cases", *cases]
    if repeats:
        command += ["--repeats", *[str(value) for value in repeats]]
    try:
        completed = subprocess.run(
            command, cwd=PROJECT_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=6 * 60 * 60,
        )
        returncode, stdout, stderr, error = (
            completed.returncode, completed.stdout, completed.stderr, None,
        )
    except subprocess.TimeoutExpired:
        returncode, stdout, stderr = None, "", ""
        error = "the baseline run exceeded six hours and was abandoned"
    except OSError as exc:
        returncode, stdout, stderr = None, "", ""
        error = f"could not start the baseline: {exc}"
    with _RUN_LOCK:
        _RUN_STATE.update({
            "status": "finished", "finished_at": time.time(),
            "returncode": returncode, "stdout": stdout[-20000:],
            "stderr": stderr[-20000:], "error": error,
            "failure": failure_reason(returncode, stderr, error),
        })


def start_run(tracks: list[str], cases: list[str] | None, repeats: list[int] | None,
              root: Path) -> tuple[bool, str]:
    """Start one run. Returns (accepted, message) without waiting for it.

    The state check and the claim happen under one lock acquisition. Re-acquiring
    a non-reentrant ``Lock`` in the same thread blocks forever, which is what an
    earlier version of this function did.
    """
    unknown = [name for name in tracks if name not in KNOWN_TRACKS]
    if unknown:
        return False, f"unknown track(s): {', '.join(unknown)}"
    with _RUN_LOCK:
        if _RUN_STATE["status"] == "running":
            return False, "a baseline run is already in progress"
        _RUN_STATE.update({
            "status": "running", "tracks": list(tracks),
            "started_at": time.time(), "finished_at": None,
            "returncode": None, "stdout": "", "stderr": "", "error": None,
            "failure": None,
        })
    threading.Thread(
        target=_execute, args=(tracks, cases, repeats, root), daemon=True
    ).start()
    return True, "started"


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forestry Agent 评分</title>
<style>
:root{--bg:#0f1116;--panel:#171a21;--line:#262b36;--fg:#e6e9ef;--dim:#98a1b3;
--ok:#3fb950;--bad:#f85149;--unk:#d29922;--accent:#4493f8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-size:15px;margin:0;font-weight:600}
h2{font-size:13px;margin:0 0 10px;color:var(--dim);text-transform:uppercase;
letter-spacing:.08em;font-weight:600}
main{padding:20px;max-width:1400px;margin:0 auto;display:grid;gap:18px}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px}
.meta{color:var(--dim);font-size:12px}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;
padding:7px 13px;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg)}
button:disabled{opacity:.45;cursor:not-allowed}
select{background:var(--bg);color:var(--fg);border:1px solid var(--line);
border-radius:6px;padding:6px 9px;font:inherit}
.badge{display:inline-block;padding:3px 9px;border-radius:20px;font-weight:700;font-size:12px}
.pass{background:rgba(63,185,80,.15);color:var(--ok)}
.fail{background:rgba(248,81,73,.15);color:var(--bad)}
.unknown{background:rgba(210,153,34,.15);color:var(--unk)}
.incomplete{background:rgba(210,153,34,.15);color:var(--unk)}
.blocked{background:rgba(248,81,73,.15);color:var(--bad)}
.measured_unqualified{background:rgba(63,185,80,.15);color:var(--ok)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.stat{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:12px}
.stat .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.stat .v{font-size:21px;font-weight:700;margin-top:5px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
tr:last-child td{border-bottom:0}
.bar{height:7px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:6px;min-width:90px}
.bar i{display:block;height:100%;background:var(--accent)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.chip{padding:1px 7px;border-radius:4px;font-size:11px;background:var(--bg);border:1px solid var(--line)}
details{margin-top:5px}
summary{cursor:pointer;color:var(--dim);font-size:11px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;
padding:11px;overflow:auto;max-height:340px;font-size:11px;margin:8px 0 0}
.empty{color:var(--dim);font-style:italic}
.warn{border-left:3px solid var(--unk);padding-left:11px;color:var(--unk)}
.actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
label{color:var(--dim);font-size:12px}
</style></head><body>
<header>
  <h1>Forestry Agent 评分</h1>
  <span class="meta" id="root"></span>
  <span style="flex:1"></span>
  <button class="ghost" id="refresh">刷新</button>
</header>
<main>
  <section>
    <h2>运行基线</h2>
    <div class="actions">
      <label>轨道
        <select id="track">
          <option value="engineering">engineering（门禁，不需模型）</option>
          <option value="agent">agent（真实模型）</option>
          <option value="ui">ui（真实浏览器）</option>
          <option value="">全部三条</option>
        </select>
      </label>
      <button id="run">运行</button>
      <span class="meta" id="runmsg"></span>
    </div>
    <div id="runwarn"></div>
    <div id="runlog"></div>
  </section>

  <section>
    <h2>总体判定</h2>
    <div id="overall"></div>
  </section>

  <section>
    <h2>能力组</h2>
    <div id="groups"></div>
  </section>

  <section>
    <h2>逐题结果</h2>
    <div id="cases"></div>
  </section>

  <section>
    <h2>缺证据原因</h2>
    <div id="missing"></div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"]/g,
  ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
const num = value => (value === null || value === undefined) ? '—' : value;

let currentRoot = '';

function verdictClass(verdict) {
  if (verdict === 'pass') return 'pass';
  if (verdict === 'fail') return 'fail';
  return 'unknown';
}

function renderEmpty(target, message) {
  target.innerHTML = '<p class="empty">' + esc(message) + '</p>';
}

function renderOverall(data) {
  const sc = data.scorecard;
  if (!sc) {
    if (data.scorecard_error) {
      renderEmpty($('overall'), '评分文件无法读取：' + data.scorecard_error);
    } else {
      renderEmpty($('overall'),
        '还没有评分结果。点上面的「运行」跑一次，或选择 engineering 轨道（不需要模型）。');
    }
    return;
  }
  const q = sc.qualification || 'unknown';
  const bounds = sc.agent_missing_evidence_bounds;
  const range = Array.isArray(bounds)
    ? bounds.map(v => (v === null ? '—' : Number(v).toFixed(1))).join(' ~ ') : '—';
  $('overall').innerHTML = `
    <div class="grid">
      <div class="stat"><div class="k">qualification</div>
        <div class="v"><span class="badge ${esc(q)}">${esc(q)}</span></div></div>
      <div class="stat"><div class="k">gates</div>
        <div class="v"><span class="badge ${esc(sc.gates || 'unknown')}">${esc(sc.gates || 'unknown')}</span></div></div>
      <div class="stat"><div class="k">agent_macro_score</div>
        <div class="v">${num(sc.agent_macro_score === null ? null : Number(sc.agent_macro_score).toFixed(1))}</div></div>
      <div class="stat"><div class="k">worst_agent_group</div>
        <div class="v">${num(sc.worst_agent_group_score === null ? null : Number(sc.worst_agent_group_score).toFixed(1))}</div></div>
      <div class="stat"><div class="k">缺证据下界~上界</div><div class="v">${esc(range)}</div></div>
      <div class="stat"><div class="k">suite_version</div><div class="v" style="font-size:14px">${esc(sc.suite_version || '—')}</div></div>
    </div>
    <p class="meta" style="margin-top:12px">
      退出码语义：blocked=门禁失败需优先修 · incomplete=证据未齐（不是失败） ·
      measured_unqualified=测完且门禁通过，但本版没有校准过的发布线。
      agent_macro_score 为 null 表示三个能力组尚未全部有据可依。
    </p>`;
}

function renderGroups(data) {
  const sc = data.scorecard;
  if (!sc || !sc.groups) { renderEmpty($('groups'), '暂无分组结果。'); return; }
  const rows = Object.entries(sc.groups).sort(([a],[b]) => a.localeCompare(b));
  $('groups').innerHTML = '<table><thead><tr><th>组</th><th>分数</th>' +
    '<th>覆盖率</th><th>通过/计划</th><th>未知</th><th>每题全通过</th></tr></thead><tbody>' +
    rows.map(([name, g]) => {
      const coverage = g.evidence_coverage === null ? null : Number(g.evidence_coverage);
      const pct = coverage === null ? 0 : Math.round(coverage * 100);
      return '<tr><td>' + esc(name) + '</td>' +
        '<td>' + num(g.score === null ? null : Number(g.score).toFixed(1)) + '</td>' +
        '<td>' + pct + '%<div class="bar"><i style="width:' + pct + '%"></i></div></td>' +
        '<td>' + num(g.passed) + '/' + num(g.planned) + '</td>' +
        '<td>' + num(g.unknown) + '</td>' +
        '<td>' + num(g.all_repeats_success_rate) + '</td></tr>';
    }).join('') + '</tbody></table>';
}

function renderCases(data) {
  const sc = data.scorecard;
  if (!sc || !sc.cases) { renderEmpty($('cases'), '暂无逐题结果。'); return; }
  $('cases').innerHTML = '<table><thead><tr><th>题目</th><th>轨道</th><th>组</th>' +
    '<th>门禁</th><th>得分</th><th>覆盖</th><th>重复结果与检查项</th></tr></thead><tbody>' +
    sc.cases.map(c => {
      const trials = (c.trials || []).map(t =>
        '<span class="chip ' + verdictClass(t.verdict) + '">#' + t.repeat + ' ' +
        esc(t.verdict) + (t.reasons && t.reasons.length
          ? ' · ' + esc(t.reasons.join(', ')) : '') + '</span>').join('');
      return '<tr><td><strong>' + esc(c.id) + '</strong></td>' +
        '<td>' + esc(c.track) + '</td><td>' + esc(c.group) + '</td>' +
        '<td>' + (c.gate ? '<span class="badge fail">gate</span>' : '—') + '</td>' +
        '<td>' + num(c.score === null ? null : Number(c.score).toFixed(1)) + '</td>' +
        '<td>' + Math.round((c.evidence_coverage || 0) * 100) + '%</td>' +
        '<td><div class="chips">' + (trials || '—') + '</div></td></tr>';
    }).join('') + '</tbody></table>';
}

function renderMissing(data) {
  const sc = data.scorecard;
  const missing = sc && sc.missing_evidence;
  if (!Array.isArray(missing)) { renderEmpty($('missing'), '暂无记录。'); return; }
  if (!missing.length) {
    $('missing').innerHTML = '<p class="meta">所有预注册槽位都已有证据。</p>';
    return;
  }
  const byReason = {};
  for (const item of missing) {
    const key = (item.reasons || ['unknown']).join(', ');
    (byReason[key] = byReason[key] || []).push(item.case_id + '#' + item.repeat);
  }
  const entries = Object.entries(byReason).sort((a,b) => b[1].length - a[1].length);
  $('missing').innerHTML = '<p class="meta">共 ' + missing.length +
    ' 个槽位未获得证据。未测不代表失败，分母不缩减。</p><table><thead><tr>' +
    '<th>数量</th><th>原因</th><th>槽位</th></tr></thead><tbody>' +
    entries.map(([reason, slots]) => '<tr><td>' + slots.length + '</td><td>' +
      esc(reason) + '</td><td class="meta">' +
      esc(slots.slice(0, 24).join(' ')) + (slots.length > 24 ? ' …' : '') +
      '</td></tr>').join('') + '</tbody></table>';
}

function renderRun(run) {
  const running = run.status === 'running';
  $('run').disabled = running;
  $('run').textContent = running ? '运行中…' : '运行';
  if (running) {
    $('runmsg').textContent = '轨道：' + (run.tracks || []).join(', ') + ' · 已运行 ' +
      Math.round(Date.now()/1000 - run.started_at) + ' 秒';
  } else if (run.status === 'finished') {
    const code = run.returncode;
    const meaning = {0:'完整测量',1:'门禁阻断',2:'证据不齐'}[code] || ('退出码 ' + code);
    $('runmsg').textContent = '上次运行结束：' + meaning;
  } else {
    $('runmsg').textContent = '';
  }
  // A crash and a gate failure share exit code 1. Saying "门禁阻断" for a crash
  // sends the reader to the scorecard, which in that case is the previous run's.
  $('runwarn').innerHTML = (run.status === 'finished' && run.failure)
    ? '<p class="warn">上次运行未产生新的评分结果：' + esc(run.failure) +
      ' 下面的内容可能来自更早的一次运行。</p>'
    : '';
  const output = (run.stdout || '') + (run.stderr || '');
  $('runlog').innerHTML = output
    ? '<details' + (run.failure ? ' open' : '') +
      '><summary>运行输出</summary><pre>' + esc(output.slice(-6000)) + '</pre></details>'
    : '';
}

async function refresh() {
  const res = await fetch('/api/view');
  const data = await res.json();
  currentRoot = data.root;
  $('root').textContent = data.root + ' · 记录 ' +
    (data.record_count === null ? '—' : data.record_count) + ' 条';
  renderOverall(data); renderGroups(data); renderCases(data);
  renderMissing(data); renderRun(data.run);
  return data.run.status === 'running';
}

async function trigger() {
  const track = $('track').value;
  $('run').disabled = true;
  const body = { tracks: track ? [track] : [] };
  const res = await fetch('/api/run', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  const payload = await res.json();
  if (!payload.accepted) { $('runmsg').textContent = payload.message || '未启动'; }
  poll();
}

async function poll() {
  while (await refresh()) {
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

$('refresh').onclick = () => refresh();
$('run').onclick = () => trigger();
refresh().then(running => { if (running) poll(); });
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    root = DEFAULT_ROOT

    def log_message(self, *args) -> None:  # keep the console readable
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/api/view":
            self._json(200, load_view(self.root))
        elif route == "/api/health":
            self._json(200, {"status": "ok", "root": str(self.root)})
        else:
            self._json(404, {"error": "unknown endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route != "/api/run":
            self._json(404, {"error": "unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            self._json(413, {"accepted": False, "message": "request body too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json(400, {"accepted": False, "message": f"invalid JSON: {exc}"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"accepted": False, "message": "expected a JSON object"})
            return
        tracks = payload.get("tracks") or []
        cases = payload.get("cases") or None
        repeats = payload.get("repeats") or None
        if not isinstance(tracks, list) or not all(isinstance(t, str) for t in tracks):
            self._json(400, {"accepted": False, "message": "tracks must be a list of strings"})
            return
        accepted, message = start_run(tracks, cases, repeats, self.root)
        self._json(200 if accepted else 409, {"accepted": accepted, "message": message})


def parse_tracks(value: str | None) -> list[str]:
    if not value:
        return []
    tracks = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in tracks if item not in KNOWN_TRACKS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown track(s): {', '.join(unknown)}; choose from {', '.join(KNOWN_TRACKS)}"
        )
    return tracks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("SCORECARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SCORECARD_PORT", "8012")))
    parser.add_argument("--root", type=Path,
                        default=Path(os.getenv("SCORECARD_ROOT", str(DEFAULT_ROOT))))
    parser.add_argument("--run-tracks", default=None,
                        help="start a run immediately, e.g. engineering or agent,ui")
    args = parser.parse_args()

    Handler.root = args.root.resolve()
    Handler.root.mkdir(parents=True, exist_ok=True)
    if args.run_tracks is not None:
        tracks = parse_tracks(args.run_tracks)
        accepted, message = start_run(tracks, None, None, Handler.root)
        print(f"baseline run: {message}", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"scorecard viewer: http://{args.host}:{args.port}/", flush=True)
    print(f"reading: {Handler.root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
