# Enregistre une tache planifiee Windows pour lancer les MCP Odoo a chaque connexion.

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "OdooMCP-Autostart"
$AutostartScript = Join-Path $PSScriptRoot "autostart-odoo-mcp.ps1"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tache planifiee '$TaskName' supprimee."
    exit 0
}

if (-not (Test-Path $AutostartScript)) {
    throw "Script introuvable : $AutostartScript"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$AutostartScript`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Demarre les conteneurs Odoo MCP (docker compose) a la connexion Windows." `
    -Force | Out-Null

Write-Host "Tache planifiee '$TaskName' installee (demarrage a la connexion)."
Write-Host "Versions ciblees : variable ODOO_VERSIONS dans .env (ex. 17,18,19)."
Write-Host "Pour desinstaller : .\scripts\install-autostart.ps1 -Uninstall"
