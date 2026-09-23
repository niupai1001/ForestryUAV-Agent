"""日常运行只需修改 INPUT_DIRECTORY。"""

from pathlib import Path


INPUT_DIRECTORY = Path(
    r"E:\0730-多光谱正射(晴天飞行)\1_原始数据\1630落叶松"
)

# 原生 ODM/GDAL/PROJ 统一放在纯英文工作区，避免 Windows 中文路径兼容问题。
WORKSPACE_ROOT = Path(r"E:\UAV_WORKSPACE")
