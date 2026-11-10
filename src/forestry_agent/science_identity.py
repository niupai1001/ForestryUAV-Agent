"""Identity of a recipe and its effective inputs, independent of job/session IDs."""
import hashlib
import json
from pathlib import Path


def recipe_versions(operation):
    root = Path(__file__).parent
    return {
        'algorithm_version': hashlib.sha256((root/'imagery_worker.py').read_bytes()).hexdigest(),
        'preprocessing_version': (
            hashlib.sha256((root/'legacy_flight_metadata.py').read_bytes()).hexdigest()
            if operation == 'orthomosaic' else 'rasterio-scale-offset-mask-ndvi-v1'),
    }


def science_run_identity(operation, data, declaration, image):
    # Round-trip tuples/default-normalized values to the same JSON form as records.
    effective = {k: v for k, v in declaration.items() if k != 'source_note'}
    calibration = {k: v for k, v in effective.items()
                   if k in {'calibration_source', 'band_mapping_source', 'reflectance',
                            'radiometric_calibration', 'geometry_source', 'sensor_response_source'}}
    body = {'schema_version': 1, 'operation': operation,
            'method_id': 'imagery.odm.v1' if operation == 'orthomosaic' else 'imagery.prosail_legacy_nn.v1',
            'data': data,
            'effective_declaration': effective, 'image_digest': image,
            'calibration_provenance': calibration, **recipe_versions(operation)}
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return {**json.loads(encoded), 'sha256': hashlib.sha256(encoded.encode('utf-8')).hexdigest()}
