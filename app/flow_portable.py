"""Build readable, single-file Flows from the reviewed execution source.

Only referenced declarations are included. They are copied verbatim, comments
included, from the application modules so a fix made in the generated file can
be carried back to app/<module>.py unchanged. A small in-file import loader
keeps every module in its own namespace and compiles each section with the
generated file's real line numbers, so tracebacks, breakpoints and editors all
point at the lines the operator sees. No bytecode, archives, third-party
libraries, database or application server are bundled.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from app import flow_recording

HEADER = '# Metronome portable recorded Flow v2\n'
CATALOG_HEADER = '# Metronome portable catalog Flow v2\n'
SECTION_MARK = '# ==== module: '
SECTION_END = ' ===='
ROOT = Path(__file__).resolve().parent
DEPENDENCIES = ('playwright', 'httpx', 'openpyxl', 'xlrd', 'pyxlsb', 'sqlalchemy', 'psycopg2-binary', 'tzdata')
CONFIG_SOURCE = '''import os
UPLOAD_PGDATABASE = os.getenv('DG_UPLOAD_PGDATABASE') or os.getenv('PGDATABASE', '')
UPLOAD_PGHOST = os.getenv('DG_UPLOAD_PGHOST') or os.getenv('PGHOST', '')
UPLOAD_PGPASSWORD = os.getenv('DG_UPLOAD_PGPASSWORD', '')
UPLOAD_PGPORT = os.getenv('DG_UPLOAD_PGPORT') or os.getenv('PGPORT', '5432')
UPLOAD_PGUSER = os.getenv('DG_UPLOAD_PGUSER', '')
'''

GUIDE = '''#
# HOW TO USE
#   python run_flow.py --dry-run    check the saved configuration; downloads, writes and SQL are skipped
#   python run_flow.py              run the whole Flow start to finish (README.md beside this file has the details)
#   Progress is printed to stderr while the Flow runs (--quiet silences it) and a failure prints the
#   full Python traceback with this file's line numbers. JSONL logs are kept under Scripts/standalone-logs.
#
# HOW THIS FILE IS ORGANISED
#   1. FLOW: the saved configuration, frozen when Metronome generated this file.
#   2. A small loader that turns every "# ==== module: NAME ====" section below into the importable
#      module _mf.NAME while keeping this file's real line numbers.
#   3. One section per Metronome module (app/NAME.py in the repository) with the declarations this
#      Flow needs, copied verbatim with their comments. Only imports of other Metronome modules are
#      rewritten to the in-file _mf package.
#
# HOW TO TROUBLESHOOT AND PORT A FIX
#   Edit and rerun this file freely. On the next save Metronome archives your edited copy under
#   Scripts/versions and refreshes run_flow.py from the current application code, so nothing is lost.
#   To make a fix permanent, copy the changed function back into app/NAME.py in the Metronome
#   repository (same module, same name). After the application is updated, every Flow's run_flow.py
#   is regenerated with that code. Metronome's database, not this file, defines the scheduled Flow.
#
'''

# The loader below is copied into every generated file. It reads the file once,
# splits it at the section markers and compiles each section at its real line
# offset so tracebacks and debuggers refer to run_flow.py itself.
PROGRAM = '''from __future__ import annotations
import __future__ as _future
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path

FLOW = json.loads(r"""
__FLOW_JSON__
""")

_HERE = Path(__file__).resolve()
_TEXT = _HERE.read_text(encoding='utf-8')
_MARK = '# ==== ' + 'module: '


def _sections():
    """Map each module section to its zero-based first line and text."""
    sections, name, start = {}, None, 0
    lines = _TEXT.split('\\n')
    for index, line in enumerate(lines):
        if line.startswith(_MARK) and line.endswith(' ===='):
            if name is not None:
                sections[name] = (start, '\\n'.join(lines[start:index]))
            name, start = line[len(_MARK):-5].strip(), index + 1
    if name is not None:
        sections[name] = (start, '\\n'.join(lines[start:]))
    return sections


SECTIONS = _sections()


class _FlowModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == '_mf' or (fullname.startswith('_mf.') and fullname[4:] in SECTIONS):
            return importlib.util.spec_from_loader(fullname, self, origin=str(_HERE), is_package=fullname == '_mf')
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if module.__name__ == '_mf':
            return
        start, text = SECTIONS[module.__name__[4:]]
        module.__file__ = str(_HERE)
        code = compile('\\n' * start + text, str(_HERE), 'exec', flags=_future.annotations.compiler_flag, dont_inherit=True)
        exec(code, module.__dict__)

    def get_source(self, fullname):
        if fullname == '_mf':
            return ''
        start, text = SECTIONS[fullname[4:]]
        return '\\n' * start + text


sys.meta_path.insert(0, _FlowModules())

if __name__ == '__main__':
    from _mf.__ENTRY_MODULE__ import __ENTRY_FUNCTION__
    sys.exit(__ENTRY_FUNCTION__(FLOW))

# ---------------------------------------------------------------------------
# Execution source. The sections below are loaded on demand by the loader
# above; when this file runs as a program the process exits before reaching
# them. If the file is imported or loaded with runpy instead, the same
# declarations are also defined here at file level, which is harmless.
# ---------------------------------------------------------------------------
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


def _rebased_import(node) -> str:
    """Unparse one import statement with application modules rewritten to _mf."""
    if isinstance(node, ast.Import) and len(node.names) > 1:
        return '; '.join(ast.unparse(_Rebase().visit(ast.Import(names=[copy.deepcopy(alias)]))) for alias in node.names)
    return ast.unparse(_Rebase().visit(copy.deepcopy(node)))


def _char_offset(line: str, byte_offset: int) -> int:
    return len(line.encode('utf-8')[:byte_offset].decode('utf-8', errors='replace'))


def _rewrite_nested_imports(segment: list[str], node, start: int) -> None:
    """Splice rewritten imports found inside a declaration in place, keeping line counts."""
    imports = [child for child in ast.walk(node) if isinstance(child, (ast.Import, ast.ImportFrom))
               and child is not node and any(_module_import(child, alias) for alias in child.names)]
    for imported in sorted(imports, key=lambda child: (child.lineno, child.col_offset), reverse=True):
        first, last = imported.lineno - start, imported.end_lineno - start
        head = segment[first][:_char_offset(segment[first], imported.col_offset)]
        tail = segment[last][_char_offset(segment[last], imported.end_col_offset):]
        rebased = _rebased_import(imported)
        if first == last:
            segment[first] = head + rebased + tail
            continue
        if tail.strip() and not tail.lstrip().startswith('#'):
            raise ValueError('A multi-line import of a Metronome module shares its last line with other code; put it on its own lines.')
        segment[first:last + 1] = [head + rebased + '\n' * (last - first) + tail]


def _declaration_start(lines: list[str], node, floor: int) -> int:
    """First line of a declaration including decorators and the comment lines directly above it."""
    start = min([node.lineno] + [decorator.lineno for decorator in getattr(node, 'decorator_list', [])])
    while start - 1 > floor and lines[start - 2].lstrip().startswith('#'):
        start -= 1
    return start


def _declaration_text(lines: list[str], node, floor: int) -> str:
    start = _declaration_start(lines, node, floor)
    segment = lines[start - 1:node.end_lineno]
    _rewrite_nested_imports(segment, node, start)
    return '\n'.join(segment)


def _text_literal(text: str) -> str:
    """A readable raw literal when it round-trips exactly, otherwise repr."""
    if "'''" not in text and not text.endswith('\\'):
        literal = "r'''" + text + "'''"
        try:
            if ast.literal_eval(literal) == text:
                return literal
        except (SyntaxError, ValueError):
            pass
    return repr(text)


def _is_embedded_script(node) -> bool:
    return isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '_EMBEDDED_SCRIPT' for target in node.targets)


def _assemble_module(module: str, tree, text: str, nodes: set, aliases: dict) -> str:
    """Verbatim declarations of one module, in source order, behind a rewritten import prologue."""
    lines = text.split('\n')
    spans = [(node.lineno, node.end_lineno) for node in tree.body]
    for (_, previous_end), (next_start, _) in zip(spans, spans[1:]):
        if next_start <= previous_end:
            raise ValueError(f'{module}.py has top-level statements sharing a line; portable slicing needs one per line.')
    docstring = ast.get_docstring(tree, clean=True) or ''
    header = [f'# Source: app/{module}.py' + (f' -- {docstring.splitlines()[0]}' if docstring else ''),
              '# Copied from the Metronome application; only imports of other Metronome modules are rewritten to _mf.']
    prologue = set()
    for imported, names in aliases.items():
        for alias in names:
            isolated = copy.deepcopy(imported)
            isolated.names = [copy.deepcopy(alias)]
            prologue.add(_rebased_import(isolated))
    parts = ['\n'.join(header)]
    if prologue:
        parts.append('\n'.join(sorted(prologue)))
    floor = 0
    for node in tree.body:
        if node in nodes:
            if module == 'flow_outlook' and _is_embedded_script(node):
                helper = (ROOT.parent / 'tools' / 'outlook_flow_attachment.ps1').read_text(encoding='utf-8-sig')
                parts.append('_EMBEDDED_SCRIPT = ' + _text_literal(helper))
            else:
                parts.append(_declaration_text(lines, node, floor))
        floor = node.end_lineno
    section = '\n\n'.join(parts) + '\n'
    _check_section(module, section)
    return section


def _check_section(module: str, section: str) -> None:
    import __future__
    for line in section.split('\n'):
        if line.startswith(SECTION_MARK):
            raise ValueError(f'{module} source contains a section marker line.')
    parsed = ast.parse(section)
    for node in parsed.body:
        if isinstance(node, ast.ImportFrom) and node.module == '__future__':
            raise ValueError(f'{module} source must not carry its own __future__ import.')
    compile(section, f'portable:{module}', 'exec', flags=__future__.annotations.compiler_flag, dont_inherit=True)


def split_sections(text: str) -> dict[str, tuple[int, str]]:
    """The generated file's own section split, for self-checks and tests."""
    sections, name, start = {}, None, 0
    lines = text.split('\n')
    for index, line in enumerate(lines):
        if line.startswith(SECTION_MARK) and line.endswith(SECTION_END):
            if name is not None:
                sections[name] = (start, '\n'.join(lines[start:index]))
            name, start = line[len(SECTION_MARK):-len(SECTION_END)].strip(), index + 1
    if name is not None:
        sections[name] = (start, '\n'.join(lines[start:]))
    return sections


