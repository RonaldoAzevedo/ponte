# Gera dist\Ponte.exe (arquivo unico, sem console).
# Uso:  powershell -ExecutionPolicy Bypass -File .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependencias." }

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Testes falharam; build cancelado." }

# Propriedades -> Detalhes do .exe (nome, versao, autoria).
& $py tools\make_version_file.py build\version_info.txt
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o arquivo de versao." }

# tools\build_exe.py = PyInstaller + tela de abertura no tema do app (clara ou escura).
& $py tools\build_exe.py main.py `
    --name Ponte `
    --onefile `
    --windowed `
    --noconfirm `
    --clean `
    --collect-all mitmproxy `
    --collect-all mitmproxy_rs `
    --collect-all customtkinter `
    --icon vpnbridge/assets/icon.ico `
    --splash vpnbridge/assets/splash-dark.png `
    --version-file build/version_info.txt `
    --add-data "vpnbridge/assets;vpnbridge/assets"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou." }

# Pacote para compartilhar: exe + guia do usuario.
$version = & $py -c "import vpnbridge; print(vpnbridge.__version__)"
$zip = "dist\Ponte-$version.zip"
Compress-Archive -Path "dist\Ponte.exe", "LEIA-ME.txt" -DestinationPath $zip -Force

Write-Host "`nPronto: dist\Ponte.exe" -ForegroundColor Green
Write-Host "Para compartilhar: $zip" -ForegroundColor Green
