"""Build the 4 MB candidate with the existing local ESP-IDF; never flash a board."""

import argparse
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--sdkconfig", type=Path)
    parser.add_argument("--profile-dir", type=Path, help="Opt-in synthetic latency test vectors")
    parser.add_argument("--no-stage-clocks", action="store_true", help="Compile benchmark without model probes")
    parser.add_argument("--integer-fc", action="store_true", help="Experimental INT8 input / INT32 FC benchmark")
    parser.add_argument("--simd-fc", action="store_true", help="Use the native S3 16-lane dot in the integer benchmark")
    parser.add_argument("--blocked-fc", action="store_true", help="Reuse FP32 inputs across four FC rows")
    parser.add_argument(
        "--model-o3", action="store_true", help="Compile only the model kernel with -O3"
    )
    parser.add_argument("--size", action="store_true")
    args = parser.parse_args()
    model_dir = args.model_dir.resolve()
    if not (model_dir / "manifest.json").is_file():
        parser.error("export_autoncp.py must generate the model directory first")
    if args.profile_dir and not (args.profile_dir / "profile_vectors.h").is_file():
        parser.error("profile_autoncp.py prepare must generate profile_vectors.h first")
    if args.integer_fc and (not args.profile_dir or not (args.profile_dir / "integer_self_test.h").is_file()):
        parser.error("--integer-fc requires profile_autoncp.py prepare --integer-fc vectors")
    if args.simd_fc and not args.integer_fc:
        parser.error("--simd-fc requires --integer-fc")
    if args.blocked_fc and args.integer_fc:
        parser.error("--blocked-fc requires FP32 activations")
    idf = Path("C:/esp/v6.0.2/esp-idf")
    installed = Path("C:/Espressif")
    python_env = installed / "tools/python/v6.0.2/venv"
    python = python_env / "Scripts/python.exe"
    tool_bins = [
        python_env / "Scripts",
        installed / "tools/cmake/4.0.3/bin",
        installed / "tools/ninja/1.12.1",
        installed / "tools/xtensa-esp-elf/esp-15.2.0_20251204/xtensa-esp-elf/bin",
    ]
    env = dict(os.environ)
    env.update(
        IDF_PATH=str(idf),
        IDF_TOOLS_PATH=str(installed),
        ESP_IDF_VERSION="6.0.2",
        IDF_PYTHON_ENV_PATH=str(python_env),
        IDF_PYTHON_CHECK_CONSTRAINTS="no",
    )
    # Installed manager environment has no constraints snapshot. IDF still checks
    # its installed Python requirements; no package installation is performed.
    env["PATH"] = (
        os.pathsep.join(map(str, tool_bins)) + os.pathsep + env.get("PATH", "")
    )
    entry = int(env.get("GIT_CONFIG_COUNT", "0"))
    env.update(
        {
            "GIT_CONFIG_COUNT": str(entry + 1),
            f"GIT_CONFIG_KEY_{entry}": "safe.directory",
            f"GIT_CONFIG_VALUE_{entry}": idf.as_posix(),
        }
    )
    print(
        "Building a candidate requiring >=4 MB Flash.",
        flush=True,
    )
    command = [
        str(python),
        str(idf / "tools/idf.py"),
        "--no-ccache",
        "-DIDF_TARGET=esp32s3",
        f"-DMODEL_DIR={model_dir.as_posix()}",
        f"-DAUTONCP_MODEL_O3={'ON' if args.model_o3 else 'OFF'}",
        f"-DAUTONCP_PROFILE_DIR={args.profile_dir.resolve().as_posix() if args.profile_dir else ''}",
        f"-DAUTONCP_STAGE_CLOCKS={'OFF' if args.no_stage_clocks else 'ON'}",
        f"-DAUTONCP_INTEGER_FC={'ON' if args.integer_fc else 'OFF'}",
        f"-DAUTONCP_INTEGER_SIMD={'ON' if args.simd_fc else 'OFF'}",
        f"-DAUTONCP_BLOCKED_FC={'ON' if args.blocked_fc else 'OFF'}",
    ]
    if args.build_dir:
        command.extend(["-B", str(args.build_dir.resolve())])
    if args.sdkconfig:
        command.append(f"-DSDKCONFIG={args.sdkconfig.resolve().as_posix()}")
    command.append("build")
    if args.size:
        command.append("size")
    subprocess.run(command, cwd=Path(__file__).resolve().parent, env=env, check=True)


if __name__ == "__main__":
    main()