@lru_cache(maxsize=2)
def execution_sources(entry='recorded'):
    texts, trees, declarations, imports, selected, aliases = {}, {}, {}, {}, {}, {}

    def index(module):
        if module == 'config' or module in trees:
            return
        if not module.startswith('flow_'):
            raise ValueError(f'Portable execution unexpectedly depends on application module {module}.')
        texts[module] = (ROOT / f'{module}.py').read_text(encoding='utf-8')
        tree = ast.parse(texts[module])
        trees[module] = tree
        declarations[module], imports[module], selected[module], aliases[module] = {}, {}, set(), {}
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

    def retain(module, imported, alias) -> bool:
        """Keep one alias of a module-level import in the prologue; True when newly added."""
        names = aliases[module].setdefault(imported, [])
        if any(kept is alias for kept in names):
            return False
        names.append(alias)
        return True

    def include(module, symbol):
        if module == 'config':
            selected.setdefault('config', set())
            return
        index(module)
        node = declarations[module].get(symbol)
        if node is None:
            if symbol not in imports[module]:
                raise ValueError(f'Portable dependency cannot be resolved: {module}.{symbol}')
            imported, alias = imports[module][symbol]
            if retain(module, imported, alias):
                dependency = _module_import(imported, alias)
                if dependency and dependency[1]:
                    include(*dependency)
            return
        if node in selected[module]:
            return
        selected[module].add(node)
        references = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        bindings = dict(imports[module])
        for imported in ast.walk(node):
            if isinstance(imported, (ast.Import, ast.ImportFrom)):
                for alias in imported.names:
                    bindings[alias.asname or alias.name.split('.')[0]] = (imported, alias)
        module_imports = {id(entry[0]) for entry in imports[module].values()}
        for name in references:
            if name in declarations[module] and declarations[module][name] is not node:
                include(module, name)
            if name not in bindings:
                continue
            imported, alias = bindings[name]
            if id(imported) in module_imports:
                # Retain only the used alias, not every sibling in a large
                # from-import of unrelated portal adapters.
                retain(module, imported, alias)
            dependency = _module_import(imported, alias)
            if dependency:
                target_module, target_symbol = dependency
                if target_symbol:
                    include(target_module, target_symbol)
                else:
                    attributes = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
                                  and isinstance(n.value, ast.Name) and n.value.id == name}
                    for attribute in attributes:
                        include(target_module, attribute)

    if entry == 'recorded':
        include('flow_recording_runtime', 'standalone_main')
    else:
        include('flow_standalone', 'offline_main')
    result = {}
    for module in selected:
        if module == 'config':
            result[module] = CONFIG_SOURCE
            continue
        result[module] = _assemble_module(module, trees[module], texts[module], selected[module], aliases[module])
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


