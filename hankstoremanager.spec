# hankstoremanager.spec
# Usage: pyinstaller --clean hankstoremanager.spec
# Entrée principale : src/hankstoremanager.py
# Objectif :
# - inclure ressources sous src/
# - rassembler DLL natifs utiles (SQLCipher, OpenSSL) en cherchant par heuristiques
# - inclure Tcl/Tk pour tkinter (Windows)
# - ajouter runtime hook pour charger .env si présent
# - réduire les fichiers inutiles embarqués

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs, copy_metadata
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

block_cipher = None

BASE = os.path.abspath(os.path.dirname(__file__))
SRC_DIR = os.path.join(BASE, "src")

def collect_folder(src_folder, target_base=None):
    """
    Recursively collect files under src_folder, skipping common unwanted files.
    Returns list of (src_path, dest_dir) tuples suitable for datas.
    """
    pairs = []
    if not os.path.exists(src_folder):
        return pairs
    if target_base is None:
        target_base = os.path.basename(src_folder)
    for root, _, files in os.walk(src_folder):
        rel_dir = os.path.relpath(root, src_folder)
        dest_dir = os.path.join(target_base, rel_dir) if rel_dir != '.' else target_base
        for f in files:
            # Skip bytecode, VCS, hidden and runtime build artifacts
            if f.endswith(('.pyc', '.pyo')) or f.startswith('.') or f.endswith(('.log', '.cache')):
                continue
            # Skip virtualenv / large artifacts if present accidentally
            if f.lower().endswith(('.exe', '.dll')) and 'dist' in root:
                continue
            pairs.append((os.path.join(root, f), dest_dir))
    return pairs

# --- datas: ressources du projet ---
datas = []
folders_to_include = [
    'src/assets',
    'src/static',
    'src/templates',
    'src/api',
    'src/controllers',
    'src/database',
    'src/gui',
    'src/models',
    'src/utils',
    'src/views',
]
for fd in folders_to_include:
    datas += collect_folder(os.path.join(BASE, fd), target_base=os.path.basename(fd))

# Include .env.example if present
if os.path.exists(os.path.join(BASE, '.env.example')):
    datas.append((os.path.join(BASE, '.env.example'), '.'))
elif os.path.exists(os.path.join(SRC_DIR, '.env.example')):
    datas.append((os.path.join(SRC_DIR, '.env.example'), '.'))

# --- hidden imports ---
hidden_imports = [
    'pkg_resources',
    'importlib.resources',
    'requests',
    'django',
    'weasyprint',
    'matplotlib',
    # Force inclusion of tkinter and ttk
    'tkinter',
    'tkinter.ttk',
]
# cryptography submodules can be many; collect them to avoid missing imports
try:
    hidden_imports += collect_submodules('cryptography')
except Exception:
    pass

# --- binaries: dynamic libs (SQLCipher, OpenSSL, Tcl/Tk) ---
binaries = []

# Try to collect dynamic libs from known python packages that might expose them
for pkg in ('pysqlcipher3', 'sqlcipher3', 'pysqlcipher'):
    try:
        dyn = collect_dynamic_libs(pkg)
        if dyn:
            binaries += dyn
    except Exception:
        pass

# Heuristics: common MSYS2 / MINGW paths on Windows runners or developer machines
msys_candidates = [
    r"C:\msys64\mingw64\bin\libsqlcipher-1.dll",
    r"C:\msys64\mingw64\bin\libsqlcipher.dll",
    r"C:\msys64\mingw64\bin\libcrypto-1_1-x64.dll",
    r"C:\msys64\mingw64\bin\libssl-1_1-x64.dll",
    r"C:\msys64\mingw64\bin\libcrypto-3.dll",
    r"C:\msys64\mingw64\bin\libssl-3.dll",
]
for p in msys_candidates:
    if os.path.exists(p):
        binaries.append((p, '.'))

# Allow CI / user to explicitly specify a DLL path (SQLCIPHER_DLL_PATH)
sqlcipher_env = os.environ.get('SQLCIPHER_DLL_PATH')
if sqlcipher_env:
    sqlcipher_env = os.path.abspath(sqlcipher_env)
    if os.path.exists(sqlcipher_env):
        binaries.append((sqlcipher_env, '.'))
    else:
        print(f"WARNING: SQLCIPHER_DLL_PATH set but file not found: {sqlcipher_env}")

