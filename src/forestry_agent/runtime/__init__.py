"""运行核心组件。

对外只暴露接口与装配点：``interfaces`` 定义契约，``model_adapter`` 负责模型
传输，``tools``/``file_tools`` 负责工具定义与文件能力。业务模块可以导入本包，
但组件之间不互相导入具体实现。
"""

from .interfaces import ModelChannel, RequestTrace, RunScope, ToolSpec
from .model_adapter import LocalModelAdapter
from .tools import ToolRegistry, registrar

__all__ = ["LocalModelAdapter", "ModelChannel", "RequestTrace", "RunScope", "ToolRegistry", "ToolSpec", "registrar"]