def flow_json(job) -> str:
    """Readable configuration text that survives a raw triple-quoted literal."""
    text = json.dumps(job, indent=2, sort_keys=True, ensure_ascii=True)
    if '"""' in text or any(line.endswith('\\') for line in text.split('\n')):
        raise ValueError('The Flow configuration cannot be embedded as readable text.')
    return text


def _library_versions(job) -> dict:
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
    return versions


def _assert_no_application_imports(text: str) -> None:
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and (node.module == 'app' or (node.module or '').startswith('app.')):
            raise ValueError('The generated script still imports the installed application.')
        if isinstance(node, ast.Import) and any(alias.name == 'app' or alias.name.startswith('app.') for alias in node.names):
            raise ValueError('The generated script still imports the installed application.')


def source(job):
    from app.flow_standalone import freeze
    job = freeze(job)
    job.pop('recording_parameters', None)
    recorded = job['flow'].get('execution_method') == 'recorded'
    if recorded:
        flow_recording.validate_definition(job['recording']['definition'])
    sources = execution_sources('recorded' if recorded else 'catalog')
    core = flow_recording.digest(sources)
    versions = _library_versions(job)
    header = [(HEADER if recorded else CATALOG_HEADER).rstrip('\n'),
              '# Requires Python 3.11+, the saved Chrome/Edge browser, and these installed libraries:',
              '# ' + ', '.join(f'{key}=={value}' for key, value in sorted(versions.items())),
              '# Configuration and readable execution source are included below. Credentials are not.',
              f'# Execution core copied into this file: {core}']
    if recorded:
        tested = job['recording'].get('engine_hash') or 'not recorded'
        header.append(f"# Recording revision {job['recording'].get('revision')} was last tested with execution core {tested}"
                      + (' (the same code).' if tested == core else ' (an earlier version of the code).'))
    entry_module, entry_function = ('flow_recording_runtime', 'standalone_main') if recorded else ('flow_standalone', 'offline_main')
    # Substitute the entry tokens before the configuration is inserted so a
    # saved value that happens to contain a token is never rewritten.
    program = (PROGRAM.replace('__ENTRY_MODULE__', entry_module).replace('__ENTRY_FUNCTION__', entry_function)
               .replace('__FLOW_JSON__', flow_json(job)))
    sections = [f'{SECTION_MARK}{module}{SECTION_END}\n{code}' for module, code in sorted(sources.items())]
    text = '\n'.join(header) + '\n' + GUIDE + program + '\n'.join(sections)
    compile(text, 'run_flow.py', 'exec')
    _assert_no_application_imports(text)
    split = split_sections(text)
    for module, code in sources.items():
        if split[module][1].rstrip('\n') != code.rstrip('\n'):
            raise ValueError(f'The generated section for {module} does not round-trip.')
    return text


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _set_aside(path: Path) -> str | None:
    """Move an unexpected file out of the way instead of blocking the refresh."""
    from app import flow_layout
    flow_layout._regular(path)
    if not path.exists():
        return None
    aside = path.with_name(f'{path.stem}-edited-{_stamp()}{path.suffix}')
    counter = 0
    while aside.exists():
        counter += 1
        aside = path.with_name(f'{path.stem}-edited-{_stamp()}-{counter}{path.suffix}')
    os.replace(path, aside)
    return str(aside)


