#!/usr/bin/env python3

import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("verify_deck_cmake_cache.sh")
EXPECTED = "-O2 -g -DNDEBUG"


class VerifyDeckCmakeCacheTests(unittest.TestCase):
    def run_validator(self, contents: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = pathlib.Path(temp_dir) / "CMakeCache.txt"
            cache.write_text(contents, encoding="utf-8")
            return subprocess.run(
                [str(SCRIPT), str(cache)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_accepts_exact_c_and_cxx_flags(self) -> None:
        result = self.run_validator(
            f"CMAKE_C_FLAGS_RELWITHDEBINFO:STRING={EXPECTED}\n"
            f"CMAKE_CXX_FLAGS_RELWITHDEBINFO:STRING={EXPECTED}\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validation passed", result.stdout)

    def test_rejects_empty_c_flags(self) -> None:
        result = self.run_validator(
            "CMAKE_C_FLAGS_RELWITHDEBINFO:STRING=\n"
            f"CMAKE_CXX_FLAGS_RELWITHDEBINFO:STRING={EXPECTED}\n"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("CMAKE_C_FLAGS_RELWITHDEBINFO", result.stderr)

    def test_rejects_empty_cxx_flags(self) -> None:
        result = self.run_validator(
            f"CMAKE_C_FLAGS_RELWITHDEBINFO:STRING={EXPECTED}\n"
            "CMAKE_CXX_FLAGS_RELWITHDEBINFO:STRING=\n"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("CMAKE_CXX_FLAGS_RELWITHDEBINFO", result.stderr)

    def test_rejects_drifted_optimization_level(self) -> None:
        result = self.run_validator(
            "CMAKE_C_FLAGS_RELWITHDEBINFO:STRING=-O0 -g\n"
            "CMAKE_CXX_FLAGS_RELWITHDEBINFO:STRING=-O0 -g\n"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected", result.stderr)


if __name__ == "__main__":
    unittest.main()
