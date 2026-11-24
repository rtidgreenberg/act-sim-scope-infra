#!/usr/bin/env python3
"""Build privately and compare the current monitored union processor."""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import os
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "connext:7.7.0"
LICENSE = Path(os.environ.get("CONNEXT_SHARED_DIR",
                              "/home/dgreenberg/rti_connext_dds-7.7.0"))


def docker(arguments):
    return subprocess.run(["sg", "docker", "-c", shlex.join(["docker", *arguments])],
                          check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-accounting", action="store_true",
                        help="Measure compression and flow-controller accounting instead of the full matrix")
    args = parser.parse_args()
    if not (LICENSE / "rti_license.dat").is_file():
        raise FileNotFoundError("CONNEXT_SHARED_DIR must contain rti_license.dat")
    docker(["image", "inspect", IMAGE, "--format", "{{.Id}}"])
    prefix = "flow_token_accounting" if args.token_accounting else "full_union_compression"
    results = ROOT / "debug/test_reports" / datetime.now(timezone.utc).strftime(
        prefix + "_%Y%m%dT%H%M%SZ")
    results.mkdir()
    print("RESULTS=" + str(results), flush=True)
    with tempfile.TemporaryDirectory(prefix="full-union-compression-") as temporary:
        work = Path(temporary)
        work.chmod(0o777)
        common = ["run", "--rm", "--network", "none", "--ipc", "private",
                  "-v", str(ROOT) + ":/workspace:ro",
                  "-v", str(work) + ":/work",
                  "-v", str(results) + ":/results",
                  "-v", str(LICENSE) + ":/shared:ro", "-w", "/workspace",
                  "-e", "NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0",
                  "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
                  "-e", "PYTHONDONTWRITEBYTECODE=1"]
        build = (
            "cmake -S router -B /work/build -DBUILD_ROUTING_SERVICE_VARIANT=ON "
            "-DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0 "
            "-DFETCHCONTENT_SOURCE_DIR_YAML-CPP=/workspace/router/build/_deps/yaml-cpp-src "
            "&& cmake --build /work/build --target act_gateway_processor act_rs_config "
            "test_full_type_bindings test_admin_types -j2 "
            "&& ROUTER_BUILD_DIR=/work/build bash router/run_tests.sh "
            "-R 'admin_types|full_type_bindings'")
        with (results / "build.log").open("w") as output:
            subprocess.run(["sg", "docker", "-c", shlex.join([
                "docker", *common, "--user", f"{os.getuid()}:{os.getgid()}",
                "--tmpfs", "/workspace/tmp:exec,mode=1777", IMAGE, "sh", "-c", build])],
                stdout=output, stderr=subprocess.STDOUT, check=True)
        docker([*common, "--hostname", "localhost", "--cap-add", "NET_RAW",
                "--cap-add", "NET_ADMIN", "--tmpfs", "/tmp:exec", IMAGE,
                "python3", "spikes/full_union_compression/" +
                ("token_accounting.py" if args.token_accounting else "runner.py")])


if __name__ == "__main__":
    main()
