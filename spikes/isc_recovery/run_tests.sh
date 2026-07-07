#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Instance-state recovery spike runner — Scenario A vs Scenario B (see PLAN.md).
#
# Each test runs on its own domain and its own working dir (so durable-writer-history
# files don't cross tests). The writer is driven over a FIFO so we can send timed
# commands and, for Scenario B, SIGKILL and relaunch it with the SAME DWH file.
#
# The verdict for each test is read from the reader's transition log:
#   via=STATE  -> recovery/transition delivered by the middleware with NO data sample
#                 (native ISC recovery, or a dispose/no-writers state change)
#   via=DATA   -> recovery came from a (re)written / replayed data sample
#
# Usage: NDDSHOME=/home/rti/rti_connext_dds-7.7.0 ./run_tests.sh [base_domain]
# -----------------------------------------------------------------------------
set -u
: "${NDDSHOME:?set NDDSHOME to your Connext install}"
export NDDSHOME

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/build"
QOS="$HERE/qos_isc_recovery.xml"
WRITER="$BIN/state_writer"
READER="$BIN/state_reader"
RELAY="$BIN/state_relay"
BASE_DOMAIN="${1:-50}"
# Working dirs must be on a LOCAL fs: the VirtualBox shared folder (vboxsf) does not
# support mkfifo (needed to feed the writer timed commands) — so never under $BIN.
RUNROOT="${RUNROOT:-$(mktemp -d /tmp/isc_recovery_run.XXXXXX)}"

[ -x "$WRITER" ] && [ -x "$READER" ] || { echo "build first: cmake --build build"; exit 1; }
rm -rf "$RUNROOT"; mkdir -p "$RUNROOT"
mkfifo "$RUNROOT/.probe" 2>/dev/null || { echo "FATAL: $RUNROOT does not support mkfifo; set RUNROOT to a local fs"; exit 1; }
rm -f "$RUNROOT/.probe"

# --- per-test scaffolding ----------------------------------------------------
READER_PID=""; WRITER_PID=""; FIFO=""; WORK=""

start_reader() { # $1=profile  (on $DOM)
    "$READER" --domain "$DOM" --qos-file "$QOS" --profile "$1" >"$WORK/reader.log" 2>&1 &
    READER_PID=$!
}
start_reader_dom() { # $1=domain $2=profile
    "$READER" --domain "$1" --qos-file "$QOS" --profile "$2" >"$WORK/reader.log" 2>&1 &
    READER_PID=$!
}
start_relay() { # $1=up-domain $2=down-domain $3...=extra relay args
    local up="$1" down="$2"; shift 2
    "$RELAY" --upstream-domain "$up" --downstream-domain "$down" \
        --qos-file "$QOS" --profile Recovery::Recover "$@" >"$WORK/relay.log" 2>&1 &
    RELAY_PID=$!
}
start_writer() { # $1=profile ; reads commands from $FIFO
    "$WRITER" --domain "$DOM" --qos-file "$QOS" --profile "$1" <"$FIFO" >>"$WORK/writer.log" 2>&1 &
    WRITER_PID=$!
    exec 3>"$FIFO"    # hold the write end open so the writer's stdin stays open
}
say()   { echo "$1" >&3; }            # send a command to the writer
kill_writer_hard() { kill -9 "$WRITER_PID" 2>/dev/null; wait "$WRITER_PID" 2>/dev/null; exec 3>&- 2>/dev/null; }
stop_all() {
    exec 3>&- 2>/dev/null
    [ -n "$WRITER_PID" ] && kill -TERM "$WRITER_PID" 2>/dev/null
    [ -n "$RELAY_PID" ]  && kill -TERM "$RELAY_PID" 2>/dev/null
    [ -n "$READER_PID" ] && { kill -USR1 "$READER_PID" 2>/dev/null; sleep 1; kill -TERM "$READER_PID" 2>/dev/null; }
    wait 2>/dev/null
}
setup() { # $1=test name
    WORK="$RUNROOT/$1"; mkdir -p "$WORK"; cd "$WORK"
    FIFO="$WORK/cmd.fifo"; mkfifo "$FIFO"
    READER_PID=""; WRITER_PID=""; RELAY_PID=""
    DOM=$((BASE_DOMAIN++))
    echo; echo "================ $1  (domain $DOM) ================"
}
# last observed state for a key, and how it arrived
laststate() { grep "key=$1 " "$WORK/reader.log" | tail -1; }
evidence()  { echo "  reader transitions for $1:"; grep "key=$1 " "$WORK/reader.log" | sed 's/^/    /'; }

