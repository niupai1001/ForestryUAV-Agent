"""Trusted source inspection, executed only in existing restricted offline Jobs.

Archive validation is shared with source building. Backend hooks run through the
public PyPA frontend in a temporary hash-checked environment, never on the host.
"""

FRONTEND_REQUIREMENTS = ['pyproject-hooks==1.2.0', 'packaging==25.0']

WORKER = r'''
def inspect_build_sources(root, wheelhouse, sources, wheels, clean_env, image_reference, mode):
    import tomllib, venv

    def bounded_requirements(value):
        if (not isinstance(value, list) or len(value) > 32 or
            any(not isinstance(r, str) or not r or len(r.encode('utf-8')) > 2000 for r in value)):
            raise RuntimeError('Build requirements require at most 32 bounded PEP508 strings')
        return value

    def inspect(project, source, index):
        pyproject = project/'pyproject.toml'
        config = {}
        if pyproject.exists():
            with pyproject.open('rb') as reader:
                data = reader.read(1024*1024+1)
            if len(data) > 1024*1024:
                raise RuntimeError('pyproject.toml exceeds inspection limit')
            config = tomllib.loads(data.decode('utf-8'))
        system = config.get('build-system')
        if system is None:
            if not (project/'setup.py').is_file():
                raise RuntimeError('No build-system or legacy setup.py')
            system = {'requires':['setuptools>=40.8.0'], 'build-backend':'setuptools.build_meta:__legacy__'}
        if not isinstance(system, dict) or 'requires' not in system:
            raise RuntimeError('build-system requires must be declared')
        requirements = bounded_requirements(system['requires'])
        backend = system.get('build-backend', 'setuptools.build_meta:__legacy__')
        paths = system.get('backend-path', [])
        if (not isinstance(backend, str) or not backend or len(backend) > 1024 or
            not isinstance(paths, list) or len(paths) > 32 or
            any(not isinstance(p, str) or len(p) > 1024 or
                not (project/p).resolve().is_relative_to(project.resolve()) for p in paths)):
            raise RuntimeError('Invalid or escaping build backend configuration')
        dynamic = []
        frontend = None
        if mode == 'dynamic':
            environment = root/('hook-env-'+str(index))
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment/'bin/python'
            locked = root/('hook-install-'+str(index)+'.lock')
            locked.write_text('\n'.join(str(wheelhouse/w['filename'])+' --hash=sha256:'+w['sha256']
                for w in wheels)+'\n', encoding='utf-8')
            command = [str(python), '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
                '--no-cache-dir', 'install', '--no-index', '--only-binary', ':all:', '--require-hashes',
                '--no-compile', '--find-links', str(wheelhouse), '-r', str(locked)]
            subprocess.run(command, env=clean_env, check=True)
            request = root/('hook-request-'+str(index)+'.json')
            result = root/('hook-result-'+str(index)+'.json')
            request.write_text(json.dumps({'project':str(project),'backend':backend,'paths':paths,
                'requirements':requirements,'wheelhouse':str(wheelhouse)}), encoding='utf-8')
            script = root/'invoke-build-hook.py'
            script.write_text(HOOK_CALLER, encoding='utf-8')
            subprocess.run([str(python), '-I', '-B', str(script), str(request), str(result)], env=clean_env, check=True)
            with result.open('rb') as reader:
                data = reader.read(65536+1)
            if len(data) > 65536:
                raise RuntimeError('Build hook result exceeds limit')
            returned = json.loads(data)
            dynamic = bounded_requirements(returned['requirements'])
            frontend = returned['frontend']
            if frontend != {'name':'pyproject-hooks','version':'1.2.0','hook':'get_requires_for_build_wheel'}:
                raise RuntimeError('Build hook frontend identity mismatch')
        return {'source_resource_id':source['resource_id'],'source_sha256':source['sha256'],
            'static_requires':requirements,'dynamic_requires':dynamic,'build_backend':backend,
            'backend_path':paths,'frontend':frontend}

    _, records = build_sources(root, wheelhouse, sources, clean_env, inspector=inspect)
    manifest = {'schema_version':1,'kind':'source_build_requirements','inspection':mode,
        'image_reference':image_reference,'python':platform.python_version(),'network':'none',
        'sources':sources,'wheels':wheels,'projects':records,'runtime_installation':False}
    text = json.dumps(manifest,sort_keys=True,indent=2)
    if len(text.encode('utf-8')) > 256*1024:
        raise RuntimeError('Source inspection manifest exceeds limit')
    Path('/output/build-requirements.json').write_text(text,encoding='utf-8')
    print('SOURCE_BUILD_REQUIREMENTS_READY',flush=True)
'''

HOOK_CALLER = r'''
import importlib.metadata, json, subprocess, sys
from pathlib import Path
from pyproject_hooks import BuildBackendHookCaller
from packaging.requirements import Requirement
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
for value in request['requirements']:
    if len(value)>500 or '\n' in value or '\r' in value or Requirement(value).url:
        raise RuntimeError('Only bounded public PEP508 build requirements are supported')
# Native pip checks static versions/extras/markers using only files already
# hash-checked by the outer worker; no other index/source is admitted.
static = Path(sys.argv[1]).with_suffix('.requirements.txt')
static.write_text('\n'.join(request['requirements'])+'\n',encoding='utf-8')
subprocess.run([sys.executable,'-I','-B','-m','pip','--isolated',
    '--disable-pip-version-check','--no-cache-dir','install','--no-index',
    '--only-binary',':all:','--find-links',request['wheelhouse'],'-r',str(static)],check=True)
def runner(cmd, cwd=None, extra_environ=None):
    import os
    env = dict(os.environ)
    env.update(extra_environ or {})
    subprocess.run(cmd, cwd=cwd, env=env, check=True)
caller = BuildBackendHookCaller(request['project'], request['backend'], backend_path=request['paths'],
    python_executable=sys.executable, runner=runner)
requirements = caller.get_requires_for_build_wheel(config_settings=None)
if (not isinstance(requirements,list) or len(requirements)>32 or
    any(not isinstance(r,str) or not r or len(r.encode('utf-8'))>2000 for r in requirements)):
    raise RuntimeError('Build hook requires at most 32 bounded requirement strings')
Path(sys.argv[2]).write_text(json.dumps({'requirements':requirements,'frontend':{
    'name':'pyproject-hooks','version':importlib.metadata.version('pyproject-hooks'),
    'hook':'get_requires_for_build_wheel'}}),encoding='utf-8')
'''
