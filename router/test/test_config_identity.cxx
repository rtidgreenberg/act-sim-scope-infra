// test_config_identity.cxx — no-DDS unit test for the Phase-0 config identity reader.
//
// Verifies the shipped sample configs parse to the expected identity, that the role-aware
// / team configs are distinguished by router.name + config_set, and that a missing file
// and an identity-incomplete file are reported as failures. Dependency-free: a tiny
// check() harness, exit code 0 == all passed.
//
// Runtime hygiene (see .github/copilot-instructions.md): the negative-case fixtures are
// written under /tmp (local ext4), never the vboxsf share.

#include "config/RouterIdentity.hpp"

#include <cstdio>
#include <fstream>
#include <string>
#include <unistd.h>

#ifndef ROUTER_SAMPLE_DIR
#define ROUTER_SAMPLE_DIR "."
#endif

static int g_failures = 0;

#define CHECK(cond)                                                                      \
    do {                                                                                 \
        if (!(cond)) {                                                                   \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);         \
            ++g_failures;                                                                \
        }                                                                                \
    } while (0)

#define CHECK_EQ(actual, expected)                                                       \
    do {                                                                                 \
        auto a_ = (actual);                                                              \
        auto e_ = (expected);                                                            \
        if (!(a_ == e_)) {                                                               \
            std::fprintf(stderr, "FAIL %s:%d  %s == %s  (got \"%s\")\n", __FILE__,        \
                         __LINE__, #actual, #expected, std::string(a_).c_str());         \
            ++g_failures;                                                                \
        }                                                                                \
    } while (0)

static std::string sample(const char *name) {
    return std::string(ROUTER_SAMPLE_DIR) + "/" + name;
}

int main() {
    using router::load_identity;
    using router::RouterIdentity;

    // 1. control-platform sample parses to the expected identity.
    {
        RouterIdentity id;
        std::string err;
        bool ok = load_identity(sample("control-platform.yaml"), id, err);
        CHECK(ok);
        CHECK_EQ(id.node_name, std::string("Platform_30"));
        CHECK_EQ(id.node_role, std::string("platform"));
        CHECK(id.router_id == 30);
        CHECK_EQ(id.router_name, std::string("platform-30-control-platform"));
        CHECK_EQ(id.config_set, std::string("control-platform"));
        CHECK_EQ(id.default_forwarding_mode, std::string("dynamic_data"));
    }

    // 2. platform-team sample is a distinct router instance on the same node.
    {
        RouterIdentity id;
        std::string err;
        bool ok = load_identity(sample("platform-team.yaml"), id, err);
        CHECK(ok);
        CHECK_EQ(id.node_name, std::string("Platform_30"));
        CHECK_EQ(id.router_name, std::string("platform-30-team"));
        CHECK_EQ(id.config_set, std::string("platform-team"));
    }

    // 3. missing file is a reported failure, not a crash.
    {
        RouterIdentity id;
        std::string err;
        bool ok = load_identity("/nonexistent/does-not-exist.yaml", id, err);
        CHECK(!ok);
        CHECK(!err.empty());
    }

    // 4. inline comments stripped, quotes handled, required-field validation.
    {
        std::string path = "/tmp/router_p0_test_" + std::to_string(getpid()) + ".yaml";
        {
            std::ofstream f(path);
            f << "node:\n"
              << "  name: \"Control_1\"   # quoted + inline comment\n"
              << "  role: control\n"
              << "router:\n"
              << "  id: 1\n"
              << "  name: control-1-control-platform\n";
        }
        RouterIdentity id;
        std::string err;
        bool ok = load_identity(path, id, err);
        CHECK(ok);
        CHECK_EQ(id.node_name, std::string("Control_1"));
        CHECK_EQ(id.node_role, std::string("control"));
        CHECK(id.router_id == 1);
        std::remove(path.c_str());

        // missing router.name -> failure
        std::string bad = "/tmp/router_p0_test_bad_" + std::to_string(getpid()) + ".yaml";
        {
            std::ofstream f(bad);
            f << "node:\n  name: X\n  role: control\n";
        }
        RouterIdentity id2;
        std::string err2;
        CHECK(!load_identity(bad, id2, err2));
        CHECK(!err2.empty());
        std::remove(bad.c_str());
    }

    if (g_failures == 0) {
        std::printf("test_config_identity: OK\n");
        return 0;
    }
    std::fprintf(stderr, "test_config_identity: %d failure(s)\n", g_failures);
    return 1;
}
