$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$desktop = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvPython = Join-Path $desktop '.venv\Scripts\python.exe'
$python = if ($env:NOVAL_SIDECAR_PYTHON) {
    $env:NOVAL_SIDECAR_PYTHON
} elseif (Test-Path $venvPython) {
    $venvPython
} else {
    (py -3.13 -c 'import sys; print(sys.executable)').Trim()
}
$version = (& $python -c 'import sys; print(sys.version_info.major, sys.version_info.minor)').Trim()
if ($version -ne '3 13') {
    throw "Noval Desktop Sidecar must be built with Python 3.13; found $version."
}
& $python -m ensurepip --upgrade
& $python -m pip install --disable-pip-version-check pyinstaller
& $python -m pip install --disable-pip-version-check --no-deps --editable $root
& $python -m PyInstaller --noconfirm --clean --onedir --name noval-sidecar --paths $root --distpath (Join-Path $desktop 'build\sidecar') --workpath (Join-Path $desktop 'build\pyinstaller') --specpath (Join-Path $desktop 'build') (Join-Path $desktop 'sidecar\noval_sidecar\__main__.py')
