# Symphony Global Launcher
# Starts one daemon per active repo, each in its own PowerShell window.
# Run this script to bring up all Symphony instances.

$SYMPHONY_DIR = "C:\Users\chris\Downloads\symphony-finexio"
$ENV_FILE = "C:\Users\chris\OneDrive\Desktop\Claude\.env"

# Load env vars from .env file
if (Test-Path $ENV_FILE) {
    Get-Content $ENV_FILE | ForEach-Object {
        if ($_ -match "^([^#][^=]+)=(.+)$") {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
    Write-Host "Loaded env from $ENV_FILE"
}

# Repos to run Symphony for (add/remove as needed)
$WORKFLOWS = @(
    "workflows\finexio-portal-pro.md",
    "workflows\sfdc-ops-pro.md",
    "workflows\finexio-skills.md"
)

Write-Host ""
Write-Host "Starting Symphony for $($WORKFLOWS.Count) repos..."
Write-Host ""

foreach ($wf in $WORKFLOWS) {
    $wfPath = Join-Path $SYMPHONY_DIR $wf
    if (-not (Test-Path $wfPath)) {
        Write-Warning "Workflow not found: $wfPath"
        continue
    }

    $repoName = [System.IO.Path]::GetFileNameWithoutExtension($wf)
    $logFile = Join-Path $SYMPHONY_DIR "log\$repoName.log"
    New-Item -ItemType Directory -Force (Split-Path $logFile) | Out-Null

    # Launch in a new PowerShell window (minimized)
    $cmd = "python `"$SYMPHONY_DIR\symphony.py`" `"$wfPath`" --logs-root `"$SYMPHONY_DIR\log\$repoName`" 2>&1 | Tee-Object -FilePath `"$logFile`" -Append"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd -WindowStyle Minimized

    Write-Host "  Started: $repoName"
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "All Symphony instances running."
Write-Host "Logs: $SYMPHONY_DIR\log\"
Write-Host ""
Write-Host "To stop all: close the Symphony PowerShell windows, or run stop-symphony.ps1"
