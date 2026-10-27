#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
mkdir -p build/window-fixtures
fixture_hash=$(shasum -a 256 tests/manual/window_fixture_macos.m | cut -d ' ' -f 1)
xcrun --sdk macosx clang -fobjc-arc -fblocks -Wall -Wextra -Werror \
    -Werror=unguarded-availability -mmacosx-version-min=13.0 \
    "-DFIXTURE_BUILD_SHA256=\"$fixture_hash\"" \
    tests/manual/window_fixture_macos.m -framework AppKit \
    -o build/window-fixtures/window-fixture-macos
printf 'Built build/window-fixtures/window-fixture-macos (source sha256 %s)\n' "$fixture_hash"
