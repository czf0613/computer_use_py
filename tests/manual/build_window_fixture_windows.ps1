# Run in an x64 or ARM64 Visual Studio Developer PowerShell with the Windows SDK.
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '../..')
try {
    New-Item -ItemType Directory -Force 'build/window-fixtures' | Out-Null
    $fixtureHash = (Get-FileHash 'tests/manual/window_fixture_windows.cpp' -Algorithm SHA256).Hash.ToLowerInvariant()
    # A generated forced-include header avoids native-argument quote differences
    # between Windows PowerShell 5.1 and PowerShell 7.
    Set-Content -LiteralPath 'build/window-fixtures/fixture-build.h' -Encoding ascii `
        -Value ('#define FIXTURE_BUILD_SHA256 "' + $fixtureHash + '"')
    $fixtureFlags = @('/nologo', '/std:c++20', '/EHsc', '/utf-8', '/W4', '/permissive-',
        '/DUNICODE', '/D_UNICODE', '/D_WIN32_WINNT=0x0A00',
        '/FIbuild/window-fixtures/fixture-build.h',
        '/Fobuild/window-fixtures/window-fixture-windows.obj',
        '/Febuild/window-fixtures/window-fixture-windows.exe')
    if ($env:SCAPKIT_ANALYZE -eq '1') { $fixtureFlags += '/analyze' }
    & cl.exe @fixtureFlags 'tests/manual/window_fixture_windows.cpp' /link user32.lib gdi32.lib `
        ole32.lib oleaut32.lib uiautomationcore.lib advapi32.lib comctl32.lib uuid.lib
    if ($LASTEXITCODE -ne 0) { throw "cl.exe exited $LASTEXITCODE" }
    Write-Output "Built build/window-fixtures/window-fixture-windows.exe (source sha256 $fixtureHash)"
} finally { Pop-Location }
