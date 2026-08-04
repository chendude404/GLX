"""Build fixtures for the GLX test suite.

The C sources are compiled fresh into tests/_build/ at session start -- both as a
shared library (so individual pipeline stages can be exercised through ctypes)
and as the two CLI binaries (so the container format can be tested end to end).

Nothing here touches the checked-in glx_encode.exe / glx_decode.exe; tests always
run against a build of the current sources.
"""

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

GLX_DIR = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = GLX_DIR / "src"
GEN_DIR = SRC_DIR / "generated"
BUILD_DIR = pathlib.Path(__file__).resolve().parent / "_build"

# Every pipeline stage; encoder.c/decoder.c are excluded because they carry main().
MAINS = {"encoder.c", "decoder.c"}
COMPONENTS = sorted(p.name for p in SRC_DIR.glob("*.c") if p.name not in MAINS)

CFLAGS = ["-O2", "-Wall", "-Wextra", "-std=c11",
          "-I%s" % SRC_DIR, "-I%s" % GEN_DIR]


def _find_cc():
    for name in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if name and shutil.which(name):
            return shutil.which(name)
    return None


def _shared_lib_name():
    if sys.platform == "win32":
        return "glx.dll"
    if sys.platform == "darwin":
        return "libglx.dylib"
    return "libglx.so"


def _exe(name):
    return name + ".exe" if sys.platform == "win32" else name


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "build failed:\n  %s\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (" ".join(cmd), proc.stdout, proc.stderr)
        )
    return proc


@pytest.fixture(scope="session")
def cc():
    compiler = _find_cc()
    if compiler is None:
        pytest.skip("no C compiler found (set CC, or install gcc/clang)")
    return compiler


@pytest.fixture(scope="session")
def shared_lib(cc):
    """Compile the pipeline stages into a shared library, return its path."""
    BUILD_DIR.mkdir(exist_ok=True)
    out = BUILD_DIR / _shared_lib_name()

    cmd = [cc] + CFLAGS + ["-shared"]
    if sys.platform != "win32":
        cmd += ["-fPIC"]
    else:
        # keep the DLL loadable by a stock CPython that has no MSYS libs on PATH
        cmd += ["-static-libgcc"]
    cmd += ["-o", str(out)] + [str(SRC_DIR / c) for c in COMPONENTS]

    _run(cmd)
    return out


@pytest.fixture(scope="session")
def glx(shared_lib):
    """ctypes binding to the compiled pipeline (see glxlib.GlxLib)."""
    from glxlib import GlxLib
    return GlxLib(shared_lib)


@pytest.fixture(scope="session")
def cli(cc):
    """Build glx_encode / glx_decode, return (encode_path, decode_path)."""
    BUILD_DIR.mkdir(exist_ok=True)
    paths = []
    for main_src, exe_name in (("encoder.c", "glx_encode"), ("decoder.c", "glx_decode")):
        out = BUILD_DIR / _exe(exe_name)
        cmd = ([cc] + CFLAGS + ["-o", str(out), str(SRC_DIR / main_src)]
               + [str(SRC_DIR / c) for c in COMPONENTS])
        _run(cmd)
        paths.append(out)
    return tuple(paths)


# make glxlib / glxref importable without installing anything
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
