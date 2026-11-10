import pytest

from test_tool_discovery import test_discovery_activates_next_step_and_respects_permissions as discovery_contract
from test_tool_discovery import test_search_cannot_activate_a_hidden_tool_inside_the_same_batch as batch_contract
from test_tool_discovery import test_real_registered_catalog_recall_without_inputs as catalog_contract


@pytest.mark.asyncio
@pytest.mark.parametrize('restricted', [False, True])
async def test_native_discovery_uses_same_activation_restore_permission_contract(client, app, monkeypatch, restricted):
    await discovery_contract(client, app, monkeypatch, restricted, discovery_mode='native_bm25')


@pytest.mark.asyncio
async def test_native_discovery_keeps_same_batch_frozen(client, app, monkeypatch):
    await batch_contract(client, app, monkeypatch, discovery_mode='native_bm25')


@pytest.mark.asyncio
@pytest.mark.parametrize('query,expected', [
    ('正射影像重建', 'process_imagery'), ('多光谱LAI反演', 'process_imagery'),
    ('LAZ地形', 'process_lidar_terrain'), ('CSV缺失值', 'code_execute'), ('图像计数', 'view_image')])
async def test_native_discovery_uses_same_real_catalog_recall_contract(client, app, monkeypatch, query, expected):
    await catalog_contract(client, app, monkeypatch, query, expected, discovery_mode='native_bm25')
