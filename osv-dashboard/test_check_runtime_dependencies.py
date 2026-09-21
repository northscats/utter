import unittest

from check_runtime_dependencies import unsafe_nix_packages


class NixRuntimeDependencyTests(unittest.TestCase):
    def test_runtime_document_tools_are_allowed(self):
        config = {
            "nix": {
                "packages": ["libreoffice", "tesseract", "poppler-utils"],
            }
        }

        self.assertEqual(unsafe_nix_packages(config), [])

    def test_development_only_packages_are_rejected(self):
        config = {
            "nix": {
                "packages": [
                    "rustc",
                    "cargo",
                    "playwright-driver",
                    "git-svn",
                    "gcc",
                    "clang",
                    "llvmPackages",
                    "libclang",
                    "cmake",
                ],
            }
        }

        failures = unsafe_nix_packages(config)

        self.assertEqual(
            [package for package, _reason in failures],
            [
                "rustc",
                "cargo",
                "playwright-driver",
                "git-svn",
                "gcc",
                "clang",
                "llvmPackages",
                "libclang",
                "cmake",
            ],
        )

    def test_versioned_development_package_is_rejected(self):
        config = {"nix": {"packages": ["gcc-14", "rust-bin"]}}

        self.assertEqual(
            [package for package, _reason in unsafe_nix_packages(config)],
            ["gcc-14", "rust-bin"],
        )


if __name__ == "__main__":
    unittest.main()