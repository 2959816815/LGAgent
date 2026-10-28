!macro customCheckAppRunning
  ; electron-builder 26.x 在部分机器上会把安装器自身误判为运行中的应用。
  ; 升级时由用户手动关闭旧窗口，避免安装被错误阻断。
!macroend

!macro customInstall
  CreateDirectory "$PROFILE\Desktop"
  CreateShortCut "$PROFILE\Desktop\LangGraph Agent.lnk" "$INSTDIR\LangGraph Agent.exe"
  CreateDirectory "$APPDATA\Microsoft\Windows\Start Menu\Programs"
  CreateShortCut "$APPDATA\Microsoft\Windows\Start Menu\Programs\LangGraph Agent.lnk" "$INSTDIR\LangGraph Agent.exe"
!macroend

!macro customUnInstall
  Delete "$PROFILE\Desktop\LangGraph Agent.lnk"
  Delete "$APPDATA\Microsoft\Windows\Start Menu\Programs\LangGraph Agent.lnk"
!macroend
