param(
    [switch]$Logs,
    [switch]$Stop,
    [string]$Version
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
# "live" = MCP d'exploration de donnees d'une instance (service odoo-mcp-live, port 8900).
$AvailableVersions = @("15", "16", "17", "18", "19", "live")

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

function Ensure-EnvAndVolume {
    if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
        Write-Host ""
        Write-Host "Creation de .env depuis .env.example" -ForegroundColor Yellow
        Write-Host "Pensez a renseigner GITHUB_TOKEN pour le clone enterprise."
        Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $ProjectRoot ".env")
    }
    $script:VolumePath = Get-DotEnvValue "ODOO_VOLUME_PATH" "C:\Users\antoi\Documents\MCP Odoo"
    if (-not (Test-Path $script:VolumePath)) {
        New-Item -ItemType Directory -Path $script:VolumePath -Force | Out-Null
        Write-Host "Dossier volume cree : $script:VolumePath"
    }
}

function Get-ServiceName {
    param([string]$Ver)
    if ($Ver -eq "live") { return "odoo-mcp-live" }
    return "odoo-mcp-v$Ver"
}

function Get-ProfileName {
    param([string]$Ver)
    if ($Ver -eq "live") { return "live" }
    return "v$Ver"
}

function Get-VersionLabel {
    param([string]$Ver)
    if ($Ver -eq "live") { return "Odoo live (donnees d'une instance)" }
    return "Odoo $Ver"
}

function Ensure-LiveConfig {
    $dir = Get-DotEnvValue "ODOO_MCP_CONFIG_DIR" (Join-Path $env:USERPROFILE ".odoo-mcp")
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $file = Join-Path $dir "instances.json"
    if (-not (Test-Path $file)) {
        Set-Content -Path $file -Value "{}" -Encoding ascii
        Write-Host "Fichier de profils cree : $file (modele : instances.example.json)" -ForegroundColor Yellow
    }
    return $file
}

function Test-VersionHasData {
    param([string]$Ver)
    $base = Join-Path $VolumePath "v$Ver"
    return (Test-Path (Join-Path $base "community")) -or (Test-Path (Join-Path $base "enterprise"))
}

function Test-VersionContainerExists {
    param([string]$Ver)
    $name = Get-ServiceName $Ver
    $found = docker compose ps -a --format "{{.Service}}" 2>$null
    return ($found -contains $name)
}

function Remove-VersionClone {
    param([string]$Ver)
    $base = Join-Path $VolumePath "v$Ver"
    foreach ($folder in @("community", "enterprise")) {
        $path = Join-Path $base $folder
        if (Test-Path $path) {
            Write-Host "  Suppression de $path"
            Remove-Item -Recurse -Force $path
        }
    }
}

function Select-VersionsInteractive {
    $items = foreach ($ver in $AvailableVersions) {
        [PSCustomObject]@{ Version = $ver; Selected = $true }
    }
    $cursor = 0
    $confirmed = $false

    [Console]::CursorVisible = $false
    try {
        do {
            Clear-Host
            Write-Host ""
            Write-Host "Quelles versions Odoo MCP demarrer ?" -ForegroundColor Cyan
            Write-Host ""

            for ($i = 0; $i -lt $items.Count; $i++) {
                $check = if ($items[$i].Selected) { "[*]" } else { "[ ]" }
                $label = "  $check $(Get-VersionLabel $items[$i].Version)"
                if ($i -eq $cursor) {
                    Write-Host $label -ForegroundColor Black -BackgroundColor Cyan
                }
                else {
                    Write-Host $label
                }
            }

            Write-Host ""
            Write-Host "  Haut/Bas : naviguer  |  Espace : cocher/decoche  |  Entree : valider  |  Echap : annuler" -ForegroundColor DarkGray

            $key = [Console]::ReadKey($true)
            switch ($key.Key) {
                ([ConsoleKey]::UpArrow) {
                    if ($cursor -gt 0) { $cursor-- }
                }
                ([ConsoleKey]::DownArrow) {
                    if ($cursor -lt ($items.Count - 1)) { $cursor++ }
                }
                ([ConsoleKey]::Spacebar) {
                    $items[$cursor].Selected = -not $items[$cursor].Selected
                }
                ([ConsoleKey]::Enter) {
                    $confirmed = $true
                }
                ([ConsoleKey]::Escape) {
                    Clear-Host
                    exit 0
                }
            }
        } until ($confirmed)
    }
    finally {
        [Console]::CursorVisible = $true
    }

    Clear-Host

    $selected = @($items | Where-Object { $_.Selected } | ForEach-Object { $_.Version })
    if ($selected.Count -eq 0) {
        Write-Host "Aucune version selectionnee." -ForegroundColor Yellow
        exit 1
    }
    return $selected
}

