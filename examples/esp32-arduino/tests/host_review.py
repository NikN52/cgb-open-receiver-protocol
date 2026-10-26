"""Compile portable firmware math and validate its emitted wire data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def find_compiler(explicit: str | None) -> Path:
    candidates = [explicit, os.environ.get("CXX"), shutil.which("g++"), shutil.which("clang++")]
    home = Path.home()
    candidates.extend(home.glob(".platformio/packages/toolchain-gccmingw32/bin/g++.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise RuntimeError("no host C++ compiler found; pass --compiler or set CXX")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler")
    args = parser.parse_args()
    compiler = find_compiler(args.compiler)
    environment = os.environ.copy()
    environment["PATH"] = str(compiler.parent) + os.pathsep + environment.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="openrx-host-") as directory:
        binary = Path(directory) / ("imu_host.exe" if os.name == "nt" else "imu_host")
        command = [str(compiler), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                   "-I", str(ROOT / "include"), str(Path(__file__).with_name("imu_host.cpp")),
                   "-o", str(binary)]
        subprocess.run(command, check=True, env=environment)
        result = subprocess.run([str(binary)], text=True, capture_output=True, env=environment)
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr).strip() or
                               f"host executable failed with {result.returncode}")

    sys.path.insert(0, str(REPO / "tools"))
    from protocol import ImuClock, StreamDecoder  # pylint: disable=import-error

    wire = [line + "\r\n" for line in result.stdout.splitlines() if line.startswith("$PCGBI,")]
    if not wire:
        raise AssertionError("host executable emitted no PCGBI fixture")
    decoder = StreamDecoder()
    parsed = decoder.feed("".join(wire).encode("ascii"))
    if len(parsed) != len(wire) or decoder.errors:
        raise AssertionError("Python protocol decoder rejected C++ PCGBI output")
    clock = ImuClock()
    if any(clock.accept(line, index * 20) not in {"boundary", "step"}
           for index, line in enumerate(parsed)):
        raise AssertionError("Python IMU clock rejected C++ PCGBI output")
    for line in parsed:
        if line.name != "PCGBI":
            raise AssertionError("unexpected C++ wire sentence")
    if "imu host review: PASS" not in result.stdout:
        raise AssertionError("host executable did not complete all checks")
    print(f"host review: PASS ({compiler})")


if __name__ == "__main__":
    main()
