# Start Ollama and run the local harness from the project virtual environment.
param(
    [ValidateSet("up", "test", "eval", "chat", "ui", "run")]
    [string]$Command = "up",
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$ConfigPath = Join-Path $RepoRoot "codeharness.json"

function Get-OllamaExe {
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $local = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $local) {
        return $local
    }
    throw "Ollama is not installed. Install it, then run this script again."
}

function Test-OllamaApi {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-HarnessModel {
    if (-not (Test-Path $ConfigPath)) {
        throw "Missing $ConfigPath. Copy codeharness.example.json to codeharness.json and set model."
    }
    $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    if (-not $config.model) {
        throw "Set model in codeharness.json before starting Ollama."
    }
    return [string]$config.model
}

function Start-OllamaIfNeeded {
    if (Test-OllamaApi) {
        Write-Output "Ollama is already running at http://127.0.0.1:11434"
        return
    }
    $exe = Get-OllamaExe
    Write-Output "Starting Ollama..."
    Start-Process -FilePath $exe -ArgumentList "serve" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-OllamaApi) {
            Write-Output "Ollama is running at http://127.0.0.1:11434"
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "Ollama did not answer on port 11434."
}

function Assert-ModelPresent {
    param([string]$ModelName)
    $exe = Get-OllamaExe
    $listed = (& $exe list | Out-String)
    if ($listed -notmatch [regex]::Escape($ModelName)) {
        Write-Output "Downloading $ModelName ..."
        & $exe pull $ModelName
        if ($LASTEXITCODE -ne 0) {
            throw "Could not download $ModelName"
        }
    }
    Write-Output "Model ready: $ModelName"
}

function Assert-Venv {
    if (-not (Test-Path $Python)) {
        throw "Missing $Python. From the repo root run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
    }
}

if (-not $Root) {
    $Root = $RepoRoot
}

switch ($Command) {
    "up" {
        $model = Get-HarnessModel
        Start-OllamaIfNeeded
        Assert-Venv
        Push-Location $RepoRoot
        try {
            & $Python -m codeharness pull
            if ($LASTEXITCODE -ne 0) {
                throw "Could not download $model"
            }
        } finally {
            Pop-Location
        }
        Write-Output "Environment ready."
        Write-Output "Test the loop without the model: .\scripts\harness.ps1 test"
        Write-Output "Score the local model: .\scripts\harness.ps1 eval"
        Write-Output "Chat in a project: .\scripts\harness.ps1 chat"
        Write-Output "Open the visual page: .\scripts\harness.ps1 ui"
    }
    "test" {
        Assert-Venv
        & $Python -m pytest $RepoRoot
        exit $LASTEXITCODE
    }
    "eval" {
        $model = Get-HarnessModel
        Start-OllamaIfNeeded
        Assert-ModelPresent -ModelName $model
        Assert-Venv
        Push-Location $RepoRoot
        try {
            & $Python -m codeharness eval
            exit $LASTEXITCODE
        } finally {
            Pop-Location
        }
    }
    "ui" {
        $model = Get-HarnessModel
        Start-OllamaIfNeeded
        Assert-ModelPresent -ModelName $model
        Assert-Venv
        Push-Location $RepoRoot
        try {
            & $Python -m codeharness --root $Root ui
            exit $LASTEXITCODE
        } finally {
            Pop-Location
        }
    }
    "run" {
        Assert-Venv
        Push-Location $RepoRoot
        try {
            & $Python -m codeharness --root $Root run
            exit $LASTEXITCODE
        } finally {
            Pop-Location
        }
    }
    "chat" {
        $model = Get-HarnessModel
        Start-OllamaIfNeeded
        Assert-ModelPresent -ModelName $model
        Assert-Venv
        Push-Location $RepoRoot
        try {
            & $Python -m codeharness --root $Root chat
            exit $LASTEXITCODE
        } finally {
            Pop-Location
        }
    }
}
