# Run in an x64 or ARM64 Visual Studio Developer PowerShell with the Windows SDK.
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '../..')
try {
    New-Item -ItemType Directory -Force 'build/window-fixtures' | Out-Null
    $fixtureHash = (Get-FileHash 'tests/manual/window_fixture_windows.cpp' -Algorithm SHA256).Hash.ToLowerInvariant()
    # Hash the actual bytes cl.exe will compile; never normalize a mismatch away.
    $identityHeader = Get-Content -LiteralPath 'native_code/windows/include/window_fixture_identity.h' -Raw
    $pins = [regex]::Matches($identityHeader, '(?m)^#define SCAPKIT_WINDOW_FIXTURE_SHA256 "([a-f0-9]{64})"\r?$')
    if ($pins.Count -ne 1) { throw 'Expected exactly one Windows fixture source pin' }
    $fixturePin = $pins[0].Groups[1].Value
    if ($fixtureHash -cne $fixturePin) {
        throw "Windows fixture source/pin mismatch: source=$fixtureHash native_pin=$fixturePin"
    }
    Write-Output "Verified Windows fixture source/pin sha256 $fixtureHash"
    # A generated forced-include header avoids native-argument quote differences
    # between Windows PowerShell 5.1 and PowerShell 7.
    Set-Content -LiteralPath 'build/window-fixtures/fixture-build.h' -Encoding ascii `
        -Value ('#define FIXTURE_BUILD_SHA256 "' + $fixtureHash + '"')
    # /FI uses include-file lookup (relative to the source), not output-file
    # lookup. Pass one absolute argument, including when the checkout has spaces.
    $fixtureHeader = (Resolve-Path -LiteralPath 'build/window-fixtures/fixture-build.h').ProviderPath
    # A data-only PE resource ties adapter recognition to this exact fixture source.
    # It is not a write authorization; profiles must still pin the executable hash.
    Set-Content -LiteralPath 'build/window-fixtures/fixture.rc' -Encoding ascii `
        -Value ('SCAPKIT_WINDOW_FIXTURE_SOURCE RCDATA { "' + $fixtureHash + '" }')
    & rc.exe /nologo /fo 'build/window-fixtures/fixture.res' 'build/window-fixtures/fixture.rc'
    if ($LASTEXITCODE -ne 0) { throw "rc.exe exited $LASTEXITCODE" }
    $fixtureFlags = @('/nologo', '/std:c++20', '/EHsc', '/utf-8', '/W4', '/permissive-',
        '/DUNICODE', '/D_UNICODE', '/D_WIN32_WINNT=0x0A00',
        ('/FI' + $fixtureHeader),
        '/Fobuild/window-fixtures/window-fixture-windows.obj',
        '/Febuild/window-fixtures/window-fixture-windows.exe')
    if ($env:SCAPKIT_ANALYZE -eq '1') { $fixtureFlags += '/analyze' }
    & cl.exe @fixtureFlags 'tests/manual/window_fixture_windows.cpp' 'build/window-fixtures/fixture.res' /link user32.lib gdi32.lib `
        ole32.lib oleaut32.lib uiautomationcore.lib advapi32.lib comctl32.lib uuid.lib
    if ($LASTEXITCODE -ne 0) { throw "cl.exe exited $LASTEXITCODE" }
    Write-Output "Built build/window-fixtures/window-fixture-windows.exe (source sha256 $fixtureHash)"
} finally { Pop-Location }
