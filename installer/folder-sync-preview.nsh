; Give the preview a dedicated directory, including when /D is supplied.
; Another application's directory must never be used as the uninstall root.
!macro customInit
  ; NSIS returns an empty string for a directory that does not exist yet.
  ; Keep the installer-selected path on a first install.
  GetFullPathName $R3 "$INSTDIR"
  ${If} $R3 != ""
    StrCpy $INSTDIR $R3
  ${EndIf}
  ReadRegStr $R2 HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
  ${If} $R2 == $INSTDIR
  ${AndIf} ${FileExists} "$INSTDIR\EasyReadFolderSyncPreview.exe"
  ${AndIfNot} ${FileExists} "$INSTDIR\EasyRead.exe"
    Goto preview_install_ready
  ${EndIf}

  ClearErrors
  FindFirst $R0 $R1 "$INSTDIR\*.*"
  IfErrors preview_install_ready
  preview_inspect:
    StrCmp $R1 "" preview_close_ready
    StrCmp $R1 "." preview_next
    StrCmp $R1 ".." preview_next
    FindClose $R0
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP "Install the EasyRead Folder Sync Preview into its own empty directory."
    ${EndIf}
    SetErrorLevel 2
    Quit
  preview_next:
    ClearErrors
    FindNext $R0 $R1
    IfErrors preview_close_ready preview_inspect
  preview_close_ready:
    FindClose $R0
  preview_install_ready:
!macroend
