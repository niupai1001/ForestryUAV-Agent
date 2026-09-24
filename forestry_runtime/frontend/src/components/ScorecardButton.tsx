/**
 * Opens the local scorecard viewer.
 *
 * The viewer is a separate process on purpose (evaluation/dashboard.py): it uses
 * only the Python standard library, never imports `runtime`, and starts even when
 * the Runtime container is stopped. Serving it from this app would put the
 * evaluator inside the thing it measures and would need the Runtime image to run.
 *
 * `setup.ps1` starts that process with the rest of the deployment, so by the time
 * anyone clicks this the viewer is normally already up. When it is not, the browser
 * opens a dead tab that says nothing useful, so the button probes first and then
 * reports which local command starts it.
 */
const SCORECARD_URL = import.meta.env.VITE_SCORECARD_URL ?? 'http://127.0.0.1:8012/'

export function ScorecardButton() {
  const open = async () => {
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 1500)
      const response = await fetch(`${SCORECARD_URL}api/health`, { signal: controller.signal })
      clearTimeout(timer)
      if (!response.ok) throw new Error(String(response.status))
      window.open(SCORECARD_URL, '_blank', 'noopener')
    } catch {
      window.alert(
        `评分界面未在运行（${SCORECARD_URL}）。\n\n` +
        '重新运行 start.cmd 会自动启动它；\n' +
        '或在项目目录执行：python -m evaluation.dashboard',
      )
    }
  }
  return <button className="scorecardLink" onClick={() => void open()} title="打开本地评分界面">
    ⊞ 评分界面 <small>独立进程 · 127.0.0.1:8012</small>
  </button>
}
