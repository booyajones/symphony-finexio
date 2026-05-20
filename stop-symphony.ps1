# Stop all Symphony instances
Get-Process powershell | Where-Object {
    $_.MainWindowTitle -like "*symphony*" -or
    ($_.CommandLine -like "*symphony.py*" 2>$null)
} | ForEach-Object {
    Write-Host "Stopping PID $($_.Id): $($_.MainWindowTitle)"
    Stop-Process -Id $_.Id -Force
}

# Also stop any python processes running symphony
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    try { $_.CommandLine -like "*symphony.py*" } catch { $false }
} | ForEach-Object {
    Write-Host "Stopping python PID $($_.Id)"
    Stop-Process -Id $_.Id -Force
}

Write-Host "Symphony stopped."
