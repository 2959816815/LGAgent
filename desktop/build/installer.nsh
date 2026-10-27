!macro customInstall
  CreateDirectory "$PROFILE\Desktop"
  CreateShortCut "$PROFILE\Desktop\LangGraph Agent.lnk" "$INSTDIR\LangGraph Agent.exe" "--no-sandbox"
  CreateDirectory "$APPDATA\Microsoft\Windows\Start Menu\Programs"
  CreateShortCut "$APPDATA\Microsoft\Windows\Start Menu\Programs\LangGraph Agent.lnk" "$INSTDIR\LangGraph Agent.exe" "--no-sandbox"
!macroend

!macro customUnInstall
  Delete "$PROFILE\Desktop\LangGraph Agent.lnk"
  Delete "$APPDATA\Microsoft\Windows\Start Menu\Programs\LangGraph Agent.lnk"
!macroend
