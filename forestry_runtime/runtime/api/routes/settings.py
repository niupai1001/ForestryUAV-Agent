"""Read and change the Runtime's tunable values from the workbench.

Two routes over one registry (``runtime.settings``):

* ``GET /settings`` reports every tunable value, its **effective** value, where that
  value came from (override / environment / default) and whether a change applies to
  the next Run or needs a restart. Reporting the origin is the point: "why is the
  model-call limit 32" had no answer in the product before, only a grep.
* ``PUT /settings`` writes an override, or clears one when the value is empty so the
  deployment's own environment takes over again.

Validation lives in the registry, so a rejected value comes back with the reason the
panel shows, and an unknown key is refused rather than stored and silently ignored.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException

from .. import app as api
from ...settings import BY_KEY, SCOPE_PER_RUN, SettingError, panel, resolve

router = APIRouter()


@router.get('/settings', response_model=dict)
def read_settings(store=Depends(api.request_store)):
    """Every tunable value with its effective setting and origin."""
    return panel(store.setting_overrides())


@router.put('/settings', response_model=dict)
def write_settings(body: dict, owner: str = Depends(api.identity),
                   store=Depends(api.request_store)):
    """Apply submitted values and report what each change will do.

    A submitted value is validated before anything is written, so a rejected entry
    leaves the stored settings exactly as they were -- a half-applied form is worse
    than a refused one.
    """
    submitted = body.get("settings") if isinstance(body, dict) else None
    if not isinstance(submitted, dict) or not submitted:
        raise HTTPException(400, "send {'settings': {'KEY': value}}")

    validated: dict[str, str | None] = {}
    for key, raw in submitted.items():
        setting = BY_KEY.get(str(key))
        if setting is None:
            raise HTTPException(400, f"{key} is not a tunable setting")
        if raw is None or str(raw).strip() == "":
            # An empty value is how the panel restores the deployment's own setting.
            validated[setting.key] = None
            continue
        try:
            validated[setting.key] = setting.coerce(raw)
        except SettingError as error:
            raise HTTPException(400, str(error)) from None

    for key, value in validated.items():
        if value is None:
            store.clear_setting(key)
        else:
            store.set_setting(key, value, updated_by=owner)

    overrides = store.setting_overrides()
    applied = []
    for key, value in validated.items():
        resolution = resolve(key, overrides)
        applied.append({
            "key": key,
            "value": resolution.value,
            "source": resolution.source,
            "applies": ("next_run" if BY_KEY[key].scope == SCOPE_PER_RUN
                        else "restart_required"),
            "cleared": value is None,
        })
    return {"applied": applied, **panel(overrides)}
