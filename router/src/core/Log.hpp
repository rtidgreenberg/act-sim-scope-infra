// Log.hpp — the router's single structured log stream (Phase 0).
//
// One logfmt line per event to stderr, tagged source=router. Thread-safe: the emit
// path takes a mutex, so it is safe to call from multiple threads (later, DDS/waitset
// threads). Values that contain spaces, '=', or quotes are quoted and escaped.
//
// Design note (code-architecture.md): this is "one structured stream". In a later DDS
// phase the Connext logger is bridged into this same stream via
// rti::config::Logger::instance().output_handler(...) so middleware messages arrive
// tagged source=connext alongside source=router. That bridge is intentionally absent
// here — Phase 0 creates no participants, so there is nothing to bridge yet.

#pragma once

#include <chrono>
#include <ctime>
#include <initializer_list>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <utility>

namespace router {

enum class LogLevel { Debug, Info, Warn, Error };

inline const char *to_string(LogLevel l) {
    switch (l) {
    case LogLevel::Debug: return "DEBUG";
    case LogLevel::Info:  return "INFO";
    case LogLevel::Warn:  return "WARN";
    case LogLevel::Error: return "ERROR";
    }
    return "INFO";
}

namespace detail {

inline std::mutex &log_mutex() {
    static std::mutex m;
    return m;
}

inline std::string quote_if_needed(const std::string &v) {
    bool needs = v.empty();
    for (char c : v) {
        if (c == ' ' || c == '=' || c == '"' || c == '\t') {
            needs = true;
            break;
        }
    }
    if (!needs) {
        return v;
    }
    std::string out;
    out.reserve(v.size() + 2);
    out.push_back('"');
    for (char c : v) {
        if (c == '"' || c == '\\') {
            out.push_back('\\');
        }
        out.push_back(c);
    }
    out.push_back('"');
    return out;
}

inline std::string timestamp() {
    using namespace std::chrono;
    auto now = system_clock::now();
    auto t = system_clock::to_time_t(now);
    auto ms = duration_cast<milliseconds>(now.time_since_epoch()).count() % 1000;
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tm);
    std::ostringstream os;
    os << buf << '.';
    os.width(3);
    os.fill('0');
    os << ms << 'Z';
    return os.str();
}

} // namespace detail

using LogFields = std::initializer_list<std::pair<std::string, std::string>>;

inline void log(LogLevel level, const std::string &event, LogFields fields = {}) {
    std::ostringstream os;
    os << "ts=" << detail::timestamp()
       << " level=" << to_string(level)
       << " source=router"
       << " event=" << detail::quote_if_needed(event);
    for (const auto &kv : fields) {
        os << ' ' << kv.first << '=' << detail::quote_if_needed(kv.second);
    }
    std::lock_guard<std::mutex> lk(detail::log_mutex());
    std::cerr << os.str() << '\n';
}

struct Log {
    static void debug(const std::string &e, LogFields f = {}) { log(LogLevel::Debug, e, f); }
    static void info(const std::string &e, LogFields f = {}) { log(LogLevel::Info, e, f); }
    static void warn(const std::string &e, LogFields f = {}) { log(LogLevel::Warn, e, f); }
    static void error(const std::string &e, LogFields f = {}) { log(LogLevel::Error, e, f); }
};

} // namespace router
