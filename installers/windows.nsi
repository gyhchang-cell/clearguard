Unicode True
!include "MUI2.nsh"
!include "x64.nsh"

Name "ClearGuard ${VERSION}"
OutFile "release\ClearGuard-${VERSION}-windows-x64-setup.exe"
InstallDir "$LOCALAPPDATA\Programs\ClearGuard"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "ClearGuard"
VIAddVersionKey "FileDescription" "ClearGuard Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "ClearGuard contributors"
VIAddVersionKey "LegalCopyright" "Copyright (c) ClearGuard contributors"

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\ClearGuard.exe"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "This installer requires 64-bit Windows." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
FunctionEnd

Section
  SetShellVarContext current
  SetRegView 64
  IfFileExists "$INSTDIR\*.*" 0 install
  MessageBox MB_ICONSTOP "The installation directory already contains files. Uninstall the previous copy before installing ClearGuard." /SD IDOK
  SetErrorLevel 3
  Abort

  install:
  SetOutPath "$INSTDIR"
  File /r "dist\ClearGuard\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\ClearGuard.lnk" "$INSTDIR\ClearGuard.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard" "DisplayName" "ClearGuard"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard" "Publisher" "ClearGuard contributors"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard" "DisplayIcon" "$INSTDIR\ClearGuard.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  IfFileExists "$INSTDIR\ClearGuard.exe" 0 remove
  ClearErrors
  Delete "$INSTDIR\ClearGuard.exe"
  IfErrors 0 remove
  MessageBox MB_ICONSTOP "Close ClearGuard before uninstalling it." /SD IDOK
  SetErrorLevel 4
  Abort

  remove:
  !include "uninstall-files.nsh"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\ClearGuard.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearGuard"
SectionEnd
