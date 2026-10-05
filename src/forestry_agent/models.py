from __future__ import annotations

import httpx

from .config import Config, ModelProfile
from .contracts import Failure


async def model_identity(config: Config, profile: ModelProfile):
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(config.ollama_base_url + "/api/tags")
            response.raise_for_status()
            match = next((m for m in response.json()["models"] if m["name"] == profile.served_name), None)
        if not match:
            raise Failure("MODEL_UNAVAILABLE", "指定本地模型权重未安装", 422)
        if not match.get("digest"):
            raise Failure("MODEL_UNVERIFIED", "模型服务没有返回权重摘要", 422)
        return {"served_name": profile.served_name, "repository": profile.repository,
                "digest": match["digest"], "size_bytes": match.get("size"),
                "precision": match.get("details", {}).get("quantization_level"),
                "backend": "ollama", "backend_url": config.ollama_base_url}
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise Failure("MODEL_UNAVAILABLE", "不能确认本地模型权重：" + str(error), 503, True) from error
