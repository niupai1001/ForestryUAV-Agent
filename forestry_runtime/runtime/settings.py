"""One place that knows every tunable value, its effective setting and its origin.

These values used to be read with ``os.getenv`` at thirty call sites, so answering
"what is the model-call limit and why is it that number" meant grepping the tree and
then working out whether the value came from the container environment, the
deployment ``.env``, or a default literal buried in the code. The limit that ended a
real Run was one of them, and nothing in the product could report it.

The resolution order is fixed and reported:

1. an **override** written through the UI (persisted, so it survives a restart);
2. the **environment**, which is what the deployment declared;
3. the **default** in this registry.

Every entry carries its own metadata -- range, group, and the scope that says whether
a change applies to the next Run or needs the process restarted -- so the settings
panel is generated from this table instead of from a second hand-maintained list.

What is deliberately *not* here: credentials and deployment paths
(``RUNTIME_API_KEY``, ``HOST_BRIDGE_KEY``, ``HOST_BRIDGE_URL``, ``OLLAMA_URL``,
``RUNTIME_DATA_HOST_ROOT``, ``KNOWLEDGE_ROOTS``). A settings panel is a convenience
surface; putting a privileged path or a shared secret on it would widen the Runtime's
attack surface for no operational gain.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

#: A value read once when the process starts cannot be changed live by writing a
#: row, so the panel must say so rather than appear to have no effect.
SCOPE_PER_RUN = "per_run"
SCOPE_STARTUP = "startup"


class SettingError(ValueError):
    """A rejected value, with the reason the caller should show."""


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str                      # "int" | "float" | "bool" | "text"
    default: str
    group: str
    help: str
    scope: str = SCOPE_PER_RUN
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()

    def coerce(self, raw: Any) -> str:
        """Validate and normalise one submitted value, or raise :class:`SettingError`."""
        text = str(raw).strip()
        if self.kind == "int":
            try:
                number = int(text)
            except (TypeError, ValueError):
                raise SettingError(f"{self.key} must be a whole number") from None
            self._check_range(number)
            return str(number)
        if self.kind == "float":
            try:
                number = float(text)
            except (TypeError, ValueError):
                raise SettingError(f"{self.key} must be a number") from None
            self._check_range(number)
            return repr(number)
        if self.kind == "bool":
            lowered = text.casefold()
            if lowered in {"1", "true", "yes", "on"}:
                return "true"
            if lowered in {"0", "false", "no", "off"}:
                return "false"
            raise SettingError(f"{self.key} must be true or false")
        if self.choices and text not in self.choices:
            raise SettingError(
                f"{self.key} must be one of: {', '.join(self.choices)}"
            )
        return text

    def _check_range(self, number: float) -> None:
        if self.minimum is not None and number < self.minimum:
            raise SettingError(f"{self.key} must be at least {self.minimum:g}")
        if self.maximum is not None and number > self.maximum:
            raise SettingError(f"{self.key} must be at most {self.maximum:g}")

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "default": self.default,
            "group": self.group,
            "help": self.help,
            "scope": self.scope,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
        }


GROUP_MODEL = "模型与采样"
GROUP_LOOP = "Agent 循环"
GROUP_CONTEXT = "上下文预算"
GROUP_JOBS = "作业与等待"
GROUP_KNOWLEDGE = "知识与插件"

#: The registry. A value that is not here is not tunable from the panel, which is
#: the intended way to keep deployment configuration out of it.
SETTINGS: tuple[Setting, ...] = (
    # -- model and sampling -------------------------------------------------
    Setting("OLLAMA_MODEL", "text", "qwen3.5:4b", GROUP_MODEL,
            "生成所用的模型名。改了下一个 Run 生效。"),
    Setting("OLLAMA_TEMPERATURE", "float", "0.2", GROUP_MODEL,
            "采样温度。0 更确定，越高越发散。", minimum=0.0, maximum=2.0),
    Setting("OLLAMA_CONTEXT", "int", "32768", GROUP_MODEL,
            "模型上下文窗口（token）。", minimum=1024, maximum=1048576),
    Setting("OLLAMA_OUTPUT_TOKENS", "int", "8192", GROUP_MODEL,
            "单次回复的输出上限（token）。", minimum=256, maximum=131072),
    Setting("OLLAMA_THINK", "bool", "true", GROUP_MODEL,
            "是否让模型输出思考过程。"),

    # -- agent loop ---------------------------------------------------------
    Setting("AGENT_MAX_ROUNDS", "int", "32", GROUP_LOOP,
            "单个 Run 允许的模型调用次数上限。耗尽后本轮暂停并保存现场，可补充要求后继续。",
            minimum=1, maximum=10000),
    Setting("CONTINUATION_REVIEW_ENABLED", "bool", "true", GROUP_LOOP,
            "只在只读探索出现重复或空结果时，用一次无工具模型请求审查下一步；每轮最多两次。"),
    Setting("CONTEXT_KEEP_TOOL_PAIRS", "int", "4", GROUP_LOOP,
            "压缩历史时保留的最近工具调用对数。", minimum=0, maximum=100),
    Setting("CONTEXT_KEEP_MESSAGES", "int", "6", GROUP_LOOP,
            "压缩历史时保留的最近消息条数。", minimum=0, maximum=200),

    # -- context budget -----------------------------------------------------
    Setting("CONTEXT_UTILISATION", "float", "0.9", GROUP_CONTEXT,
            "输入预算占上下文窗口的比例。", minimum=0.1, maximum=1.0),
    Setting("CONTEXT_SAFETY_MARGIN_TOKENS", "int", "0", GROUP_CONTEXT,
            "在预算之外额外保留的余量（token）。", minimum=0, maximum=65536),

    # -- jobs and waiting ---------------------------------------------------
    Setting("INSTALL_WAIT_SECONDS", "text", "90,300,900", GROUP_JOBS,
            "等待依赖安装的递增时长（秒，逗号分隔）。"),
    Setting("INSTALL_ATTEMPT_LIMIT", "int", "2", GROUP_JOBS,
            "同一组依赖安装失败后允许的重试次数上限。", minimum=1, maximum=10),
    Setting("JOB_WAIT_POLL_SECONDS", "float", "1.5", GROUP_JOBS,
            "等待作业时的轮询间隔（秒）。", minimum=0.1, maximum=60.0),
    Setting("JOB_OBSERVATION_SECONDS", "int", "45", GROUP_JOBS,
            "一次作业观察的时长（秒）。", minimum=1, maximum=3600),
    Setting("RECOVERY_POLL_SECONDS", "int", "5", GROUP_JOBS,
            "恢复检查的轮询间隔（秒）。", minimum=1, maximum=600),
    Setting("CANCEL_TIMEOUT_SECONDS", "int", "300", GROUP_JOBS,
            "取消作业的等待上限（秒）。", minimum=1, maximum=3600),
    Setting("CODE_RUN_PREFLIGHT", "bool", "true", GROUP_JOBS,
            "执行代码前是否先校验依赖已安装并可用。"),
    Setting("ALLOW_INSTALL_CANCEL", "bool", "false", GROUP_JOBS,
            "是否允许取消依赖安装作业。"),

    # -- knowledge and plugins ---------------------------------------------
    Setting("KNOWLEDGE_SEARCHES_PER_TURN", "int", "3", GROUP_KNOWLEDGE,
            "每轮允许的知识检索次数。", minimum=0, maximum=50),
    Setting("PROJECT_INSTRUCTION_BUDGET_TOKENS", "int", "2000", GROUP_KNOWLEDGE,
            "项目指令的 token 预算。", minimum=0, maximum=200000),
    Setting("DOMAIN_GUIDES_ENABLED", "bool", "true", GROUP_KNOWLEDGE,
            "是否启用领域方法指南目录。"),
    Setting("REMOTE_SENSING_PLUGINS_ENABLED", "bool", "false", GROUP_KNOWLEDGE,
            "是否启用遥感领域工具。"),
)

BY_KEY: dict[str, Setting] = {setting.key: setting for setting in SETTINGS}

GROUPS: tuple[str, ...] = (
    GROUP_MODEL, GROUP_LOOP, GROUP_CONTEXT, GROUP_JOBS, GROUP_KNOWLEDGE,
)


@dataclass
class Resolution:
    """One setting's effective value, where it came from, and whether it is live."""

    setting: Setting
    value: str
    source: str                    # "override" | "env" | "default"

    def as_dict(self) -> dict:
        payload = self.setting.as_dict()
        payload.update({
            "value": self.value,
            "source": self.source,
            # A startup value cannot be changed by writing a row, so the panel must
            # say so instead of offering an edit that appears to do nothing.
            "applies": "next_run" if self.setting.scope == SCOPE_PER_RUN else "restart_required",
        })
        return payload


