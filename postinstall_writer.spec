# postinstall_writer.spec
import os
from PyInstaller.utils.hooks import copy_metadata

block_cipher = None

BASE = os.path.abspath(os.path.dirname(__file__))

datas = []
# Inclure .env.example (vérifier les deux emplacements possibles)
if os.path.exists(os.path.join(BASE, 'src', '.env.example')):
    datas.append((os.path.join(BASE, 'src', '.env.example'), '.'))
elif os.path.exists(os.path.join(BASE, '.env.example')):
    datas.append((os.path.join(BASE, '.env.example'), '.'))

# Optionnel template
if os.path.exists(os.path.join(BASE, 'install', 'install_values.template')):
    datas.append((os.path.join(BASE, 'install', 'install_values.template'), 'install'))

# copy metadata if required by cryptography used by postinstall
try:
    datas += copy_metadata('cryptography')
except Exception:
    pass

a = Analysis(
    ['src/postinstall_writer.py'],
    pathex=[os.path.abspath('src'), os.path.abspath('.')],
    binaries=[],
    datas=datas,
    hiddenimports=['cryptography.fernet'],
    hookspath=[],
    runtime_hooks=[],
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
    name='postinstall_writer',
    debug=False,
    strip=False,
    upx=False,
    console=False,   # mettre True pour debug si nécessaire
)
