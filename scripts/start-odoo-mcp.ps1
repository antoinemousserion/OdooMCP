param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("15", "16", "17", "18", "19")]
    [string]$Version,

    [switch]$Stop,
    [switch]$Logs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Profile = "v$Version"

function Get-DotEnvValue {
    param([string]$Name, [string]$Default)
    $envFile = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path $envFile)) { return $Default }
    foreach ($line in Get-Content $envFile) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)$") {
            $value = $Matches[1].Trim().Trim('"').Trim("'")
            if ($value) { return $value }
        }
    }
    return $Default
}

Push-Location $ProjectRoot
try {
    if ($Stop) {
        docker compose --profile $Profile down
        Write-Host "Conteneur odoo-mcp-v$Version arrêté."
    }
    elseif ($Logs) {
        docker compose logs -f "odoo-mcp-v$Version"
    }
    else {
        if (-not (Test-Path ".env")) {
            Write-Host "Création de .env depuis .env.example — renseignez GITHUB_TOKEN pour enterprise."
            Copy-Item ".env.example" ".env"
        }
        $VolumePath = Get-DotEnvValue "ODOO_VOLUME_PATH" "C:\Users\antoi\Documents\MCP Odoo"
        if (-not (Test-Path $VolumePath)) {
            New-Item -ItemType Directory -Path $VolumePath -Force | Out-Null
            Write-Host "Dossier volume créé : $VolumePath"
        }
        docker compose --profile $Profile up -d --build
        Write-Host ""
        Write-Host "Odoo MCP v$Version démarré."
        Write-Host "  URL MCP : http://localhost:80$Version/sse"
        Write-Host "  Ajoutez dans Cursor (Settings > MCP) :"
        Write-Host '  { "url": "http://localhost:80' + $Version + '/sse" }'
        Write-Host ""
        Write-Host "Premier démarrage : clone Git en cours (plusieurs minutes). Suivez avec :"
        Write-Host "  .\scripts\start-odoo-mcp.ps1 -Version $Version -Logs"
    }
}
finally {
    Pop-Location
}