function Ask-ForceReclone {
    param([string[]]$SelectedVersions)

    $hasExisting = $false
    foreach ($ver in $SelectedVersions) {
        if ((Test-VersionHasData $ver) -or (Test-VersionContainerExists $ver)) {
            $hasExisting = $true
            break
        }
    }

    if (-not $hasExisting) { return $false }

    Write-Host ""
    $answer = Read-Host "Forcer un nouveau clonage Git si le code existe deja ? [y/N]"
    return ($answer -match "^[Yy]")
}

function Start-SelectedVersions {
    param(
        [string[]]$SelectedVersions,
        [bool]$ForceReclone
    )

    Write-Host ""
    Write-Host "Demarrage des versions : $($SelectedVersions -join ', ')" -ForegroundColor Green

    if ($ForceReclone) {
        Write-Host "Suppression du code existant avant reclonage..."
        foreach ($ver in $SelectedVersions) {
            Remove-VersionClone -Ver $ver
        }
    }

    $composeArgs = @("compose")
    foreach ($ver in $SelectedVersions) {
        $composeArgs += "--profile"
        $composeArgs += (Get-ProfileName $ver)
    }
    $composeArgs += @("up", "-d", "--build", "--force-recreate")

    $liveFile = $null
    if ($SelectedVersions -contains "live") { $liveFile = Ensure-LiveConfig }

    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) { throw "Echec du demarrage Docker Compose." }

    Write-Host ""
    Write-Host "Conteneurs demarres. URLs MCP pour Cursor :" -ForegroundColor Green
    foreach ($ver in $SelectedVersions) {
        if ($ver -eq "live") {
            Write-Host "  Odoo live -> http://localhost:8900/sse  (profils : $liveFile)"
        }
        else {
            Write-Host "  Odoo $ver -> http://localhost:80$ver/sse"
        }
    }
    Write-Host ""
    Write-Host "Premier lancement : le clone Git peut prendre plusieurs minutes."
    Write-Host "Suivre les logs : .\scripts\start-odoo-mcp.ps1 -Logs -Version 18"
}

function Show-Logs {
    param([string]$Ver)
    $service = Get-ServiceName $Ver
    Write-Host "Logs $service - Ctrl+C pour quitter."
    docker compose logs -f --tail 50 $service
}

function Stop-Version {
    param([string]$Ver)
    docker compose --profile (Get-ProfileName $Ver) down
    Write-Host "Conteneur $(Get-ServiceName $Ver) arrete."
}

Push-Location $ProjectRoot
try {
    Ensure-EnvAndVolume

    if ($Logs) {
        if (-not $Version) {
            Write-Host "Versions disponibles : $($AvailableVersions -join ', ')"
            $Version = Read-Host "Version a afficher dans les logs"
        }
        if ($AvailableVersions -notcontains $Version) {
            throw "Version invalide : $Version"
        }
        Show-Logs -Ver $Version
        return
    }

    if ($Stop) {
        if (-not $Version) {
            Write-Host "Versions disponibles : $($AvailableVersions -join ', ')"
            $Version = Read-Host "Version a arreter"
        }
        if ($AvailableVersions -notcontains $Version) {
            throw "Version invalide : $Version"
        }
        Stop-Version -Ver $Version
        return
    }

    Write-Host ""
    Write-Host "=== Odoo MCP - Demarrage ===" -ForegroundColor Cyan

    $selectedVersions = Select-VersionsInteractive
    $forceReclone = Ask-ForceReclone -SelectedVersions $selectedVersions
    Start-SelectedVersions -SelectedVersions $selectedVersions -ForceReclone $forceReclone
}
finally {
    Pop-Location
}