def _environment(key: str) -> str | None:
    value = os.environ.get(key)
    return None if value is None or str(value).strip() == "" else str(value)


def resolve(key: str, overrides: dict[str, str] | None = None) -> Resolution:
    """The effective value of one setting, with its origin."""
    setting = BY_KEY.get(key)
    if setting is None:
        raise SettingError(f"{key} is not a tunable setting")
    overrides = overrides or {}
    if key in overrides:
        try:
            return Resolution(setting, setting.coerce(overrides[key]), "override")
        except SettingError:
            # A stored override that no longer validates (the range tightened, say)
            # must not take the Runtime down; fall through to the next source and
            # let the panel show the effective value.
            pass
    from_env = _environment(key)
    if from_env is not None:
        try:
            return Resolution(setting, setting.coerce(from_env), "env")
        except SettingError:
            pass
    return Resolution(setting, setting.coerce(setting.default), "default")


def resolved(overrides: dict[str, str] | None = None) -> list[Resolution]:
    """Every setting, in registry order."""
    return [resolve(setting.key, overrides) for setting in SETTINGS]


def panel(overrides: dict[str, str] | None = None) -> dict:
    """The grouped payload the settings panel renders."""
    rows = resolved(overrides)
    grouped: dict[str, list[dict]] = {group: [] for group in GROUPS}
    for row in rows:
        grouped.setdefault(row.setting.group, []).append(row.as_dict())
    return {
        "groups": [
            {"name": name, "settings": grouped.get(name, [])}
            for name in GROUPS if grouped.get(name)
        ],
        "override_count": len(overrides or {}),
    }
