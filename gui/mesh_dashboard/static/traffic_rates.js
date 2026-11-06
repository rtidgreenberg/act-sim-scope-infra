// Shared capture-time rate validation and duration-weighted averages.
(function () {
  "use strict";
  const WINDOW_MS = 3000;

  function issue(sample, network = false) {
    if (!sample || sample.schema_version < 3 || sample.timestamp_basis !== "capture") {
      return "Capture-time monitor required";
    }
    if (!Number.isFinite(sample.interval_ms) || sample.interval_ms <= 0 ||
        !Number.isFinite(sample.capture_timestamp) ||
        sample.capture_window_start_ms !== sample.capture_timestamp - sample.interval_ms ||
        !Number.isFinite(sample.reported_at_ms) || sample.reported_at_ms < sample.capture_timestamp) {
      return "Invalid capture window";
    }
    const expires = network ? sample.fresh_until_ms
      : sample.reported_at_ms + Math.max(5000, sample.interval_ms * 3);
    if (sample.stale || !Number.isFinite(expires) || Date.now() > expires) return "DDS capture stale";
    if (sample.capture_timing_valid !== true) return "Late capture rows";
    if (network) {
      if (!sample.complete || !sample.windows_aligned) return "Waiting for aligned DDS captures";
      if (!sample.capture_complete) return "DDS capture incomplete";
    } else if (sample.capture_status !== "live" || sample.capture_gap ||
               sample.capture_invalid_rows_interval !== 0 ||
               sample.late_capture_packets_interval !== 0 ||
               sample.orphan_fragment_packets_interval !== 0 ||
               sample.incomplete_fragment_packets !== 0) {
      return "DDS capture incomplete";
    }
    return null;
  }

  class History {
    constructor() {
      this.entries = [];
      this.restart = null;
      this.problem = "Warming 3s capture window";
    }

    add(sample, values, network = false) {
      const previous = this.entries.at(-1);
      if (previous && sample.capture_timestamp < previous.end) return false;
      const problem = issue(sample, network);
      if (problem || !Object.values(values).every(value => Number.isFinite(value) && value >= 0)) {
        this.entries = [];
        this.problem = problem || "DDS counters unavailable";
        return false;
      }
      const replace = previous && sample.capture_timestamp === previous.end;
      if (sample.capture_restarts !== this.restart ||
          (previous && !replace && sample.capture_window_start_ms !== previous.end)) {
        this.entries = [];
      }
      const row = {start: sample.capture_window_start_ms, end: sample.capture_timestamp, values};
      if (replace && this.entries.length) this.entries[this.entries.length - 1] = row;
      else this.entries.push(row);
      this.entries = this.entries.filter(entry => entry.end > row.end - WINDOW_MS);
      this.restart = sample.capture_restarts;
      this.problem = row.end - this.entries[0].start < WINDOW_MS ? "Warming 3s capture window" : null;
      return true;
    }

    rate(field) {
      if (this.problem) return null;
      const cutoff = this.entries.at(-1).end - WINDOW_MS;
      return this.entries.reduce((total, entry) =>
        total + entry.values[field] * (entry.end - Math.max(cutoff, entry.start)) / WINDOW_MS, 0);
    }
  }

  window.ACTTrafficRates = {issue, History};
})();
