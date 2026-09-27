from ..storage import AssetError
from ..tool_protocol import execution_failure, parse_arguments
from shared.outcome import normalize_result
from ..workspace import WorkspaceRegistry
from .uav_audit.audit import UavInspectionService


from .forest_structure.tool import DEFINITIONS as FOREST_DEFINITIONS
from .prosail.tool import DEFINITIONS as PROSAIL_DEFINITIONS
from .uav_audit.tool import DEFINITIONS as UAV_DEFINITIONS
from .raster.tool import DEFINITIONS as RASTER_DEFINITIONS

DEFINITIONS = {
    **RASTER_DEFINITIONS,
    **FOREST_DEFINITIONS,
    **PROSAIL_DEFINITIONS,
    **UAV_DEFINITIONS,
}



from .inputs import InputResolution
from .forest_structure.service import ForestStructureCapability
from .prosail.service import ProsailCapability
from .raster.service import RasterCapability
from .uav_audit.service import UavAuditCapability

class RemoteSensingTools(
    InputResolution, UavAuditCapability, ProsailCapability,
    ForestStructureCapability, RasterCapability,
):
    def __init__(self, store, owner, asset_ids,
                 workspaces: WorkspaceRegistry | None = None, latest_user: str = ''):
        self.store = store
        self.owner = owner
        self.chat_id = store.chat_id if workspaces is not None else None
        self.allowed = set(asset_ids)
        for asset_id in self.allowed:
            store.get(asset_id, owner)
        self.created = []
        self._uav_audit = None
        self.workspaces = workspaces
        self.latest_user = latest_user

    @property
    def uav_audit(self):
        if self._uav_audit is None:
            self._uav_audit = UavInspectionService(temp_root=self.store.root)
        return self._uav_audit

    def asset(self, asset_id):
        if asset_id not in self.allowed:
            raise AssetError('Asset is not attached to this conversation')
        return (
            self.store.get(asset_id, self.owner),
            self.store.path(asset_id, self.owner),
        )

    def attachment_context(self) -> list[dict]:
        return [self.store.get(asset_id, self.owner) for asset_id in sorted(self.allowed)]

    def register(
        self, stream, name, media_type=None, parent=None, limit=None,
        metadata=None,
    ):
        asset = self.store.put(
            stream, name, self.owner, media_type, parent, limit, metadata
        )
        self.allowed.add(asset['id'])
        self.created.append(asset)
        return asset

    def execute(self, name, arguments, progress=None):
        if name not in DEFINITIONS:
            return {
                'ok': False,
                'error': 'Unknown tool; choose one of the supplied tools',
                'failure': {
                    'stage': 'dispatch', 'code': 'unknown_tool',
                    'operation_started': False,
                    'available_tools': list(DEFINITIONS),
                },
            }
        model = DEFINITIONS[name][0]
        source_id = None
        try:
            arguments, source_id = self._resolve_uav_source(name, arguments)
        except Exception as exc:
            return execution_failure(exc)
        args, changes, failure = parse_arguments(model, arguments)
        if failure:
            return failure
        # Every successful result names the input it actually consumed, recorded by
        # the resolver rather than reconstructed from the arguments: a relative path
        # and an absolute one can name the same file, and only the resolver knows
        # which root it settled on. This is what makes the next tool call a copy.
        self._input_references = {}
        try:
            with self.store.operation():
                values = args.model_dump()
                data = getattr(self, name)(**values)
            references = dict(self._input_references)
            if isinstance(data, dict) and references and 'exact_reference' not in data:
                primary = references.get('input') or next(iter(references.values()))
                data = {**data, 'exact_reference': primary}
                if len(references) > 1:
                    data['input_references'] = references
            result = {'ok': True, 'data': data}
            if isinstance(data, dict) and data.get('outcome') == 'empty':
                result['outcome'] = 'empty'
                result['control_verified'] = data.get('control_verified')
            if isinstance(data, dict) and data.get('failure'):
                result.update(outcome_ok=False, failure=data['failure'])
            elif isinstance(data, dict) and data.get('ready') is False:
                result.update(outcome_ok=False, failure={
                    'stage': 'preconditions', 'code': 'not_ready',
                    'problems': data.get('problems', []),
                })
            if changes:
                result['argument_normalization'] = changes
            if source_id and isinstance(result.get('data'), dict):
                result['data']['source_id'] = source_id
            return normalize_result(result)
        except Exception as exc:
            return execution_failure(exc)


__all__ = ["RemoteSensingTools", "DEFINITIONS"]