def _write_revision(path: Path, content: str) -> None:
    """Content-addressed archive copies are immutable; a differing file is set aside, never lost."""
    from app import flow_layout
    flow_layout._regular(path)
    if path.exists():
        if path.read_text(encoding='utf-8') == content:
            return
        _set_aside(path)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def preserve_edited_script(target: Path, expected_hash: str | None) -> str | None:
    """Archive an operator-edited run_flow.py under versions/ so a refresh never loses work."""
    from app import flow_layout
    flow_layout._regular(target)
    if not target.exists():
        return None
    content = target.read_text(encoding='utf-8')
    digest = hashlib.sha256(content.encode()).hexdigest()
    if expected_hash and digest == expected_hash:
        return None
    versions = target.parent / 'versions'
    flow_layout._regular(versions)
    versions.mkdir(exist_ok=True)
    for existing in sorted(versions.glob(f'run_flow-{digest}-edited-*.py')):
        flow_layout._regular(existing)
        if existing.read_text(encoding='utf-8') == content:
            return str(existing)
    archived = versions / f'run_flow-{digest}-edited-{_stamp()}.py'
    counter = 0
    while archived.exists():
        counter += 1
        archived = versions / f'run_flow-{digest}-edited-{_stamp()}-{counter}.py'
    with archived.open('x', encoding='utf-8') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return str(archived)


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
    expected = (manifest.get('standalone') or {}).get('launcher_hash')
    versions = scripts / 'versions'
    flow_layout._regular(versions)
    versions.mkdir(exist_ok=True)
    # An operator's edits are archived, never a reason to leave the script stale.
    archived = preserve_edited_script(target, expected)
    if target.exists() and not archived:
        _write_revision(versions / f'run_flow-{expected}.py', target.read_text(encoding='utf-8'))
    version = versions / f'run_flow-{checksum}.py'
    _write_revision(version, content)
    _atomic_text(target, content)
    kind = 'portable_recorded' if job['flow'].get('execution_method') == 'recorded' else 'portable_catalog'
    flow_layout.update_manifest(folder, job['flow']['id'], standalone={
        'version': 2, 'kind': kind, 'launcher_hash': checksum,
        'config_hash': configuration_hash(job), 'engine_hash': execution_hash(),
        'recording_revision': job.get('recording', {}).get('revision'), 'script_revision': str(version),
        'generated_at': datetime.now(timezone.utc).isoformat()})
    result = {'state': 'current', 'kind': kind, 'launcher': str(target),
              'script_revision': str(version), 'launcher_hash': checksum}
    if archived:
        result['archived_edit'] = archived
    return result