# Try to find sqlcipher / libsqlcipher in common conda locations
conda_candidate = os.path.join(sys.prefix, "Library", "bin", "libsqlcipher-1.dll") if sys.platform == "win32" else os.path.join(sys.prefix, "lib", "libsqlcipher.so")
if os.path.exists(conda_candidate):
    binaries.append((conda_candidate, '.'))

# --- Tcl/Tk detection and inclusion (Windows) ---
tcl_tk_binaries = []
tcl_tk_datas = []

def _detect_tcl_tk_for_python(python_prefix: str):
    pfx = Path(python_prefix)
    dlls_dir = pfx / "DLLs"
    tcl_dir = pfx / "tcl"
    # common DLL candidates for various Python builds
    dll_candidates = [
        "tcl86t.dll", "tk86t.dll",
        "tcl8.6.dll", "tk8.6.dll",
        "tcl87.dll", "tk87.dll",
    ]
    for name in dll_candidates:
        p = dlls_dir / name
        if p.exists():
            tcl_tk_binaries.append((str(p), '.'))
    # include common specific tcl subfolders rather than everything
    for sub in ("tcl8.6", "tk8.6", "tcl", "tk"):
        sd = tcl_dir / sub
        if sd.exists():
            # collect only .tcl and script files and DLLs under this subfolder
            for root, _, files in os.walk(sd):
                rel = os.path.relpath(root, str(tcl_dir))
                dest_base = os.path.join("tcl", rel)
                for f in files:
                    if f.endswith(('.tcl', '.tk', '.txt', '.cfg', '.ini', '.dll')):
                        tcl_tk_datas.append((os.path.join(root, f), os.path.join("tcl", rel)))

# Try detection on base_prefix and sys.prefix
try:
    _detect_tcl_tk_for_python(getattr(sys, "base_prefix", sys.prefix))
    _detect_tcl_tk_for_python(sys.prefix)
except Exception:
    pass

# Also allow explicit environment settings for Tcl/Tk
tcl_dll_env = os.environ.get("TCL_DLL_PATH")
if tcl_dll_env and os.path.exists(tcl_dll_env):
    tcl_tk_binaries.append((tcl_dll_env, '.'))
tcl_folder_env = os.environ.get("TCL_FOLDER_PATH")
if tcl_folder_env and os.path.exists(tcl_folder_env):
    # collect files from the provided folder
    for root, _, files in os.walk(tcl_folder_env):
        rel = os.path.relpath(root, tcl_folder_env)
        for f in files:
            if f.endswith(('.tcl', '.tk', '.dll', '.txt')):
                tcl_tk_datas.append((os.path.join(root, f), os.path.join("tcl", rel)))

# Merge Tcl/Tk into binaries/datas
binaries += tcl_tk_binaries
datas += tcl_tk_datas

# Optional: copy metadata for packages that need it at runtime
for pkg in ('requests', 'cryptography', 'weasyprint', 'matplotlib'):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# If no SQLCipher-like binary found, warn build user (CI should set SQLCIPHER_DLL_PATH or MSYS2)
if not any('sqlcipher' in os.path.basename(b[0]).lower() for b in binaries):
    print("WARNING: no SQLCipher dynamic library was bundled by the spec; "
          "set SQLCIPHER_DLL_PATH env or ensure MSYS2/conda packages provide libsqlcipher")

# --- runtime hooks: charger .env tôt si le hook est présent sous src/runtime_hooks/ ---
runtime_hooks = []
rh = os.path.join(BASE, 'src', 'runtime_hooks', 'load_dotenv.py')
if os.path.exists(rh):
    runtime_hooks.append(rh)

# --- optional custom hookspath for project-specific pyinstaller hooks ---
hookspath = []
custom_hook_dir = os.path.join(BASE, 'src', 'pyinstaller_hooks')
if os.path.isdir(custom_hook_dir):
    hookspath.append(custom_hook_dir)

# --- Analysis ---
a = Analysis(
    ['src/hankstoremanager.py'],
    pathex=[os.path.abspath('src'), os.path.abspath('.')],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(set(hidden_imports)),
    hookspath=hookspath,
    runtime_hooks=runtime_hooks,
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='hankstoremanager',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join('src', 'assets', 'app.ico') if os.path.exists(os.path.join('src', 'assets', 'app.ico')) else None,
)

# Use COLLECT to produce the final folder; this ensures binaries/datas are placed correctly
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='hankstoremanager',
)
