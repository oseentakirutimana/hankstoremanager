; install\installer.iss
[Setup]
AppName=HankstoreManager
AppVersion=0.01
DefaultDirName={autopf}\HankstoreManager
DefaultGroupName=HankstoreManager
OutputBaseFilename=HankstoreManager_Setup_v0.01
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
DisableDirPage=no
Uninstallable=yes
WizardStyle=modern
SetupIconFile=..\src\assets\app.ico

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Files]
; Exécutables générés par la CI (attendus dans install\build_artifacts)
Source: "build_artifacts\hankstoremanager.exe"; DestDir: "{app}"; Flags: ignoreversion; Check: FileExistsExpand('{srcexe1}')
Source: "build_artifacts\postinstall_writer.exe"; DestDir: "{app}"; Flags: ignoreversion; Check: FileExistsExpand('{srcexe2}')

; Icône d'application pour les raccourcis
Source: "..\src\assets\app.ico"; DestDir: "{app}"; Flags: ignoreversion; Check: FileExists('..\src\assets\app.ico')

; Licence (depuis la racine du repo)
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion; Check: FileExists('..\LICENSE.txt')

; Fichier généré par la CI lu par le postinstall et supprimé après installation
Source: "install_values.txt"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall; Check: FileExists('install_values.txt')

[Icons]
Name: "{group}\HankstoreManager"; Filename: "{app}\hankstoremanager.exe"; IconFilename: "{app}\app.ico"
Name: "{commondesktop}\HankstoreManager"; Filename: "{app}\hankstoremanager.exe"; Tasks: desktopicon; IconFilename: "{app}\app.ico"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourci"; Flags: unchecked

[Run]
; Exécuter le postinstall writer et attendre sa fin. On ne masque pas le résultat, on capture le code de sortie.
Filename: "{app}\postinstall_writer.exe"; Parameters: "/writeenv"; StatusMsg: "Configuration post-installation en cours..."; Flags: waituntilterminated runascurrentuser

[Code]
#define SITE_URL "https://tonsite.example.com"

uses
  SysUtils, Windows, Classes, Dialogs, ShellAPI;

var
  srcexe1, srcexe2: String;

function FileExistsExpand(const RelPath: String): Boolean;
var
  p: String;
begin
  p := ExpandConstant(RelPath);
  Result := FileExists(p);
end;

procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel.Caption := 'Bienvenue sur HankstoreManager v0.01';
  WizardForm.WelcomeSubCaption.Caption := 'Merci d''avoir choisi HankstoreManager. Cliquez Suivant pour continuer.';
  { Préparer variables utilisables par Check: (valeurs résolues au runtime du compilateur) }
  srcexe1 := ExpandConstant('{src}\build_artifacts\hankstoremanager.exe');
  srcexe2 := ExpandConstant('{src}\build_artifacts\postinstall_writer.exe');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Production: la CI crée install\install_values.txt. Aucune écriture de clé ici. }
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  { Vérification runtime supplémentaire avant de commencer l'installation (utile si ISCC est lancé depuis un CI agent) }
  if not FileExists(srcexe1) then
  begin
    MsgBox(Format('Fichier requis introuvable: %s'#13#10'Vérifiez que la CI a copié les artefacts dans install\build_artifacts.', [srcexe1]), mbError, mb_Ok);
    Result := False;
    Exit;
  end;
  if not FileExists(srcexe2) then
  begin
    MsgBox(Format('Fichier requis introuvable: %s'#13#10'Vérifiez que la CI a copié les artefacts dans install\build_artifacts.', [srcexe2]), mbError, mb_Ok);
    Result := False;
    Exit;
  end;
end;

procedure DeinitializeSetup();
begin
  { Rien à faire ici ; placeholder pour extensibilité }
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
begin
  { Après la phase d'installation, Inno Setup retourne ici ; noter que Run(..., waituntilterminated) gère déjà l'attente.
    Si tu veux inspecter le code de sortie du postinstall, tu peux appeler Exec et vérifier le code explicitement ici. }
  if CurStep = ssPostInstall then
  begin
    { Optionnel : exécuter explicitement et vérifier le code de sortie }
    if Exec(ExpandConstant('{app}\postinstall_writer.exe'), '/writeenv', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    begin
      if ExitCode <> 0 then
      begin
        MsgBox(Format('Le post-install a retourné un code d''erreur (%d). Veuillez consulter les logs.', [ExitCode]), mbError, mb_Ok);
      end;
    end
    else
    begin
      MsgBox('Impossible d''exécuter le post-install (postinstall_writer.exe)', mbError, mb_Ok);
    end;
  end;
end;

