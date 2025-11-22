# Build et packaging (Windows EXE + Installateur)

Prérequis (local)
- Python 3.10+
- PyInstaller
- Inno Setup (Inno Setup 6)
- (Optionnel) cryptography si postinstall génère des clés

Structure du dépôt
- src/ : code source (hankstoremanager.py, config.py, postinstall_writer.py)
- install/ : installer.iss, install_values.template
- assets/ : icônes et logos
- .github/workflows/build-windows.yml : CI build et packaging

Flux de production recommandé
1. Stocker la clé Fernet de production dans GitHub Secrets: PROD_FERNET_KEY
2. Le workflow CI crée `install/install_values.txt` à partir du secret
3. CI construit dist/*.exe (PyInstaller) et copie dans install/build_artifacts
4. CI compile installer Inno qui inclut install/install_values.txt en {tmp}
5. À l'installation le postinstall_writer lit install_values.txt et injecte FACTURATION_OBR_FERNET_KEY dans %APPDATA%\hankstoremanager\.env

Build local (DEV)
1. Installer dépendances:
2. Générer postinstall_writer.exe et l’appli:
3. Préparer install/install_values.txt pour dev (NE PAS COMMITTER):
4. Copier exe dans install/build_artifacts puis compiler Inno Setup avec ISCC.

Tests
- Installer sur VM propre, vérifier %APPDATA%\hankstoremanager\.env contient FACTURATION_OBR_FERNET_KEY valide.
