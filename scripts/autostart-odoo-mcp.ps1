# Demarre les conteneurs Odoo MCP configures dans .env (ODOO_VERSIONS).
# Appele au demarrage Windows via la tache planifiee OdooMCP-Autostart.

param(
    [int]$MaxWaitSeconds = 180
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AvailableVersions = @("15", "16", "17", "18", "19")

function Get-DotEnvValue {
    param([string]$Name, [string]$Default)
    $envFile = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path $envFile)) { return $Default }
    foreach ($line in Get-Content $envFile) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)$')) {
            $value = $Matches[1].Trim().Trim('"').Trim("'")
            if ($value) { return $value }
        }
    }
    return $Default
}

function Test-DockerReady {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & docker info 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

function Wait-DockerReady {
    $elapsed = 0
    while ($elapsed -lt $MaxWaitSeconds) {
        if (Test-DockerReady) { return }
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    throw "Docker indisponible apres ${MaxWaitSeconds}s."
}

function Get-VersionsToStart {
    $raw = Get-DotEnvValue "ODOO_VERSIONS" ""
    if ($raw) {
        return @(
            $raw -split '[,\s;]+' |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and ($AvailableVersions -contains $_) }
        )
    }

    # Secours : versions deja deployees au moins une fois.
    $existing = docker ps -a --filter "name=odoo-mcp-v" --format "{{.Names}}" 2>$null
    return @(
        $existing |
        ForEach-Object {
            if ($_ -match 'odoo-mcp-v(\d+)$') { $Matches[1] }
        } |
        Where-Object { $_ -and ($AvailableVersions -contains $_) } |
        Sort-Object -Unique
    )
}

Push-Location $ProjectRoot
try {
    Wait-DockerReady

    $versions = Get-VersionsToStart
    if ($versions.Count -eq 0) {
        Write-Host "Aucune version MCP a demarrer (ODOO_VERSIONS vide et aucun conteneur existant)."
        exit 0
    }

    $composeArgs = @("compose")
    foreach ($ver in $versions) {
        $composeArgs += "--profile", "v$ver"
    }
    $composeArgs += @("up", "-d")

    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Echec du demarrage Docker Compose."
    }

    Write-Host "Conteneurs MCP demarres : $($versions -join ', ')"
}
finally {
    Pop-Location
}
