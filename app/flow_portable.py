"""Build readable, single-file recorded Flows from reviewed execution source.

Only referenced declarations are included. A small in-memory import loader
preserves Python module namespaces without an installation or adjacent files.
No bytecode, archives, third-party libraries, database or application server
are bundled. Source modules remain readable in the generated Python file.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path

from app import flow_recording

HEADER = '# Metronome portable recorded Flow v1\n'
ROOT = Path(__file__).resolve().parent
DEPENDENCIES = ('playwright', 'httpx', 'openpyxl', 'xlrd', 'pyxlsb', 'sqlalchemy', 'psycopg2-binary', 'tzdata')
CONFIG_SOURCE = '''import os
UPLOAD_PGDATABASE = os.getenv('DG_UPLOAD_PGDATABASE') or os.getenv('PGDATABASE', '')
UPLOAD_PGHOST = os.getenv('DG_UPLOAD_PGHOST') or os.getenv('PGHOST', '')
UPLOAD_PGPASSWORD = os.getenv('DG_UPLOAD_PGPASSWORD', '')
UPLOAD_PGPORT = os.getenv('DG_UPLOAD_PGPORT') or os.getenv('PGPORT', '5432')
UPLOAD_PGUSER = os.getenv('DG_UPLOAD_PGUSER', '')
'''


def _module_import(node, alias):
    if isinstance(node, ast.ImportFrom):
        if node.module == 'app':
            return alias.name, None
        if node.module and node.module.startswith('app.'):
            return node.module[4:], alias.name
    elif alias.name.startswith('app.'):
        return alias.name[4:], None
    elif (ROOT / f'{alias.name}.py').is_file():
        return alias.name, None
    return None


class _Rebase(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if node.module == 'app' or (node.module or '').startswith('app.'):
            node.module = '_mf' + node.module[3:]
        return node

    def visit_Import(self, node):
        if len(node.names) == 1 and _module_import(node, node.names[0]):
            alias = node.names[0]
            return ast.copy_location(ast.ImportFrom(module='_mf', names=[ast.alias(name=alias.name.removeprefix('app.'), asname=alias.asname)], level=0), node)
        return node


@lru_cache(maxsize=2)
def execution_sources(entry='recorded'):
    trees, declarations, imports, selected = {}, {}, {}, {}

    def index(module):
        if module == 'config' or module in trees:
            return
        if not module.startswith('flow_'):
            raise ValueError(f'Portable execution unexpectedly depends on application module {module}.')
        tree = ast.parse((ROOT / f'{module}.py').read_text(encoding='utf-8'))
        trees[module] = tree
        declarations[module], imports[module], selected[module] = {}, {}, set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                declarations[module][node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name):
                            declarations[module][name.id] = node
        # Include guarded module imports, but never hoist function-local
        # platform imports such as msvcrt/fcntl into the generated module.
        def import_nodes(nodes):
            for node in nodes:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    yield node
                elif isinstance(node, (ast.Try, ast.If)):
                    yield from import_nodes(node.body)
                    if isinstance(node, ast.Try):
                        for handler in node.handlers:
                            yield from import_nodes(handler.body)
        for node in import_nodes(tree.body):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                imports[module].setdefault(name, (node, alias))

    def include(module, symbol):
        if module == 'config':
            selected.setdefault('config', set())
            return
        index(module)
        node = declarations[module].get(symbol)
        if node is None:
            if symbol in imports[module]:
                node, alias = imports[module][symbol]
            else:
                raise ValueError(f'Portable dependency cannot be resolved: {module}.{symbol}')
        if node in selected[module]:
            return
        selected[module].add(node)
        references = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        bindings = dict(imports[module])
        for imported in ast.walk(node):
            if isinstance(imported, (ast.Import, ast.ImportFrom)):
                for alias in imported.names:
                    bindings[alias.asname or alias.name.split('.')[0]] = (imported, alias)
        for name in references:
            if name in declarations[module] and declarations[module][name] is not node:
                include(module, name)
            if name not in bindings:
                continue
            imported, alias = bindings[name]
            dependency = _module_import(imported, alias)
            if imported in [entry[0] for entry in imports[module].values()]:
                # Retain only the used alias, not every sibling in a large
                # from-import of unrelated portal adapters.
                isolated = copy.deepcopy(imported)
                isolated.names = [copy.deepcopy(alias)]
                selected[module].add(ast.unparse(isolated))
            if dependency:
                target_module, target_symbol = dependency
                if target_symbol:
                    include(target_module, target_symbol)
                else:
                    attributes = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
                                  and isinstance(n.value, ast.Name) and n.value.id == name}
                    for attribute in attributes:
                        include(target_module, attribute)
        # Imports selected directly (e.g. annotations) need their dependency.
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                dependency = _module_import(node, alias)
                if dependency and dependency[1]:
                    include(*dependency)

    if entry == 'recorded':
        include('flow_recording_runtime', 'standalone_main')
    else:
        include('flow_standalone', 'offline_main')
    result = {}
    for module, nodes in selected.items():
        if module == 'config':
            result[module] = CONFIG_SOURCE
            continue
        import_text = sorted({node for node in nodes if isinstance(node, str)})
        body = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
        for text in import_text:
            body.extend(ast.parse(text).body)
        body.extend(copy.deepcopy(node) for node in trees[module].body if node in nodes)
        rewritten = _Rebase().visit(ast.Module(body=body, type_ignores=[]))
        if module == 'flow_outlook':
            for node in rewritten.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_EMBEDDED_SCRIPT' for t in node.targets):
                    node.value = ast.Constant((ROOT.parent / 'tools' / 'outlook_flow_attachment.ps1').read_text(encoding='utf-8-sig'))
        result[module] = ast.unparse(ast.fix_missing_locations(rewritten)) + '\n'
        compile(result[module], f'portable:{module}', 'exec')
    return result


def execution_hash():
    return flow_recording.digest(execution_sources())


def configuration_hash(job):
    from app.flow_standalone import freeze
    frozen = freeze(job)
    frozen.pop('recording_parameters', None)
    frozen.pop('handover', None)
    return flow_recording.digest(frozen)


def freeze_transformation(job):
    if not job.get('transformation', {}).get('enabled'):
        return None
    path = Path(job['transformation'].get('script_path') or '')
    if path.suffix.lower() != '.py':
        raise ValueError('Portable recorded Flows require a Python transformation.')
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            if name in {'__import__', 'import_module', 'exec', 'eval', 'system', 'Popen'}:
                raise ValueError('Transformation uses dynamic code or an external program; make its dependencies explicit before export.')
            if name in {'open', 'Path', 'read_csv', 'read_excel', 'read_parquet', 'read_json'} and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                raise ValueError('Transformation uses an external file/resource literal; use the declared --input/--output contract.')
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value and any(isinstance(target, ast.Name) and flow_recording.SENSITIVE.search(target.id) for target in node.targets):
            raise ValueError('Transformation contains a literal credential; use a protected credential provider.')
        if isinstance(node, ast.Name) and node.id == '__file__':
            raise ValueError('Transformation uses adjacent resources through __file__; make its input/output dependencies explicit first.')
        if isinstance(node, ast.ImportFrom) and node.level:
            raise ValueError('Transformation relative imports must be bundled into its Python source.')
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
            for name in names:
                base = name.split('.')[0]
                if base == 'app' or (path.parent / f'{base}.py').exists() or (path.parent / base / '__init__.py').exists():
                    raise ValueError(f'Transformation depends on local module {base}; include it in the transformation first.')
                if importlib.util.find_spec(base) is None:
                    raise ValueError(f'Transformation dependency {base} is not installed.')
    return source


def source(job):
    from app.flow_standalone import freeze
    job = freeze(job)
    job.pop('recording_parameters', None)
    recorded = job['flow'].get('execution_method') == 'recorded'
    if recorded:
        flow_recording.validate_definition(job['recording']['definition'])
        if job['recording'].get('engine_hash') != execution_hash():
            raise ValueError('The recorded execution core changed; validate a new revision before generating.')
    sources = execution_sources('recorded' if recorded else 'catalog')
    versions = {}
    for package in DEPENDENCIES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    transform = job.get('recording', {}).get('transformation_source') or ''
    packages = importlib.metadata.packages_distributions() if transform else {}
    for node in ast.walk(ast.parse(transform)):
        names = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
        for name in names:
            base = (name or '').split('.')[0]
            if base in sys.stdlib_module_names:
                continue
            for package in packages.get(base, []):
                versions[package] = importlib.metadata.version(package)
    header = (HEADER if recorded else '# Metronome portable catalog Flow v1\n') + '# Requires Python 3.11+, the saved Chrome/Edge browser, and these installed libraries:\n'
    header += '# ' + ', '.join(f'{key}=={value}' for key, value in sorted(versions.items())) + '\n'
    header += '# Configuration and readable execution source are included below. Credentials are not.\n'
    header += 'import sys, json, importlib.abc, importlib.util\n'
    header += 'FLOW = json.loads(' + repr(flow_recording.canonical(job)) + ')\n\n'
    header += 'SOURCES = {}\n'
    for module, code in sorted(sources.items()):
        # repr is a Python literal; use a multiline string for readable source.
        escaped = code.replace('\\', '\\\\').replace("'''", "\\'\\'\\'")
        header += f"\n# ---- {module} ----\nSOURCES[{module!r}] = '''\n{escaped}'''\n"
    header += '''
class _FlowModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == '_mf' or fullname.startswith('_mf.') and fullname[4:] in SOURCES:
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == '_mf')
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        if module.__name__ != '_mf':
            module.__file__ = __file__
            exec(compile(SOURCES[module.__name__[4:]], __file__ + ':' + module.__name__, 'exec'), module.__dict__)
sys.meta_path.insert(0, _FlowModules())
if __name__ == '__main__':
    from _mf.flow_recording_runtime import standalone_main
    raise SystemExit(standalone_main(FLOW))
'''
    if not recorded:
        header = header.replace('from _mf.flow_recording_runtime import standalone_main\n    raise SystemExit(standalone_main(FLOW))',
                                'from _mf.flow_standalone import offline_main\n    raise SystemExit(offline_main(FLOW))')
    compile(header, 'run_flow.py', 'exec')
    return header


def generate(job):
    from app import flow_layout
    from app.flow_standalone import _atomic_text
    from app.flow_paths import assert_job_paths
    assert_job_paths(job)
    folder = Path(job['paths']['flow_folder'])
    flow_layout.ensure_layout(folder, job['flow']['id'])
    content = source(job)
    checksum = hashlib.sha256(content.encode()).hexdigest()
    scripts = folder / 'Scripts'
    target = scripts / 'run_flow.py'
    flow_layout._regular(target)
    manifest = flow_layout.read_manifest(folder, job['flow']['id'])
    if target.exists():
        previous = target.read_text(encoding='utf-8')
        expected = (manifest.get('standalone') or {}).get('launcher_hash')
        if not expected or hashlib.sha256(previous.encode()).hexdigest() != expected:
            raise ValueError('Scripts/run_flow.py was modified; preserve or rename it before regenerating.')
    versions = scripts / 'versions'
    flow_layout._regular(versions)
    versions.mkdir(exist_ok=True)
    if target.exists():
        old_version = versions / f'run_flow-{expected}.py'
        flow_layout._regular(old_version)
        if old_version.exists() and old_version.read_text(encoding='utf-8') != previous:
            raise ValueError('The previous immutable script revision was modified.')
        if not old_version.exists():
            with old_version.open('x', encoding='utf-8') as stream:
                stream.write(previous)
    version = versions / f'run_flow-{checksum}.py'
    flow_layout._regular(version)
    if version.exists() and version.read_text(encoding='utf-8') != content:
        raise ValueError('The immutable script revision was modified.')
    if not version.exists():
        with version.open('x', encoding='utf-8') as stream:
            stream.write(content)
    _atomic_text(target, content)
    flow_layout.update_manifest(folder, job['flow']['id'], standalone={
        'version': 2, 'kind': 'portable_recorded' if job['flow'].get('execution_method') == 'recorded' else 'portable_catalog', 'launcher_hash': checksum,
        'config_hash': configuration_hash(job),
        'recording_revision': job.get('recording', {}).get('revision'), 'script_revision': str(version)})
    return {'state': 'current', 'kind': 'portable_recorded' if job['flow'].get('execution_method') == 'recorded' else 'portable_catalog', 'launcher': str(target),
            'script_revision': str(version), 'launcher_hash': checksum}
