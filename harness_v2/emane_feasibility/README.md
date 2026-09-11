# EMANE RF Pipe Feasibility Fixture

This disposable two-node fixture proves that EMANE 1.5.3 can create an `emane0`
virtual transport inside a container and carry a bounded UDP datagram through RF
Pipe. It is not the ACT node-stack baseline and does not make any DDS claim.

Run it only when no other EMANE feasibility topology is active:

```bash
sg docker -c 'bash harness_v2/scripts/run_emane_rfpipe_feasibility.sh run'
```

The fixture owns its Compose project and tears it down after `run`. `up`, `probe`,
and `down` are available for diagnosis. The only Docker bridge is `emane_ctrl`;
the probe binds its source and destination to the distinct `emane0` RF addresses
and verifies the destination route selects `emane0`. Once both NEMs are ready,
the harness injects explicit $0\,\mathrm{dB}$ bidirectional RF Pipe pathloss events;
without them, RF Pipe's `precomputed` propagation mode has no nominal link to use.