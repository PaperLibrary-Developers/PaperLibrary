$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$sc = $ws.CreateShortcut("$desktop\\PaperLibrary.lnk")
$sc.TargetPath = 'y:\私人\PAPER\启动文献检索系统.bat'
$sc.WorkingDirectory = 'y:\私人\PAPER'
$sc.Description = 'PaperLibrary - Academic Literature Management'
$sc.Save()
Write-Host "Shortcut created: $desktop\\PaperLibrary.lnk"