PASS=0; FAIL=0; INSPECT=0
verdict() { # $1=label $2=result(PASS/FAIL/INSPECT) $3=detail
    printf "  [%s] %s — %s\n" "$2" "$1" "$3"
    case "$2" in PASS) PASS=$((PASS+1));; FAIL) FAIL=$((FAIL+1));; *) INSPECT=$((INSPECT+1));; esac
}

# =============================================================================
# T0 — sanity: late-joining reader sees durable last-value + a dispose
# =============================================================================
setup T0_sanity
start_writer Recovery::Recover
say "write K1 alpha"; say "write K2 beta"; sleep 1; say "dispose K2"; sleep 2
start_reader Recovery::Recover            # reader joins AFTER the states are set
sleep 4
evidence K1; evidence K2
l1="$(laststate K1)"; l2="$(laststate K2)"
{ echo "$l1" | grep -q "ALIVE"; } && { echo "$l2" | grep -q "NOT_ALIVE_DISPOSED"; } \
    && verdict "late joiner sees K1=ALIVE, K2=DISPOSED" PASS "durable replay works" \
    || verdict "late joiner state" INSPECT "check transitions above"
stop_all

# =============================================================================
# T1 — imperative-mirror thesis: a NEW reader converges to imperatively-set states
# =============================================================================
setup T1_imperative_mirror
start_writer Recovery::Recover
say "write A a"; say "write B b"; say "write C c"; sleep 1
say "dispose B"; say "unregister C"; sleep 2
start_reader Recovery::Recover
sleep 4
evidence A; evidence B; evidence C
{ laststate A | grep -q ALIVE; } && { laststate B | grep -q DISPOSED; } \
    && verdict "mirror-set states served to new reader" PASS "A=ALIVE B=DISPOSED (C see notes)" \
    || verdict "mirror thesis" INSPECT "check transitions above"
stop_all

# =============================================================================
# T2 — Scenario A: SAME physical writer loses+regains liveliness (pause/resume)
#      Expect recovery to ALIVE via=STATE (native ISC), with NO new data sample.
# =============================================================================
setup T2_scenarioA_liveliness
start_reader Recovery::Recover
sleep 2
start_writer Recovery::Recover
say "write K1 live"; sleep 3          # reader: K1 ALIVE via=DATA
say "pause"; sleep 5                  # lease 2s -> reader: K1 NOT_ALIVE_NO_WRITERS via=STATE
say "resume"; sleep 4                 # liveliness regained, NO new write
evidence K1
post="$(grep 'key=K1 ' "$WORK/reader.log" | tail -1)"
if echo "$post" | grep -q "ALIVE via=STATE"; then
    verdict "Scenario A recovers via native ISC" PASS "final K1 ALIVE via=STATE (no data)"
elif echo "$post" | grep -q "ALIVE via=DATA"; then
    verdict "Scenario A" INSPECT "recovered but via=DATA — not pure ISC"
else
    verdict "Scenario A" FAIL "K1 did not return to ALIVE: $post"
fi
stop_all

# =============================================================================
# T3/T4 — Scenario B (writer PROCESS RESTART + durable writer history) are DEFERRED
# to a separate Phase-2 runner. They require DWH (SQLite) and SIGKILL, so they must
# be set up carefully and run entirely on a local fs. Not part of this relay-only run.
# =============================================================================
echo; echo "(Scenario B / durable-writer-history tests deferred to Phase 2 — not run here)"

# =============================================================================
# T5 — NoRecover control: repeat Scenario A with ISC OFF; expect NO STATE recovery
# =============================================================================
setup T5_norecover_control
start_reader Recovery::NoRecover
sleep 2
start_writer Recovery::NoRecover
say "write K1 live"; sleep 3
say "pause"; sleep 5
say "resume"; sleep 4                 # regain liveliness, no write
evidence K1
post="$(grep 'key=K1 ' "$WORK/reader.log" | tail -1)"
if echo "$post" | grep -q "ALIVE via=STATE"; then
    verdict "NoRecover control" FAIL "recovered via=STATE with ISC OFF — unexpected"
else
    verdict "NoRecover: no free ISC recovery" PASS "K1 not restored by liveliness alone: $post"
