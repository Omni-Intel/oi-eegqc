Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = scriptDir & "\start-local-inbox.ps1"
cmd = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & ps1 & """"
code = sh.Run(cmd, 0, True)
If code = 0 Then
  MsgBox "收件服务已在后台运行。" & vbCrLf & vbCrLf & "地址：http://172.16.1.249:8443" & vbCrLf & "文件写入：D:\EEG_Data\inbox\", 64, "OI-EEGQC 收件"
Else
  MsgBox "收件服务启动失败。请查看 D:\EEG_Data\inbox\_state\receiver.err.log", 16, "OI-EEGQC 收件"
End If
