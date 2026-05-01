#!/bin/bash
set -e
cd "$(dirname "$0")"

rm -rf dist || true
rm -rf src/scapkit_computer_use.egg-info || true

uv build