; FloatingWordBall per-user installer (NSIS 3.x)
!include "MUI2.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "1.0.0"
!endif
!ifndef SOURCE_DIR
  !define SOURCE_DIR "..\dist\FloatingWordBall"
!endif
!ifndef OUTPUT_FILE
  !define OUTPUT_FILE "FloatingWordBall-Setup-${APP_VERSION}.exe"
!endif
!ifndef ICON_FILE
  !define ICON_FILE "..\floatingwordball.ico"
!endif

!define APP_NAME "FloatingWordBall"
!define APP_PUBLISHER "FloatingWordBall"
!define APP_EXE "FloatingWordBall.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\FloatingWordBall"

Name "${APP_NAME}"
BrandingText "${APP_NAME}"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\FloatingWordBall"
InstallDirRegKey HKCU "Software\FloatingWordBall" "InstallDir"
RequestExecutionLevel user
Unicode true
SetCompressor /SOLID zlib
Icon "${ICON_FILE}"
UninstallIcon "${ICON_FILE}"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=2052 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (c) 2026 FloatingWordBall contributors"

!define MUI_ABORTWARNING
!define MUI_ICON "${ICON_FILE}"
!define MUI_UNICON "${ICON_FILE}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Section "FloatingWordBall" SecMain
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\FloatingWordBall"
  CreateShortCut "$SMPROGRAMS\FloatingWordBall\FloatingWordBall.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortCut "$SMPROGRAMS\FloatingWordBall\卸载 FloatingWordBall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\FloatingWordBall.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

  WriteRegStr HKCU "Software\FloatingWordBall" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\FloatingWordBall.lnk"
  Delete "$SMPROGRAMS\FloatingWordBall\FloatingWordBall.lnk"
  Delete "$SMPROGRAMS\FloatingWordBall\卸载 FloatingWordBall.lnk"
  RMDir "$SMPROGRAMS\FloatingWordBall"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\FloatingWordBall"
  RMDir /r "$INSTDIR"
SectionEnd
