from pydantic_ai import Tool

from forestry_agent.runtime.interfaces import ToolSpec
from forestry_agent.runtime.tools import ToolRegistry, application_tools


def test_science_and_notes_metadata_come_from_actual_registration(app):
    registry = application_tools(app.state.engine, lambda *args: None)
    definitions = [Tool(spec.func).tool_def for spec in registry.specs()]
    descriptors = {value.name: value for value in registry.descriptors(definitions)}
    imagery = descriptors['process_imagery']
    assert imagery.capability == 'code.execute' and imagery.domain == 'job_tools'
    assert imagery.modalities == ('raster', 'multispectral', 'json')
    assert 'PROSAIL LUT for inversion' in imagery.accepts
    assert 'verified reuse candidates' in imagery.produces
    assert imagery.permission_tier == 'task_capability_and_input_grants'
    assert 'orthomosaics' in imagery.summary_en
    notes = descriptors['read_user_memory']
    assert notes.capability == 'memory.read' and notes.modalities == ('text',)
    assert 'method notes' in notes.summary_en
    definition = next(tool for tool in definitions if tool.name == 'process_imagery')
    assert '先验上界不是森林普适生理阈值' in definition.description
    assert '本次LUT声明' in definition.description
    assert '先调用本工具' not in definition.description


def test_undeclared_metadata_stays_unknown_and_native_definitions_are_not_fabricated():
    def plain(value: str) -> dict:
        """Read a simple value."""
        return {'value': value}
    registry = ToolRegistry()
    registry.register(ToolSpec('plain', 'fs.read', plain, 'file_tools'))
    native = Tool(plain).tool_def
    registered = registry.descriptors([native])[0]
    assert registered.accepts == ('value',) and registered.produces == registered.modalities == ()
    registry.unregister('plain')
    projected = registry.descriptors([native])[0]
    assert projected.capability == projected.domain == 'native'
    assert projected.produces == projected.modalities == ()
