# HankstoreManager

Application de facturation (HankstoreManager) — dépôt préparé pour produire un exécutable Windows et un installateur.

Structure importante
- `hankstoremanager.py` : point d'entrée de l'application
- `postinstall_writer.py` : script post-install qui écrit `.env` dans `%APPDATA%`
- `assets/` : logos, icônes, images
- `install/installer.iss` : script Inno Setup (Finish page custom)
- `hankstoremanager.spec` : fichier PyInstaller pour bundling
- `.github/workflows/build-windows.yml` : CI pour build automatique sur windows-latest

Comment contribuer / construire
- Voir `README-build.md` pour les instructions de build local et CI.

Licence
- Ce projet est sous licence MIT (fichier `LICENSE`).

Remarques de sécurité
- Ne commitez jamais `.env` réel ni secrets dans le dépôt.
- Pour signer numériquement l'installateur, stocke le certificat en tant que GitHub Secret et adapte le workflow CI.
