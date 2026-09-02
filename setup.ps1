<#
.SYNOPSIS
Ricostruisce l'ambiente Windows di Ares.

.DESCRIPTION
Crea il virtualenv Python 3.12, installa le dipendenze bloccate in uv.lock e
Ares stesso nel venv, poi verifica Ollama con `ares-preflight`. Non modifica
tmp/, workspace o backup.

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

    if ($SkipPreflight) {
        Write-Host
        Write-Host "Dipendenze pronte; preflight Ollama saltato."
    }
    else {
        Write-Host
        & ".venv\Scripts\ares-preflight.exe"
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
