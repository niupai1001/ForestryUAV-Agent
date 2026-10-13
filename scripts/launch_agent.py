"""Owner-operated Windows launcher; no model calls, installations or task submission."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psutil

from forestry_agent.config import load_config
from forestry_agent.jobs import Docker
from forestry_agent.provenance import source_manifest
from forestry_agent.store import encode

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "workspace" / "launcher"
PAGE = DIRECTORY / "startup.html"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def save(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def open_browser(target):
    if os.name == "nt":
        os.startfile(target)
    else:
        import webbrowser
        webbrowser.open(target)


class Launcher:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.base_url = f"http://{self.config.host}:{self.config.port}"
        self.http = httpx.Client(timeout=4, trust_env=False)
        self.steps = []
        self.warnings = []
        self.environment_degraded = False
        self.started = []
        self.run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%fZ")
        self.report = {"run_id": self.run_id, "config": str(config_path),
                       "workspace": str(self.config.workspace_root), "url": self.base_url}

    def render(self, phase="starting", error=None):
        self.report.update(phase=phase, steps=self.steps, warnings=self.warnings,
                           started_processes=self.started, error=error)
        output = json.dumps(self.report, ensure_ascii=False, indent=2) + "\n"
        save(DIRECTORY / "latest.json", output)
        save(DIRECTORY / f"startup-{self.run_id}.json", output)
        title = {"starting": "正在准备运行环境", "ready": "Agent 已就绪",
                 "degraded": "工作台可打开，部分环境需要处理", "failed": "启动未完成"}[phase]
        refresh = '<meta http-equiv="refresh" content="2">' if phase == "starting" else ""
        if phase == "ready":
            refresh = f'<meta http-equiv="refresh" content="3;url={html.escape(self.base_url, quote=True)}">'
        rows = "".join(f"<li><b>{html.escape(name)}</b><span>{html.escape(detail)}</span></li>"
                       for name, detail in self.steps)
        notices = "".join(f"<p class=notice>{html.escape(item)}</p>" for item in self.warnings)
        if error:
            notices += f"<p class=error>{html.escape(error)}</p>"
        link = (f'<a class=button href="{self.base_url}">打开 Agent 工作台</a>'
                if phase in {"ready", "degraded"} else "")
        save(PAGE, f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>林遥 · 启动入口</title><style>
body{{margin:0;background:#f0f5f1;color:#183b2b;font:16px/1.7 "Microsoft YaHei",sans-serif}}
main{{max-width:760px;margin:7vh auto;background:white;padding:32px;border-radius:20px;box-shadow:0 8px 35px #163b2910}}
h1{{margin:8px 0 24px;font-size:28px}}.brand{{letter-spacing:3px;color:#56806b}}
ul{{padding:0;list-style:none}}li{{padding:12px 0;border-bottom:1px solid #e5ece7}}
li span{{display:block;color:#557063;white-space:pre-wrap}}.notice,.error{{padding:12px;background:#fff4d9;border-radius:8px}}
.error{{background:#ffe7e1;color:#942f1b}}a{{color:#176342}}.button{{display:inline-block;background:#176342;color:white;padding:10px 24px;border-radius:8px;text-decoration:none}}
details{{margin-top:24px;color:#557063}}code{{overflow-wrap:anywhere}}@media(max-width:800px){{main{{margin:16px;padding:24px}}}}
</style></head><body><main><div class=brand>林遥 · ForestryUAV-Agent</div>
<h1>{title}</h1><ul>{rows}</ul>{notices}{link}
<p>{'准备完成后将自动进入工作台。' if phase in {'starting', 'ready'} else '处理提示后，可再次双击启动入口。'}</p>
<details><summary>配置与启动记录</summary><p>配置：<code>{html.escape(str(self.config_path))}</code></p>
<p>工作区：<code>{html.escape(str(self.config.workspace_root))}</code></p>
<p><a href="{(DIRECTORY / 'latest.json').as_uri()}">查看本次启动记录</a> ·
<a href="{(ROOT / 'docs' / 'ONE-CLICK-START-zh.md').as_uri()}">使用说明</a></p>
<p>后台日志：<code>{html.escape(str(DIRECTORY))}</code></p></details></main></body></html>''')

    def step(self, name, detail):
        self.steps.append([name, detail])
        self.render()

    def spawn(self, command, name):
        with (DIRECTORY / f"{name}-{self.run_id}.log").open("ab") as log:
            info = subprocess.STARTUPINFO() if os.name == "nt" else None
            if info:
                info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, startupinfo=info,
                                       creationflags=HIDDEN, close_fds=True)
        self.started.append({"name": name, "pid": process.pid,
                             "create_time": psutil.Process(process.pid).create_time()})
        return process

    def ollama_tags(self):
        response = self.http.get(self.config.ollama_base_url.rstrip("/") + "/api/tags")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("models"), list):
            raise TypeError("Ollama 端口返回了无法识别的响应。")
        return {item["name"] for item in data["models"]}

    def prepare_ollama(self):
        self.step("本地模型服务", "检查 Ollama；首次启动可能需要稍等。")
        try:
            tags = self.ollama_tags()
            action = "复用已运行的 Ollama"
        except httpx.ConnectError:
            executable = shutil.which("ollama")
            fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
            if not executable and fallback.is_file():
                executable = str(fallback)
            if not executable:
                raise RuntimeError("未找到 Ollama。请安装后重试；入口不会自动下载模型。")
            process = self.spawn([executable, "serve"], "ollama")
            deadline = time.monotonic() + 45
            while True:
                try:
                    tags = self.ollama_tags()
                    break
                except httpx.ConnectError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Ollama 未能启动，请查看 ollama 启动日志。") from None
                    time.sleep(1)
            action = "已启动 Ollama"
        missing = [profile.served_name for profile in self.config.models.values()
                   if profile.served_name not in tags and profile.served_name + ":latest" not in tags]
        self.report["installed_models"] = sorted(tags)
        if missing:
            self.environment_degraded = True
            self.warnings.append("本地缺少模型：" + "、".join(missing) + "。请准备模型后再运行相应任务。")
        self.step("本地模型服务", action + "；只检查已安装模型，没有发起模型推理。")

    def docker_call(self, *arguments, timeout=25):
        clean = {name: os.environ[name] for name in ["PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"]
                 if name in os.environ}
        result = subprocess.run(Docker(self.config).command(*arguments), cwd=ROOT, env=clean,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                creationflags=HIDDEN, check=False)
        return result.returncode, result.stdout.decode("utf-8", errors="replace").strip()

    def prepare_docker(self):
        self.step("隔离运行环境", "检查配置指定的 Docker 后端。")
        try:
            code, version = self.docker_call("version", "--format", "{{.Server.Version}}")
        except (OSError, subprocess.TimeoutExpired):
            code, version = 1, ""
        action = "复用已运行的 Docker"
        if code:
            if self.config.docker_distribution:
                result = subprocess.run([shutil.which("wsl.exe") or "wsl.exe", "--distribution",
                    self.config.docker_distribution, "--user", "root", "--exec", "/usr/bin/env", "-i",
                    "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root", "systemctl", "start", "docker.service"],
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=90, creationflags=HIDDEN, check=False)
                if result.returncode:
                    raise RuntimeError(f"无法启动 WSL 环境 {self.config.docker_distribution} 的 Docker。"
                                       "请确认该发行版已经安装，参阅 docs/EXECUTOR-WSL.md。")
                action = "已唤醒 WSL 并启动 Docker 服务"
            else:
                executable = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker/Docker/Docker Desktop.exe"
                if not executable.is_file():
                    raise RuntimeError("未找到 Docker Desktop，请完成运行环境安装后重试。")
                self.spawn([str(executable)], "docker-desktop")
                action = "已启动 Docker Desktop"
            deadline = time.monotonic() + 90
            while True:
                try:
                    code, version = self.docker_call("version", "--format", "{{.Server.Version}}", timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    code = 1
                if code == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Docker 尚未就绪，请查看 WSL/Docker 的启动状态后重试。")
                time.sleep(2)
        images = [self.config.code_image]
        if self.config.science_image:
            images.append(self.config.science_image)
        for image in images:
            code, _ = self.docker_call("image", "inspect", image, "--format", "{{.Id}}")
            if code:
                self.environment_degraded = True
                self.warnings.append(f"缺少本地运行镜像 {image}。相关代码/科学作业暂不可运行；请按运行环境说明准备。")
        self.report["docker_version"] = version
        self.step("隔离运行环境", action + f"（{version}）。")

    def agent_runtime(self):
        try:
            response = self.http.get(self.base_url + "/health")
        except httpx.ConnectError:
            return None
        except httpx.HTTPError as error:
            raise RuntimeError("Agent 端口已响应但健康检查失败；入口不会覆盖或重启未知服务。") from error
        response.raise_for_status()
        if response.json().get("application") != "ready":
            raise RuntimeError("Agent 端口被其他服务占用，入口不会终止它。")
        key = self.config.workspace_root / ".forestry" / "local.key"
        if not key.is_file():
            raise RuntimeError("端口已有服务，但无法确认属于当前工作区。请核对配置或端口。")
        runtime = self.http.get(self.base_url + "/runtime", headers={
            "authorization": "Bearer " + key.read_text(encoding="utf-8").strip()})
        if runtime.status_code != 200:
            raise RuntimeError("端口已有服务，但当前工作区身份核验失败。请核对配置；不会自动重启。")
        data = runtime.json()
        config_value = self.config.model_dump(mode="json")
        expected = {hashlib.sha256(encode(config_value).encode()).hexdigest()}
        # The frozen stage-1 server predates this optional field. Accept only
        # omission of a null science_image, never a changed image/configuration.
        if config_value["science_image"] is None:
            config_value.pop("science_image")
            expected.add(hashlib.sha256(encode(config_value).encode()).hexdigest())
        if data.get("config_sha256") not in expected:
            raise RuntimeError("已运行的 Agent 与入口配置不同。请使用原配置或手动处理端口冲突；不会自动重启。")
        return data

    def prepare_agent(self, runtime):
        action = "复用已运行的 Agent，保留现有任务"
        if runtime is None:
            # A listener without /health must never be replaced by a new server.
            with socket.socket() as connection:
                connection.settimeout(1)
                if connection.connect_ex((self.config.host, self.config.port)) == 0:
                    raise RuntimeError("Agent 端口已被占用，请核对配置。")
            process = self.spawn([sys.executable, "-m", "forestry_agent.cli", "serve", "--config",
                                  str(self.config_path)], "agent")
            deadline = time.monotonic() + 60
            while True:
                runtime = self.agent_runtime()
                if runtime:
                    break
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Agent 未能在限时内启动。请查看 agent 启动日志；未重置任务或重新提交作业。")
                time.sleep(1)
            action = "已启动 Agent；历史暂停任务仍须在工作台显式恢复"
        self.report["agent_source_sha256"] = runtime["source"]["sha256"]
        self.report["agent_config_sha256"] = runtime["config_sha256"]
        if runtime["source"]["sha256"] != source_manifest()["sha256"]:
            self.warnings.append("已复用运行中的工作台。磁盘上的部分更新尚未加载；需要更新服务时，请先在工作台结清或暂停运行任务，再手动重启。")
        self.step("Agent 工作台", action + f"。地址：{self.base_url}")

    def run(self):
        try:
            self.step("配置", "沿用选定配置和工作区。")
            runtime = self.agent_runtime()  # Verify ownership before any environment startup.
            for name, prepare in [("本地模型", self.prepare_ollama), ("隔离环境", self.prepare_docker)]:
                try:
                    prepare()
                except (OSError, ValueError, TypeError, RuntimeError, httpx.HTTPError,
                        subprocess.TimeoutExpired) as error:
                    self.environment_degraded = True
                    self.warnings.append(f"{name}：{error}")
                    self.step(name, "暂未就绪；工作台仍可用于查看历史和处理配置。")
            self.prepare_agent(runtime)
            self.render("degraded" if self.environment_degraded else "ready")
            return 0
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, httpx.HTTPError,
                subprocess.TimeoutExpired) as error:
            self.render("failed", str(error))
            return 1
        finally:
            self.http.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-browser", action="store_true", help="仅用于命令行启动/入口验收")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This launcher supports Windows only.")
    import msvcrt
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    # Windows releases the lock even if the launcher crashes; no stale PID deletion.
    with (DIRECTORY / "startup.lock").open("a+b") as lock:
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if not args.no_browser and PAGE.is_file():
                open_browser(str(PAGE))
            return 0
        candidates = [ROOT / "config/app.local.yaml", ROOT / "workspace/resolution-ui-config.yaml",
                      ROOT / "config/app.wsl.example.yaml"]
        config_path = args.config.resolve() if args.config else next(path for path in candidates if path.is_file())
        try:
            launcher = Launcher(config_path)
        except (OSError, ValueError, TypeError) as error:
            message = f"无法读取启动配置 {config_path}：{error}"
            save(PAGE, '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                 '<title>林遥 · 配置错误</title><h1>启动配置需要处理</h1><p>'
                 + html.escape(message) + '</p></html>')
            save(DIRECTORY / "latest.json", json.dumps({"phase": "failed", "error": message}, ensure_ascii=False))
            if not args.no_browser:
                open_browser(str(PAGE))
            return 1
        launcher.render()
        if not args.no_browser:
            open_browser(str(PAGE))
        result = launcher.run()
        if launcher.environment_degraded and result == 0 and not args.no_browser:
            # Keep the issue page available and still fulfill one-click opening.
            open_browser(launcher.base_url)
        return result


if __name__ == "__main__":
    raise SystemExit(main())
