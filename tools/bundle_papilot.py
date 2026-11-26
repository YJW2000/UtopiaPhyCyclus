"""Build the pinned Papilot web, review-engine and desktop integration assets."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.release-work'
UPSTREAM = 'https://github.com/PapilotAI/papilot.git'
COMMIT = 'a1d4dfffd5284e1bbbd3d36649ff82a5de897b31'
INSTALL = ['ci', '--ignore-scripts', '--prefer-offline', '--no-audit', '--no-fund', '--loglevel=error']
DESKTOP_HELPERS = (
    'agent-paths.mjs', 'environment-check.js', 'environment-ui.mjs', 'environment-window.js',
    'environment.css', 'environment.html', 'notifications.js', 'overleaf-sign-in.js',
    'platform.mjs', 'preload.cjs', 'runtime.js', 'wsl.js', 'wsl-runtime.mjs',
)
WSL_HELPERS = ('wsl-runtime.mjs', 'runtime.js', 'platform.mjs', 'agent-paths.mjs')


def node_tools() -> tuple[str, Path, str]:
    node = shutil.which('node')
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not node or not npm:
        raise RuntimeError('Install official Node.js 22.22+ with npm to build the Papilot workspace.')
    node_version = subprocess.check_output([node, '-p', 'process.versions.node'], text=True).strip()
    major, minor, _patch = (int(number) for number in node_version.split('.'))
    if (major, minor) < (22, 22):
        raise RuntimeError(f'Node.js {node_version} is too old for Papilot.')
    candidates = (
        Path(npm).resolve(), Path(npm).with_name('node_modules') / 'npm/bin/npm-cli.js',
        Path(node).parent.parent / 'lib/node_modules/npm/bin/npm-cli.js',
    )
    cli = next((candidate.resolve() for candidate in candidates if candidate.name == 'npm-cli.js' and candidate.is_file()), None)
    if cli is None:
        raise RuntimeError('Could not locate the npm CLI in the Node.js installation.')
    return node, cli, node_version


def export_source(source: Path, checkout: Path | None, scratch: Path) -> None:
    git = shutil.which('git')
    if not git:
        raise RuntimeError('Git is required to verify and export the pinned Papilot source.')
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    if checkout is None:
        checkout = WORK / 'papilot-source'
        checkout.mkdir(parents=True, exist_ok=True)
        subprocess.run([git, 'init', '--quiet', str(checkout)], check=True, env=env)
        exists = subprocess.run([git, '-C', str(checkout), 'cat-file', '-e', f'{COMMIT}^{{commit}}'],
                                capture_output=True, env=env).returncode == 0
        if not exists:
            subprocess.run([git, '-C', str(checkout), 'fetch', '--depth', '1', UPSTREAM, COMMIT], check=True, env=env)
    archive = scratch / 'source.zip'
    subprocess.run([git, '-C', str(checkout), 'archive', '--format=zip', '-o', str(archive), COMMIT], check=True, env=env)
    with zipfile.ZipFile(archive) as package:
        package.extractall(source)


def source_replacements(source: Path):
    def replace(relative: str, old: str, new: str) -> None:
        file = source / relative
        content = file.read_text(encoding='utf-8')
        if content.count(old) != 1:
            raise ValueError(f'The pinned Papilot integration changed: {relative}')
        file.write_text(content.replace(old, new), encoding='utf-8', newline='\n')

    return replace


def configure_wsl_bridge(source: Path) -> None:
    replace = source_replacements(source)
    for file in ('desktop/wsl-runtime.mjs', 'desktop/environment-check.ts'):
        replace(file, '.local/share/papilot-desktop', '.local/share/utopia-phycyclus/papilot')
    replace('desktop/wsl.ts', 'nodeMajor: string; port: number; log: string',
            'nodeMajor: string; port: number; log: string; profile?: string; sharing?: { origin: string; owner: string }')
    replace('desktop/wsl.ts', 'probe: Boolean(process.env.PAPILOT_UPDATE_PROBE), data: process.env.PAPILOT_WSL_DATA',
            'profile: options.profile, sharing: options.sharing')
    replace('desktop/wsl-runtime.mjs', "const root = path.join(target, 'server'), data = options.data || path.join(base, 'data');",
            "const profile = options.profile || 'default';\n"
            "  if (!/^(?:default|[0-9a-f]{24})$/.test(profile)) throw new Error('Invalid Utopia profile ID.');\n"
            "  const root = path.join(target, 'server'), data = path.join(base, 'profiles', profile);")
    replace('desktop/wsl-runtime.mjs', "env.NODE_USE_ENV_PROXY = '1';",
            "env.NODE_USE_ENV_PROXY = '1';\n    env.PAPILOT_DESKTOP_PACKAGE_UPDATES = '1';\n"
            "    if (options.sharing) {\n      env.PAPILOT_PUBLIC_ORIGIN = options.sharing.origin;\n"
            "      env.PAPILOT_TAILSCALE_LOGIN = options.sharing.owner;\n    }")


def configure_workspace(source: Path) -> None:
    replace = source_replacements(source)
    chrome = 'src/web/desktop-chrome.tsx'
    replace(chrome, '      onOpenFolder?(listener: (folder: string) => void): () => void;',
            '      workspaceName?: string;\n'
            '      onOpenFolder?(listener: (folder: string, openManuscript?: boolean) => void | Promise<void>): () => void;')
    main = 'src/web/main.tsx'
    replace(main, "import '../../engine/src/web/legacy-storage.ts';",
            "import '../../engine/src/web/legacy-storage.ts';\n"
            "const workspaceName = window.papilotDesktop?.workspaceName || 'Papilot';")
    replace(main, 'async function openFolder(folder?: string, main?: string)',
            'async function openFolder(folder?: string, main?: string, openManuscript = false)')
    replace(main, '      const path = folder ?? await chooseDirectory();',
            '      if (openManuscript && manuscriptNavigationGuard.current && !await manuscriptNavigationGuard.current.save()) return;\n'
            '      const path = folder ?? await chooseDirectory();')
    replace(main, "      setMainChoice(null); setProjectId(opened.project.id); setSelected(''); setSidebar(false);",
            "      setMainChoice(null); setProjectId(opened.project.id); setSelected(''); setSidebar(false);\n"
            "      if (openManuscript) {\n"
            "        setPage('manuscript'); setManuscriptView(mobile ? 'pdf' : 'task');\n"
            "        setRegion(false); setActiveNote(''); setReturnedPlace('');\n"
            "      }")
    replace(main, '  useEffect(() => window.papilotDesktop?.onOpenFolder?.(folder => void openFolderRef.current(folder)), []);',
            '  const desktopFolderReady = Boolean(state);\n'
            '  useEffect(() => {\n'
            '    if (!desktopFolderReady) return;\n'
            '    return window.papilotDesktop?.onOpenFolder?.((folder, openManuscript) => openFolderRef.current(folder, undefined, openManuscript));\n'
            '  }, [desktopFolderReady]);')
    title_line = next(line for line in (source / main).read_text(encoding='utf-8').splitlines()
                      if 'useEffect(() => { document.title = isPlayground' in line)
    replace(main, title_line, title_line.replace(' · Papilot`', ' · ${workspaceName}`').replace(": 'Papilot');", ': workspaceName);'))
    replace(main, 'className="brand-home" aria-label="Papilot"', 'className="brand-home" aria-label={workspaceName}')
    replace(main, 'className="brand-mark"/><span>Papilot</span>', 'className="brand-mark"/><span>{workspaceName}</span>')
    replace(main, "gitOpen ? t('Version Management') : 'Papilot'", "gitOpen ? t('Version Management') : workspaceName")
    versions = 'src/web/update-notice.tsx'
    replace(versions, '  const currentVersion = <span className="brand-version" title={t(\'Papilot {version}\', { version })}>',
            '  const workspaceName = window.papilotDesktop?.workspaceName;\n'
            '  const versionTitle = workspaceName ? `${workspaceName} v${versionLabel(version)}` : t(\'Papilot {version}\', { version });\n'
            '  const currentVersion = <span className="brand-version" title={versionTitle}>')
    replace(versions, "const description = `${t('Papilot {version}', { version: versionLabel(version) })}",
            "const description = `${workspaceName ? versionTitle : t('Papilot {version}', { version: versionLabel(version) })}")
    replace(versions, '<h3>Papilot</h3>', "<h3>{window.papilotDesktop?.workspaceName || 'Papilot'}</h3>")


def stage_papilot(destination: Path, checkout: Path | None = None) -> None:
    destination = destination.resolve()
    marker = destination / 'papilot-bundle.json'
    try:
        installed = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        installed = {}
    required = ('dist/index.html', 'engine/dist/web/index.html', 'src/server/start.ts',
                'desktop/overleaf-sign-in.js', 'wsl/server.tgz', 'node_modules/tsx/dist/loader.mjs',
                'runtime/node_modules/npm/bin/npm-cli.js', 'LICENSE')
    recipe = sha256(Path(__file__).read_bytes()).hexdigest()
    if installed.get('commit') == COMMIT and installed.get('recipe') == recipe and all((destination / item).is_file() for item in required):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    node, npm, node_version = node_tools()
    env = {**os.environ, 'CI': '1', 'NODE_OPTIONS': '', 'NODE_PATH': '', 'NPM_CONFIG_UPDATE_NOTIFIER': 'false'}

    def npm_run(folder: Path, *args: str, capture=False):
        return subprocess.run([node, str(npm), *args], cwd=folder, env=env, check=True,
                              capture_output=capture, text=True, encoding='utf-8')

    with tempfile.TemporaryDirectory(prefix='utopia-papilot-build-', dir=destination.parent, ignore_cleanup_errors=True) as folder:
        scratch = Path(folder)
        source = scratch / 'source'
        export_source(source, checkout, scratch)
        configure_wsl_bridge(source)
        configure_workspace(source)
        package = json.loads((source / 'package.json').read_text(encoding='utf-8'))
        if package['name'] != 'papilot' or package['license'] != 'MIT':
            raise RuntimeError('The pinned source does not contain the expected Papilot MIT package.')
        print(f'Building Papilot {package["version"]} ({COMMIT[:8]})', flush=True)
        npm_run(source, *INSTALL)
        npm_run(source / 'engine', *INSTALL)
        npm_run(source, 'run', 'build')
        npm_run(source / 'engine', 'run', 'build')
        npm_run(source, 'run', 'desktop:compile')

        packed = npm_run(source, 'pack', '--ignore-scripts', '--json', '--pack-destination', str(scratch), capture=True)
        filename = Path(json.loads(packed.stdout)[0]['filename'])
        if filename != Path(filename.name):
            raise ValueError('Unexpected Papilot npm archive filename.')
        with tarfile.open(scratch / filename, 'r:gz') as archive:
            archive.extractall(scratch, filter='data')
        result = scratch / 'package'
        shutil.copy2(source / 'package-lock.json', result / 'package-lock.json')

        # WSL must install Linux dependencies in its own distribution. Archive
        # the same locked server and both viewers before Windows installs modules.
        wsl = scratch / 'wsl'
        wsl.mkdir()
        with tarfile.open(wsl / 'server.tgz', 'w:gz', compresslevel=5) as archive:
            for entry in sorted(result.iterdir()):
                archive.add(entry, arcname=entry.name)
        (wsl / 'manifest.json').write_text(json.dumps({'nodeMajor': node_version.split('.')[0]}) + '\n', encoding='utf-8')
        (wsl / 'package.json').write_text('{"type":"module"}\n', encoding='utf-8')
        helper_root = source / '.desktop/app'
        for name in WSL_HELPERS:
            shutil.copy2(helper_root / name, wsl / name)
        wsl.replace(result / 'wsl')
        desktop = result / 'desktop'
        desktop.mkdir()
        for name in DESKTOP_HELPERS:
            shutil.copy2(helper_root / name, desktop / name)

        npm_run(result, *INSTALL, '--omit=dev')
        npm_run(result / 'engine', *INSTALL, '--omit=dev')
        # Use upstream's lockfile-aware deduplication. The engine resolves an
        # identical dependency from the server without growing the installer.
        script = "import { dedupeEngine } from " + json.dumps((source / 'scripts/desktop-dedupe.mjs').as_uri()) + "; dedupeEngine(process.argv[1]);"
        subprocess.run([node, '--input-type=module', '-e', script, str(result)], env=env, check=True)

        npm_folder = result / 'runtime/node_modules/npm'
        shutil.copytree(npm.parent.parent, npm_folder)
        bin_folder = result / 'runtime/bin'
        bin_folder.mkdir()
        for command in ('npm', 'npx'):
            script_name = 'npm-cli.js' if command == 'npm' else 'npx-cli.js'
            if os.name == 'nt':
                shim = '@echo off\nnode.exe "%~dp0..\\node_modules\\npm\\bin\\' + script_name + '" %*\n'
                (bin_folder / f'{command}.cmd').write_text(shim, encoding='ascii', newline='\r\n')
            else:
                shim = '#!/bin/sh\nexec node "$(dirname "$0")/../node_modules/npm/bin/' + script_name + '" "$@"\n'
                launcher = bin_folder / command
                launcher.write_text(shim, encoding='ascii')
                launcher.chmod(0o755)

        (result / marker.name).write_text(json.dumps({'source': UPSTREAM, 'commit': COMMIT,
           'version': package['version'], 'node': node_version, 'recipe': recipe}, indent=2) + '\n', encoding='utf-8')
        if not all((result / item).is_file() for item in required):
            raise RuntimeError('The staged Papilot bundle is incomplete.')
        previous = scratch / 'previous'
        if destination.exists():
            destination.replace(previous)
        try:
            result.replace(destination)
        except OSError:
            if previous.exists():
                previous.replace(destination)
            raise
    print(f'Papilot workspace bundle: {destination}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, help='Existing Git checkout containing the pinned upstream commit')
    parser.add_argument('--output', type=Path, default=WORK / 'desktop-preview/papilot')
    args = parser.parse_args()
    stage_papilot(args.output, args.checkout)
