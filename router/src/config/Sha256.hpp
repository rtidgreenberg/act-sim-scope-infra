// Sha256.hpp — self-contained SHA-256 for the D80 config_hash.
//
// The router stamps a digest of the loaded config file into its RouterHealth heartbeat
// so C2 can spot configuration drift mesh-wide (D80; pinned in the D79 addendum:
// SHA-256 over the RAW BYTES of the config file, full lowercase-hex digest, 64 chars,
// computed once at load). Deliberately dependency-free (FIPS 180-4 reference
// implementation) — the router links only Connext and yaml-cpp, and a crypto library
// is not worth adding for a drift fingerprint.

#pragma once

#include <cstddef>
#include <string>

namespace router {

// Full lowercase-hex SHA-256 digest (64 chars) of `len` bytes at `data`.
std::string sha256_hex(const void *data, std::size_t len);

inline std::string sha256_hex(const std::string &bytes) {
    return sha256_hex(bytes.data(), bytes.size());
}

} // namespace router
