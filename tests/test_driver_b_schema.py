"""Synchronous composition evidence; does not claim async driver behavior."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from forestry_agent.runtime.tools import application_tools


def test_driver_b_reuses_real_registered_catalog_and_parameter_validation(app):
    path = Path(__file__).resolve().parents[1]/'research/driver_b.py'
    spec = importlib.util.spec_from_file_location('driver_b_schema',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = app.state.engine
    snapshot = {'model':engine.config.models['small'].model_dump(),'harness_mode':'H1'}
    driver = module.DriverB(engine,'small','schema-only',snapshot,SimpleNamespace(model=None))
    expected = application_tools(engine,lambda *args:None)
    assert set(driver.registry.names()) == set(expected.names()) | {'search_tools'}
    assert set(driver.toolset.tools) == set(driver.registry.names())
    schemas = {name:tool.tool_def.parameters_json_schema for name,tool in driver.toolset.tools.items()}
    assert schemas['search_tools']['required'] == ['queries']
    assert schemas['read_file']['required'] == ['resource_id']
    assert 'reuse_candidate_id' in schemas['process_imagery']['properties']
    assert driver.registry.modes()['write_file'] == expected.modes()['write_file']
    # Validation happens before invocation; no resource, model or job is accessed.
    from pydantic_core import ValidationError
    try:
        driver.toolset.tools['read_file'].function_schema.validator.validate_python({})
    except ValidationError:
        pass
    else:
        raise AssertionError('Driver B lost registered parameter validation')
