<#
.SYNOPSIS
Ricostruisce l'ambiente Windows di Ares.

.DESCRIPTION
Crea il virtualenv Python 3.12, installa le dipendenze bloccate in uv.lock e
Ares stesso nel venv, scrive lo shim `ares.cmd` in %USERPROFILE%\.local\bin
(o in ARES_BIN_DIR), porta in ~\.ares lo stato di un clone precedente e
verifica Ollama con `ares preflight`. Lo stato appreso e i backup non vengono
toccati, salvo quello spostamento una tantum.

.PARAMETER SkipPreflight
Salta soltanto il controllo di Ollama e dei modelli. Serve alla CI e a chi
vuole preparare le dipendenze prima di avviare Ollama.

.EXAMPLE
.\setup.ps1

.EXAMPLE
.\setup.ps1 -SkipPreflight
#>

[CmdletBinding()]
param(
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
$PythonVersion = "3.12"
$VenvPython = ".venv\Scripts\python.exe"

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,

        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $Uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $Uv) {
        Write-Host "Manca uv. Installalo con uno dei comandi ufficiali:"
        Write-Host "    winget install --id=astral-sh.uv -e"
        Write-Host '    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
        throw "uv non disponibile nel PATH"
    }

    if (Test-Path -LiteralPath ".venv") {
        if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
            throw ".venv esiste ma non e' un virtualenv Windows; spostalo o rimuovilo e riprova"
        }
        Write-Host "Virtualenv gia' presente, lo allineo."
    }
    else {
        Write-Host "Creo il virtualenv su Python $PythonVersion."
    }

    # `sync` porta il venv esattamente com'e' scritto in uv.lock, creandolo se
    # manca, e installa Ares in editable: i comandi `ares`, `ares-backup`...
    # compaiono in .venv\Scripts. `--locked` rifiuta un lock non allineato al
    # pyproject; `--no-dev` lascia fuori gli strumenti di sviluppo, come fa
    # setup.sh. Gli hash del lock vengono verificati a ogni download.
    Write-Host "Installo le dipendenze bloccate."
    Invoke-External {
        & $Uv.Source sync --locked --no-dev --python $PythonVersion
    } "sincronizzazione delle dipendenze fallita"

    Invoke-External {
        & $Uv.Source pip check --python $VenvPython
    } "le dipendenze installate non sono coerenti"

    # `ares` da qualunque cartella: uno shim `.cmd` che chiama il comando del
    # venv. Uno shim e non `uv tool install`, che risolverebbe le dipendenze
    # da capo senza guardare uv.lock: il comando globale deve essere
    # esattamente l'ambiente bloccato, e seguire il codice del clone.
    $BinDir = if ($env:ARES_BIN_DIR) { $env:ARES_BIN_DIR } else { Join-Path $env:USERPROFILE ".local\bin" }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $Shim = Join-Path $BinDir "ares.cmd"
    $Target = Join-Path $PSScriptRoot ".venv\Scripts\ares.exe"
    Set-Content -LiteralPath $Shim -Value ('@"' + $Target + '" %*') -Encoding ASCII
    Write-Host "Comando globale: $Shim"
    if (-not (($env:Path -split ';') -contains $BinDir)) {
        Write-Host "  $BinDir non e' nel PATH. Aggiungilo una volta con:"
        Write-Host "      [Environment]::SetEnvironmentVariable('Path', `$env:Path + ';$BinDir', 'User')"
        Write-Host "  e riapri il terminale."
    }

    # Lo stato di un clone precedente, da tmp\ e ..\ares-backup a ~\.ares.
    # Non fa niente se e' gia' li' o se non c'e' niente da spostare.
    Write-Host
    Invoke-External {
        & ".venv\Scripts\ares.exe" migrate
    } "lo spostamento dello stato in ~\.ares non e' riuscito"

    if ($SkipPreflight) {
        Write-Host
        Write-Host "Dipendenze pronte; preflight Ollama saltato."
    }
    else {
        Write-Host
        & ".venv\Scripts\ares.exe" preflight
        if ($LASTEXITCODE -ne 0) {
            throw "le dipendenze sono a posto, ma il preflight Ollama non e' passato"
        }
    }
}
catch {
    [Console]::Error.WriteLine("ERRORE: " + $_.Exception.Message)
    exit 1
}
finally {
    Pop-Location
}
