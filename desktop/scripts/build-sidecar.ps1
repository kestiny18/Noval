$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$desktop = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
py -m pip install --disable-pip-version-check pyinstaller
py -m pip install --disable-pip-version-check --no-deps --editable $root
py -m PyInstaller --noconfirm --clean --onedir --name noval-sidecar --paths $root --distpath (Join-Path $desktop 'build\sidecar') --workpath (Join-Path $desktop 'build\pyinstaller') --specpath (Join-Path $desktop 'build') (Join-Path $desktop 'sidecar\noval_sidecar\__main__.py')
