from __future__ import annotations

import asyncio
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .. import app as api

router = APIRouter()


def _probe(url: str, headers: dict[str, str] | None = None,
           timeout: float = 3.0) -> tuple[bool, str | None]:
    """Return (reachable, error). Bounded so a hung dependency cannot hang /health."""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers or {}), timeout=timeout
        ) as response:
            response.read(1)
        return True, None
    except urllib.error.HTTPError as exc:
        # The service answered; an auth or routing error is not unavailability.
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # URLError, timeouts, DNS, refused connections
        return False, f"{type(exc).__name__}: {exc}"


def _dependencies() -> dict:
    """Probe what the Runtime actually needs: the model and (optionally) the bridge."""
    ollama_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
    model_ok, model_error = _probe(ollama_url + "/api/tags")

    bridge_url = os.getenv("HOST_BRIDGE_URL", "").rstrip("/")
    bridge_key = os.getenv("HOST_BRIDGE_KEY", "")
    if not bridge_url or len(bridge_key) < 24:
        bridge_ok, bridge_error = False, "HOST_BRIDGE_URL/HOST_BRIDGE_KEY not configured"
    else:
        bridge_ok, bridge_error = _probe(
            bridge_url + "/health", {"Authorization": "Bearer " + bridge_key}
        )
    return {
        "model_reachable": model_ok,
        "model_error": model_error,
        "host_bridge_reachable": bridge_ok,
        "host_bridge_error": bridge_error,
    }


@router.get('/health', response_model=dict)
def health():
    """Liveness plus an honest dependency report.

    Always 200: the container healthcheck polls this, and a dependency being down
    must not mark the Runtime itself as unhealthy. ``status`` is ``degraded`` when
    something the Runtime actually needs cannot be reached, so a reader never has
    to infer readiness from an unconditional ``ok``.
    """
    dependencies = _dependencies()
    degraded = not dependencies["model_reachable"] or not dependencies["host_bridge_reachable"]
    return {
        'status': 'degraded' if degraded else 'ok',
        'version': '0.10.0',
        'model': os.getenv('OLLAMA_MODEL', 'qwen3.5:4b'),
        'kernel': 'pydantic-ai',
        'remote_sensing_plugins': os.getenv('REMOTE_SENSING_PLUGINS_ENABLED', 'false').lower() == 'true',
        **dependencies,
    }


@router.get('/ready', response_model=dict)
def ready():
    """Readiness: 200 only when every dependency is reachable, else 503.

    Separate from ``/health`` so orchestration can gate traffic on this without
    the container healthcheck flipping the Runtime to unhealthy.
    """
    dependencies = _dependencies()
    ready_now = dependencies["model_reachable"]
    payload = {'status': 'ready' if ready_now else 'not_ready', **dependencies}
    return JSONResponse(payload, status_code=200 if ready_now else 503)

@router.get('/', include_in_schema=False)
def workbench_root():
    response = RedirectResponse('/ui/')
    response.set_cookie(
        api.UI_COOKIE, api._ui_session_key(), httponly=True,
        samesite='strict', secure=False,
    )
    return response

@router.get('/ui/', include_in_schema=False)
def ui_index():
    index = api.UI_ROOT / 'index.html'
    if index.is_file():
        response = FileResponse(index, media_type='text/html')
    else:
        response = HTMLResponse(
            '<h1>Forestry Agent UI is not built</h1>'
            '<p>Run <code>npm run build</code> in <code>frontend</code>, '
            'or use the Docker build.</p>',
            status_code=503,
        )
    response.set_cookie(
        api.UI_COOKIE, api._ui_session_key(), httponly=True,
        samesite='strict', secure=False,
    )
    return response
