#!/bin/bash
# run_tests.sh — run the router's C++ unit suite, and FAIL if it ran nothing.
#
# Why this exists (review 2026-08-11, H1): the previously-documented invocation
#
#     ctest --test-dir router/build --output-on-failure
#
# is silently a no-op on this VM. `--test-dir` was added in CMake 3.20; this install is
# 3.16.3, which IGNORES the unknown flag, scans the current directory instead, finds no
# CTestTestfile.cmake, prints "No tests were found!!!" — and exits 0. Anything gating on
# that command gets a green result having executed zero tests.
#
# So: cd into the build tree explicitly (works on every ctest version), and treat a
# zero-test run as a failure rather than a pass.
#
# Usage:
#   router/run_tests.sh                 # all tests
#   router/run_tests.sh -R controller   # pass any extra ctest args through

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${ROUTER_BUILD_DIR:-${SCRIPT_DIR}/build}"

if [[ ! -f "$BUILD_DIR/CTestTestfile.cmake" ]]; then
    echo "Error: no CTestTestfile.cmake in $BUILD_DIR — configure/build first:" >&2
    echo "  cmake -B $BUILD_DIR -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0 && cmake --build $BUILD_DIR" >&2
    exit 2
fi

cd "$BUILD_DIR"

# `ctest -N` lists without running; the trailing "Total Tests: N" line is the count.
TEST_COUNT="$(ctest -N "$@" 2>/dev/null | sed -n 's/^Total Tests: //p' | tail -1)"
if [[ -z "$TEST_COUNT" || "$TEST_COUNT" -eq 0 ]]; then
    echo "Error: ctest found 0 tests in $BUILD_DIR — refusing to report success on an" >&2
    echo "  empty run (this is exactly the H1 failure mode this script exists to catch)." >&2
    exit 1
fi

echo "[run_tests] $BUILD_DIR: $TEST_COUNT test(s)"
exec ctest --output-on-failure "$@"
