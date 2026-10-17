; Windows .fb registration: preserve existing choices and remove only our current command.
; Loaded after FileAssociation.nsh; template definitions become available only
; later, so validate identity/scope when the standard macros expand.
!macroundef APP_ASSOCIATE
!macroundef APP_UNASSOCIATE

!macro FairbeamAssertAssociation EXT FILECLASS
  !if "${BUNDLEID}" != "org.fairbeam.desktop"
    !error "Unexpected bundle identity for Fairbeam file association"
  !endif
  !if "${INSTALLMODE}" != "currentUser"
    !error "Review association ownership before changing installation scope"
  !endif
  !if "${EXT}" != "fb"
    !error "Unexpected Fairbeam file extension"
  !endif
  !if "${FILECLASS}" != "Fairbeam document"
    !error "Unexpected Fairbeam file class"
  !endif
!macroend

; Query presence, not contents. No buffer or string-length interpretation.
; RRF_RT_ANY|RRF_NOEXPAND plus the view used by pinned Tauri SetContext.
; RegGetValueW returns 2 only for missing value; any other result prevents claim.
; Predefined keys are passed as pointer-sized signed constants, as HKEY defines.
!macro FairbeamQueryAssociationValue ROOT SUBKEY
  StrCpy $R0 "0"
  !if "${ARCH}" == "x64"
    System::Call 'advapi32::RegGetValueW(p ${ROOT}, w "${SUBKEY}", w "", i 0x1001ffff, p 0, p 0, p 0) i .R0'
  !else if "${ARCH}" == "arm64"
    System::Call 'advapi32::RegGetValueW(p ${ROOT}, w "${SUBKEY}", w "", i 0x1001ffff, p 0, p 0, p 0) i .R0'
  !else
    System::Call 'advapi32::RegGetValueW(p ${ROOT}, w "${SUBKEY}", w "", i 0x1002ffff, p 0, p 0, p 0) i .R0'
  !endif
!macroend

!macro APP_ASSOCIATE EXT FILECLASS DESCRIPTION ICON COMMANDTEXT COMMAND
  !insertmacro FairbeamAssertAssociation "${EXT}" "${FILECLASS}"
  Push $R0
  ; HKEY_CURRENT_USER=-2147483647, HKEY_CLASSES_ROOT=-2147483648.
  ; Defined empty defaults also count as choices. Never claim on query failure.
  !insertmacro FairbeamQueryAssociationValue -2147483647 "Software\Classes\.${EXT}"
  StrCmp $R0 "2" 0 fb_associate_register_class
  !insertmacro FairbeamQueryAssociationValue -2147483648 ".${EXT}"
  StrCmp $R0 "2" 0 fb_associate_register_class
  ; An existing registration indicates update/reinstall, even if the user
  ; deleted the extension default. Refresh the command without retaking default.
  !insertmacro FairbeamQueryAssociationValue -2147483647 "Software\Classes\${FILECLASS}\shell\open\command"
  StrCmp $R0 "2" 0 fb_associate_register_class
  !insertmacro FairbeamQueryAssociationValue -2147483648 "${FILECLASS}\shell\open\command"
  StrCmp $R0 "2" 0 fb_associate_register_class
  WriteRegStr SHCTX "Software\Classes\.${EXT}" "" "${FILECLASS}"

fb_associate_register_class:
  WriteRegStr SHCTX "Software\Classes\${FILECLASS}" "" `${DESCRIPTION}`
  WriteRegStr SHCTX "Software\Classes\${FILECLASS}\DefaultIcon" "" `${ICON}`
  WriteRegStr SHCTX "Software\Classes\${FILECLASS}\shell" "" "open"
  WriteRegStr SHCTX "Software\Classes\${FILECLASS}\shell\open" "" `${COMMANDTEXT}`
  WriteRegStr SHCTX "Software\Classes\${FILECLASS}\shell\open\command" "" '"$INSTDIR\${MAINBINARYNAME}.exe" "%1"'
  WriteRegStr SHCTX "Software\Classes\.${EXT}\OpenWithProgids" "${FILECLASS}" ""
  !insertmacro UPDATEFILEASSOC
  Pop $R0
!macroend

!macro APP_UNASSOCIATE EXT FILECLASS
  !insertmacro FairbeamAssertAssociation "${EXT}" "${FILECLASS}"
  Push $R0
  ReadRegStr $R0 SHCTX "Software\Classes\${FILECLASS}\shell\open\command" ""
  StrCmp $R0 '"$INSTDIR\${MAINBINARYNAME}.exe" "%1"' 0 fb_unassociate_done
  ; Microsoft recommends leaving extension defaults; an unregistered ProgID
  ; is ignored. Remove only our candidate value and this command-owned class.
  DeleteRegValue SHCTX "Software\Classes\.${EXT}\OpenWithProgids" "${FILECLASS}"
  DeleteRegKey SHCTX "Software\Classes\${FILECLASS}"
  !insertmacro UPDATEFILEASSOC
fb_unassociate_done:
  Pop $R0
!macroend
