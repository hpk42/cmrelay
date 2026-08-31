#!/bin/sh
#
# Wrapper for building the docs
set -e
cd "$(dirname "$0")/.."
uv run --group doc make -C doc html
