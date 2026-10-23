"""Fetch exact-version public source links and license texts before preparing a release."""
import importlib.metadata as metadata
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'UtopiaPhyCyclus-release'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def main():
    import fitz
    mupdf_version = fitz.VersionFitz
    licenses = ROOT / 'licenses'
    licenses.mkdir(exist_ok=True)
    pymupdf_version = metadata.version('pymupdf')
    (ROOT / 'COPYING-AGPL-3.0').write_bytes(get(f'https://raw.githubusercontent.com/PyMuPDF/PyMuPDF/{pymupdf_version}/COPYING'))
    # Some upstream COPYING files are just a dual-license notice. Include the full text too.
    full = get(f'https://raw.githubusercontent.com/ArtifexSoftware/mupdf/{mupdf_version}/COPYING')
    if len(full) < 20000:
        full = get('https://www.gnu.org/licenses/agpl-3.0.txt')
    (ROOT / 'COPYING-AGPL-3.0').write_bytes(full)
    distributions = set()
    queue = ['streamlit', 'requests', 'python-dotenv', 'Pillow', 'pymupdf']
    from packaging.requirements import Requirement
    while queue:
        name = queue.pop()
        distribution = metadata.distribution(name)
        name = distribution.metadata['Name']
        if name in distributions:
            continue
        distributions.add(name)
        for requirement in distribution.requires or []:
            req = Requirement(requirement)
            if req.marker is None or req.marker.evaluate({'extra': ''}):
                queue.append(req.name)
    sources = []
    for name in sorted(distributions, key=str.lower):
        distribution = metadata.distribution(name)
        version = distribution.version
        data = json.loads(get(f'https://pypi.org/pypi/{name}/{version}/json'))
        sdists = [{'url': f['url'], 'sha256': f['digests']['sha256']} for f in data['urls'] if f['packagetype'] == 'sdist']
        sources.append({'name': name, 'version': version, 'source_archives': sdists,
                        'project_urls': data['info'].get('project_urls', {})})
        for file in distribution.files or []:
            path = str(file)
            if any(word in Path(path).name.lower() for word in ['license', 'copying', 'notice']):
                original = distribution.locate_file(file)
                if original.is_file() and original.suffix.lower() not in {'.py', '.pyc', '.pyd', '.dll'}:
                    target = licenses / f'{name[:18]}-{hashlib.sha256(str(file).encode()).hexdigest()[:10]}.txt'
                    target.write_bytes(original.read_bytes())
        print(f'Source and license recorded: {name} {version}', flush=True)
    sources += [
        {'name': 'MuPDF', 'version': mupdf_version, 'source_archives': [{'url': f'https://mupdf.com/downloads/archive/mupdf-{mupdf_version}-source.tar.gz'}], 'build_instructions': 'https://pymupdf.readthedocs.io/en/latest/installation.html'},
        {'name': 'Apache ECharts', 'version': '5.5.0', 'source_archives': [{'url': 'https://github.com/apache/echarts/archive/refs/tags/5.5.0.tar.gz'}]},
        {'name': 'CPython', 'version': '.'.join(map(str, sys.version_info[:3])), 'source_archives': [{'url': f'https://www.python.org/ftp/python/{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}/Python-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.tgz'}]},
    ]
    python_license = Path(sys.base_prefix) / 'LICENSE_PYTHON.txt'
    if python_license.exists():
        (licenses / 'CPython-LICENSE.txt').write_bytes(python_license.read_bytes())
    (licenses / 'Apache-2.0.txt').write_bytes(get('https://raw.githubusercontent.com/apache/echarts/5.5.0/LICENSE'))
    (ROOT / 'dependency-sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'requirements-lock.txt').write_text(
        '# Exact runtime dependency snapshot\n' + '\n'.join(
            f"{s['name']}=={s['version']}" for s in sources
            if s['name'] not in ['MuPDF', 'Apache ECharts', 'CPython']) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
