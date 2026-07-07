#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Multi-hop relay chain — basis for the framework.
#
#     origin(D0) --> relay(D0->D1) --> relay(D1->D2) --> ... --> reader(Dn)
#
# Proves instance state + ISC-recovery re-assert COMPOSE across more than one
# intermediary (the real ACT topology: platform-node router + control-node router).
# The chain length is just the length of the DOMS array — set 3 domains for 2 hops.
#
# Phase-1 safe: no durable writer history, no process kills, all runtime files on a
# local fs, UDPv4-only transport. Liveliness pause/resume drives Scenario-A recovery.
#
# Usage: NDDSHOME=... ./run_2hop.sh [base_domain]
# -----------------------------------------------------------------------------
set -u
: "${NDDSHOME:?set NDDSHOME to your Connext install}"; export NDDSHOME

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/build"; QOS="$HERE/qos_isc_recovery.xml"
WRITER="$BIN/state_writer"; READER="$BIN/state_reader"; RELAY="$BIN/state_relay"
PROFILE="Recovery::Recover"
BASE="${1:-120}"

[ -x "$WRITER" ] && [ -x "$READER" ] && [ -x "$RELAY" ] || { echo "build first: cmake --build build"; exit 1; }
WORK="$(mktemp -d /tmp/isc_2hop.XXXXXX)"
mkfifo "$WORK/.probe" 2>/dev/null || { echo "FATAL: $WORK not local (no mkfifo)"; exit 1; }
rm -f "$WORK/.probe"; cd "$WORK"

# Chain domains: origin on the first, reader on the last, one relay per adjacent pair.
DOMS=($BASE $((BASE+1)) $((BASE+2)))       # 3 domains => 2 hops
N=${#DOMS[@]}
ORIGIN_DOM=${DOMS[0]}; READER_DOM=${DOMS[$((N-1))]}
echo "chain: origin($ORIGIN_DOM) $(for ((i=1;i<N;i++)); do echo -n "-> relay(${DOMS[$((i-1))]}->${DOMS[$i]}) "; done)-> reader($READER_DOM)"

RELAY_PIDS=()
FIFO="$WORK/cmd.fifo"; mkfifo "$FIFO"

# downstream reader on the last domain
"$READER" --domain "$READER_DOM" --qos-file "$QOS" --profile "$PROFILE" >"$WORK/reader.log" 2>&1 &
RPID=$!
# relays for each adjacent pair, started from the reader end inward
for ((i=N-1; i>=1; i--)); do
    up=${DOMS[$((i-1))]}; down=${DOMS[$i]}
    "$RELAY" --upstream-domain "$up" --downstream-domain "$down" \
        --qos-file "$QOS" --profile "$PROFILE" >"$WORK/relay_${up}_${down}.log" 2>&1 &
    RELAY_PIDS+=($!)
done
sleep 3   # let the chain form end-to-end
# origin writer on the first domain
"$WRITER" --domain "$ORIGIN_DOM" --qos-file "$QOS" --profile "$PROFILE" <"$FIFO" >"$WORK/writer.log" 2>&1 &
WPID=$!; exec 3>"$FIFO"
say() { echo "$1" >&3; }
sleep 2

PASS=0; FAIL=0
laststate() { grep "key=$1 " "$WORK/reader.log" | tail -1; }
check() { # $1=label $2=key $3=expected-substring
    local l; l="$(laststate "$2")"
    if echo "$l" | grep -q "$3"; then echo "  [PASS] $1 — $l"; PASS=$((PASS+1));
    else echo "  [FAIL] $1 — expected '$3', got: ${l:-<none>}"; FAIL=$((FAIL+1)); fi
}

echo; echo "---- H1: basic instance state across $((N-1)) hops ----"
say "write K1 alpha"; say "write K2 beta"; sleep 2; say "dispose K2"; sleep 4
echo "  reader transitions:"; grep -E "key=(K1|K2) " "$WORK/reader.log" | sed 's/^/    /'
check "K1 relayed ALIVE end-to-end" K1 " ALIVE via="
check "K2 dispose relayed end-to-end" K2 "NOT_ALIVE_DISPOSED"

echo; echo "---- H2: Scenario-A recovery across $((N-1)) hops (origin pause/resume) ----"
say "pause";  sleep 6      # origin loses liveliness -> unregister ripples down the chain
say "resume"; sleep 7      # hop-1 ISC recovery -> re-write -> propagates as data down the chain
echo "  K1 transitions:"; grep "key=K1 " "$WORK/reader.log" | sed 's/^/    /'
check "K1 recovered ALIVE across the chain" K1 " ALIVE via="

echo; echo "================ SUMMARY ================"
echo "PASS=$PASS  FAIL=$FAIL   (logs: $WORK)"

# cleanup
exec 3>&- 2>/dev/null
kill -TERM "$WPID" "${RELAY_PIDS[@]}" 2>/dev/null
kill -TERM "$RPID" 2>/dev/null
wait 2>/dev/null