fi
stop_all

# =============================================================================
# RELAY CHAIN:  origin(up) --> [ relay: reader -> mirror -> writer ] --> downstream(down)
# The point: does instance state RELAYED from the relay's reader reach its writer so the
# downstream reader sees it — including leg-1 native ISC recovery back to ALIVE?
# =============================================================================

# R1 — basic relay of instance state across both legs
setup R1_relay_basic
UP=$DOM; DOWN=$((BASE_DOMAIN++)); echo "  chain: origin($UP) -> relay -> reader($DOWN)"
start_relay "$UP" "$DOWN"; sleep 2
start_reader_dom "$DOWN" Recovery::Recover; sleep 2
FIFO="$WORK/cmd.fifo"   # writer (origin) on UP
"$WRITER" --domain "$UP" --qos-file "$QOS" --profile Recovery::Recover <"$FIFO" >>"$WORK/writer.log" 2>&1 &
WRITER_PID=$!; exec 3>"$FIFO"
say "write K1 alpha"; say "write K2 beta"; sleep 2; say "dispose K2"; sleep 3
evidence K1; evidence K2
{ laststate K1 | grep -qE " ALIVE via="; } && { laststate K2 | grep -q NOT_ALIVE_DISPOSED; } \
    && verdict "relay propagates state end-to-end" PASS "downstream K1=ALIVE, K2=DISPOSED" \
    || verdict "relay basic" INSPECT "check transitions above"
stop_all

# R3 — the CORE-13337 gap: relay with --no-reassert cannot route NO_WRITERS -> ALIVE
setup R3_relay_gap_noreassert
UP=$DOM; DOWN=$((BASE_DOMAIN++)); echo "  chain: origin($UP) -> relay(--no-reassert) -> reader($DOWN)"
start_relay "$UP" "$DOWN" --no-reassert; sleep 2
start_reader_dom "$DOWN" Recovery::Recover; sleep 2
FIFO="$WORK/cmd.fifo"
"$WRITER" --domain "$UP" --qos-file "$QOS" --profile Recovery::Recover <"$FIFO" >>"$WORK/writer.log" 2>&1 &
WRITER_PID=$!; exec 3>"$FIFO"
say "write K1 live"; sleep 3          # downstream K1 ALIVE
say "pause"; sleep 5                  # origin loses liveliness -> relay unregisters -> downstream NO_WRITERS
say "resume"; sleep 5                 # leg-1 ISC recovers ALIVE, but relay does NOT re-write
evidence K1
post="$(grep 'key=K1 ' "$WORK/reader.log" | tail -1)"
if echo "$post" | grep -qE " ALIVE via="; then
    verdict "gap demo (no-reassert)" INSPECT "downstream recovered anyway: $post"
else
    verdict "gap reproduced: intermediary can't route NO_WRITERS->ALIVE" PASS "downstream stuck: $post"
fi
stop_all

# R2 — the fix: relay re-writes cached value on leg-1 ALIVE recovery -> downstream ALIVE
setup R2_relay_reassert_fix
UP=$DOM; DOWN=$((BASE_DOMAIN++)); echo "  chain: origin($UP) -> relay(reassert on) -> reader($DOWN)"
start_relay "$UP" "$DOWN"; sleep 2
start_reader_dom "$DOWN" Recovery::Recover; sleep 2
FIFO="$WORK/cmd.fifo"
"$WRITER" --domain "$UP" --qos-file "$QOS" --profile Recovery::Recover <"$FIFO" >>"$WORK/writer.log" 2>&1 &
WRITER_PID=$!; exec 3>"$FIFO"
say "write K1 live"; sleep 3
say "pause"; sleep 5
say "resume"; sleep 5                  # leg-1 ISC recovery -> relay re-writes cached K1 -> downstream ALIVE
evidence K1
post="$(grep 'key=K1 ' "$WORK/reader.log" | tail -1)"
if echo "$post" | grep -qE " ALIVE via="; then
    verdict "reassert fix routes recovery end-to-end" PASS "downstream K1 back to ALIVE: $post"
else
    verdict "reassert fix" FAIL "downstream did not recover: $post"
fi
stop_all

# =============================================================================
echo; echo "================ SUMMARY ================"
echo "PASS=$PASS  FAIL=$FAIL  INSPECT=$INSPECT"
echo "Logs under: $RUNROOT/<test>/{reader,writer}.log"
