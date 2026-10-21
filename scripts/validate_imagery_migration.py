"""One scoped migration check. Durable IDs reuse successes and retain failures.

Build the new flight's LUT once; validate inversion with exact LUT spectra and
invalid pixels. This is a numerical contract, NOT a real orthomosaic/LAI accuracy test.
"""
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.imagery_schema import LutDeclaration
from forestry_agent.jobs import TERMINAL

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workspace/imagery-migration-20261021"
IMAGE = "sha256:d54664352b9d40d8d4d21cc38349dbd4623f1202177b41ff7dadc9756ca33969"


def main():
    config = load_config(ROOT / "config/app.local.yaml")
    config.workspace_root = OUT / "runtime"
    config.imagery_image = IMAGE
    config.port = 8774
    metadata = json.loads((OUT / "metadata/flight_parameters.json").read_text(encoding="utf-8"))
    declaration = LutDeclaration(
        parameter_source="Legacy run_prosail.py RANGES; exploratory priors, not field-calibrated",
        geometry_source="N flight_metadata NOAA; UTC+8 assumed at China GPS; flight median, nadir view approximation",
        sensor_response_source="M3M XMP BandFreq; legacy rectangular half-width approximation, not measured SRF",
        solar_zenith=metadata["median_solar_zenith_deg"], view_zenith=0., relative_azimuth=0.,
        ranges={"n":(1.2,2.2),"cab":(20,70),"car":(4,18),"cbrown":(0,.5),"cw":(.005,.035),
                "cm":(.004,.020),"lai":(.3,7),"lidfa":(30,70),"hspot":(.01,.30),"rsoil":(.5,1.5),"psoil":(0,1)},
        bands=[{"name":n,"center_nm":c,"half_width_nm":h} for n,c,h in (
            ("Red",650,16),("Green",560,16),("RedEdge",730,16),("NIR",860,26))])
    declared = OUT / "lut-declaration.json"
    content = declaration.model_dump_json(indent=2)
    if declared.exists():
        assert declared.read_text(encoding="utf-8") == content, "Frozen declaration changed; retain original experiment"
    else:
        declared.write_text(content, encoding="utf-8")
    app = create_app(config, enable_agent=False)
    engine = app.state.engine
    with TestClient(app, base_url="http://127.0.0.1", headers={"origin":"http://127.0.0.1"}) as client:
        client.get("/session")
        ref = client.post("/resources/register", json={"request_id":"migration-lut-declaration-v1",
            "path":str(declared),"read_only":True}).json()["resource_id"]
        task = client.post("/tasks", json={"request_id":"migration-lut-task-v1", "objective":"新航次LUT与迁移数值契约检查；不代表实测精度",
            "input_refs":[ref],"requested_method_id":"imagery.prosail_legacy_nn.v1",
            "constraints":{"allowed_capabilities":["code.execute","job.observe"]}}).json()["task_id"]

        def wait(receipt):
            assert receipt["kind"] == "completed", receipt
            job_id = receipt["job"]["job_id"]
            print(json.dumps({"job_id":job_id,"status":"observing"}), flush=True)
            deadline = time.monotonic()+300
            while True:
                engine.jobs.tick()
                job = engine.jobs.read(job_id)
                if job["status"] in TERMINAL:
                    break
                if job.get("blocker"):
                    raise RuntimeError(json.dumps(job["blocker"]))
                if time.monotonic()>deadline:
                    raise TimeoutError("Job retained; observe same job rather than resubmit")
                time.sleep(2)
            assert job["status"] == "succeeded", engine.jobs.public(job)
            return job

        response = client.post(f"/tasks/{task}/imagery", json={"request_id":"migration-lut-v1",
            "expected_spec_revision":1,"operation":"build_lut","input_refs":{},"declaration_ref":ref,"seconds":300})
        lut_job = wait(response.json())
        artifacts = [engine.resources.artifact(a,task) for a in lut_job["artifact_ids"]]
        lut = next(a for a in artifacts if a["name"] == "prosail_lut.npz")
        worker = (ROOT / "src/forestry_agent/imagery_worker.py").read_text(encoding="utf-8")
        # Worker definitions imported into one contract Job without invoking main.
        definitions = worker.rsplit('if __name__ == "__main__":',1)[0]
        assertions = '''
import numpy as np
import rasterio
from rasterio.transform import from_origin
lut_path = Path("/inputs/LUT_REF")
with np.load(lut_path, allow_pickle=False) as f:
    spectra, expected_lai, expected_cab = f['reflectance'], f['lai'], f['cab']
values = np.zeros((5, 4, 4), dtype='float32')
for j in range(4): values[j] = spectra[20,j]
values[4] = 255
values[4,0,0]=0
values[0,0,1]=1.1
values[0,0,2]=np.nan
transform = from_origin(500000,4700000,.1,.1)
source = Path('/output/contract-source.tif')
with rasterio.open(source,'w',driver='GTiff',height=4,width=4,count=5,dtype='float32',crs='EPSG:32650',transform=transform) as dst:
    dst.write(values)
    dst.descriptions=('Red','Green','RedEdge','NIR','Alpha')
d={'reflectance':'calibrated_fraction_0_1','calibration_source':'exact LUT synthetic numerical contract',
   'band_mapping_source':'explicit contract order','band_names':['Red','Green','RedEdge','NIR'],
   'raster_bands':[1,2,3,4],'alpha_band':5,'ndvi_threshold':None,'red_name':'Red','nir_name':'NIR'}
r=invert(source,lut_path,d,Path('/output'),1)
assert r['valid_pixels']==13,r
with rasterio.open('/output/prosail_lai_cab_rmse.tif') as src:
    a=src.read()
    assert src.crs.to_epsg()==32650 and src.transform==transform
    assert np.isnan(a[:,0,:3]).all()
    assert np.allclose(a[0,1:,:],expected_lai[20],atol=1e-6)
    assert np.allclose(a[1,1:,:],expected_cab[20],atol=1e-5)
    assert np.all(a[2,1:,:]==0)
write_json('/output/contract-result.json',{'status':'passed','valid_pixels':13,'grid_preserved':True,
    'invalid_alpha_range_nan_excluded':True,'exact_spectrum_retrieval':True,'field_accuracy_claim':False})
'''.replace("LUT_REF", lut["artifact_id"])
        code = definitions + assertions
        deps = Deps(engine, task, "migration-contract", 1)
        receipt = engine.perform(deps,"migration-numeric-contract-v1","code.execute",
            {"code":code,"input_refs":[lut["artifact_id"]]},lambda a:engine.jobs.submit(
                task,1,a,code,[lut["artifact_id"]],300,runtime_profile="imagery",reuse_equivalent=True))
        checked = wait(receipt)
        result = {"task_id":task,"image":IMAGE,"lut_job":lut_job["job_id"],"lut_artifact":lut["artifact_id"],
            "contract_job":checked["job_id"],"contract":"passed","real_orthomosaic":"not_run_by_this_script",
            "model_requests":0,"field_accuracy":"not_evaluated"}
        (OUT/"numerical-validation.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result),flush=True)


if __name__ == "__main__":
    main()
