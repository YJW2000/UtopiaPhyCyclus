"""Check core PE dependencies against the bundle and Windows base-system DLLs."""
from pathlib import Path
import json
import pefile

WINDOWS_DLLS = set(('''advapi32 bcrypt bcryptprimitives comctl32 comdlg32 crypt32
cryptbase cryptsp dbghelp dhcpcsvc dnsapi dsound dwmapi dxgi gdi32 gdi32full imm32
iphlpapi kernel32 kernelbase msimg32 msvcrt mswsock netapi32 ntdll normaliz ncrypt
ntasn1 ole32 oleacc oleaut32 opengl32 pdh powrprof profapi psapi rpcrt4 secur32
setupapi shell32 shlwapi sspicli user32 userenv uxtheme urlmon version winscard
winmm winhttp wininet wintrust wlanapi wldap32 ws2_32 wtsapi32 ucrtbase win32u
rasapi32 sensapi usp10 propsys cabinet avrt dwmapi dwrite d2d1''').split())


def audit(root):
    root = Path(root)
    local = {}
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.dll', '.pyd', '.exe', '.node'}:
            local.setdefault(path.name.lower(), []).append(path)
    pending = [root / 'UtopiaPhyCyclus.exe', root / '_internal/python312.dll',
               root / 'runtimes/node/node.exe', root / 'runtimes/texlive/bin/windows/xelatex.exe',
               root / 'runtimes/whisper/node_modules/@fugood/node-whisper-win32-x64/index.node']
    pending += list((root / '_internal/pymupdf').glob('*.pyd'))
    seen, missing, system = set(), set(), set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        binary = pefile.PE(str(path), fast_load=True)
        binary.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        for entry in getattr(binary, 'DIRECTORY_ENTRY_IMPORT', []):
            name = entry.dll.decode('ascii').lower()
            stem = name.removesuffix('.dll')
            if name.startswith(('api-ms-win-', 'ext-ms-win-')) or stem in WINDOWS_DLLS or name == 'winspool.drv':
                system.add(name)
            elif name in local:
                candidates = local[name]
                match = next((p for p in candidates if pefile.PE(str(p), fast_load=True).FILE_HEADER.Machine == binary.FILE_HEADER.Machine), None)
                if match is None:
                    missing.add(name + ' (architecture mismatch)')
                else:
                    pending.append(match)
            else:
                missing.add(name)
        binary.close()
    report = {'core_pe_files_checked': len(seen), 'windows_base_dlls': sorted(system), 'missing_dlls': sorted(missing)}
    if missing:
        raise RuntimeError('Unbundled native dependencies: ' + ', '.join(sorted(missing)))
    print(json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    import sys
    audit(sys.argv[1])
