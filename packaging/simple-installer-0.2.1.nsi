Unicode True
!include "MUI2.nsh"

!define APP_NAME "LangGraph Agent"
!define APP_VERSION "0.2.1"
!define APP_EXE "LangGraph Agent.exe"
!define SIDECAR_EXE "langgraph-sidecar.exe"
!define SOURCE_DIR "D:\Vibecoding\Langgraph\release-final\win-unpacked"

Name "${APP_NAME}"
OutFile "D:\Vibecoding\Langgraph\release-final\LangGraph-Agent-Setup-0.2.1.exe"
InstallDir "$LOCALAPPDATA\Programs\LangGraph Agent"
InstallDirRegKey HKCU "Software\LangGraph Agent" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "Install"
    ; Close the app and any orphaned sidecar from a previous version.
    nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
    nsExec::Exec 'taskkill /IM "${SIDECAR_EXE}" /F'

    SetOutPath "$INSTDIR"
    File /r /x "data" /x "boot.log" "${SOURCE_DIR}\*.*"

    CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\LangGraph Agent" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoRepair" 1
SectionEnd

Section "Uninstall"
    nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
    nsExec::Exec 'taskkill /IM "${SIDECAR_EXE}" /F'
    Delete "$DESKTOP\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    RMDir "$SMPROGRAMS\${APP_NAME}"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
    DeleteRegKey HKCU "Software\LangGraph Agent"
    RMDir /r "$INSTDIR"
SectionEnd
