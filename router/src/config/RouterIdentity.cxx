#include "config/RouterIdentity.hpp"

#include <fstream>
#include <sstream>
#include <string>

namespace router {
namespace {

std::string strip_inline_comment(const std::string &s) {
    // Drop a trailing " # ..." comment (space before '#'); leaves a leading '#'
    // (whole-line comment) for the caller to detect. Values here never contain '#'.
    std::string::size_type pos = s.find(" #");
    if (pos != std::string::npos) {
        return s.substr(0, pos);
    }
    return s;
}

std::string trim(const std::string &s) {
    std::string::size_type b = s.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) {
        return "";
    }
    std::string::size_type e = s.find_last_not_of(" \t\r\n");
    return s.substr(b, e - b + 1);
}

std::string unquote(const std::string &s) {
    if (s.size() >= 2 &&
        ((s.front() == '"' && s.back() == '"') || (s.front() == '\'' && s.back() == '\''))) {
        return s.substr(1, s.size() - 2);
    }
    return s;
}

std::size_t indent_of(const std::string &line) {
    std::size_t n = 0;
    while (n < line.size() && line[n] == ' ') {
        ++n;
    }
    return n;
}

} // namespace

bool load_identity(const std::string &path, RouterIdentity &out, std::string &error) {
    std::ifstream in(path);
    if (!in.is_open()) {
        error = "cannot open config file: " + path;
        return false;
    }

    std::string section; // current top-level block: "node", "router", or other
    std::string raw;
    while (std::getline(in, raw)) {
        std::string line = strip_inline_comment(raw);
        std::string content = trim(line);
        if (content.empty() || content[0] == '#') {
            continue;
        }

        if (indent_of(line) == 0) {
            // New top-level key. We only care about "node:" and "router:"; any other
            // top-level key (control:, participants:, routes:, ...) ends capture.
            std::string::size_type colon = content.find(':');
            section = (colon == std::string::npos) ? content : trim(content.substr(0, colon));
            continue;
        }

        if (section != "node" && section != "router") {
            continue;
        }

        std::string::size_type colon = content.find(':');
        if (colon == std::string::npos) {
            continue; // e.g. a nested list item; not an identity field
        }
        std::string key = trim(content.substr(0, colon));
        std::string value = unquote(trim(content.substr(colon + 1)));
        if (value.empty()) {
            continue;
        }

        if (section == "node") {
            if (key == "name") {
                out.node_name = value;
            } else if (key == "role") {
                out.node_role = value;
            }
        } else { // router
            if (key == "name") {
                out.router_name = value;
            } else if (key == "config_set") {
                out.config_set = value;
            } else if (key == "default_forwarding_mode") {
                out.default_forwarding_mode = value;
            } else if (key == "id") {
                // Stale-config guard (D79): router.id is retired; hard error so a
                // stale fleet config cannot hide behind a silently ignored field.
                error = "router.id is retired (D79: the router name is the only "
                        "identity) — remove it from " + path;
                return false;
            }
        }
    }

    if (out.node_name.empty()) {
        error = "missing required field node.name in " + path;
        return false;
    }
    if (out.router_name.empty()) {
        out.router_name = "router"; // fleet-wide default (D79 addendum/D80)
    }
    return true;
}

} // namespace router
