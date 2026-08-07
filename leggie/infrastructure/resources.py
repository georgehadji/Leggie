"""ResourceLocator — resolves packaged and writable resource paths.

Facade over ``importlib.resources`` for read-only packaged resources
(config routes, citation indexes) plus a strategy for user-writable
output paths. Replaces hardcoded path literals throughout the codebase
(PROD-07).

Usage::

    locator = ResourceLocator()
    routes_path = locator.package_resource("leggie.config", "routes.yaml")
    output_dir = locator.writable_path("Outputs", cli_override=args.output)

Two strategies:
    * **Packaged** — read-only, via ``importlib.resources`` / ``importlib.resources.files``.
    * **User-writable** — explicit ``--output-dir``, or default ``./Outputs/``.
"""

from __future__ import annotations

import importlib.resources as _resources
from pathlib import Path
from typing import Literal


class ResourceLocator:
    """Resolves resource paths — packaged (read-only) or writable (user override)."""

    def __init__(
        self,
        default_output: str = "Outputs",
    ) -> None:
        self._default_output = default_output

    # ── Packaged (read-only) resources ──────────────────────────────

    def package_resource(self, package: str, resource: str) -> Path:
        """Resolve the filesystem path to a packaged resource.

        Args:
            package: Dotted package name (e.g. ``"leggie.config"``).
            resource: File name within that package (e.g. ``"routes.yaml"``).

        Returns:
            Absolute ``Path`` to the resource.
        """
        # Try filesystem-based resolution first (works for editable installs
        # and packages that haven't been installed yet, like leggie.data).
        try:
            pkg = __import__(package, fromlist=["__name__"])
            # Namespace packages have __file__ = None, so this is a real case,
            # not defensive padding — TypeError used to catch it at runtime.
            pkg_file = getattr(pkg, "__file__", None)
            if pkg_file is not None:
                candidate = Path(pkg_file).resolve().parent / resource
                if candidate.exists():
                    return candidate
        except (ImportError, AttributeError, TypeError):
            pass
        # Fallback to importlib.resources (works for installed packages).
        # files() yields a Traversable, which is only a Path for filesystem-backed
        # loaders; str() round-trips the one case this function supports and keeps
        # the declared -> Path contract honest.
        try:
            return Path(str(_resources.files(package) / resource))
        except (AttributeError, ModuleNotFoundError, TypeError):
            return Path(package.replace(".", "/")) / resource

    def routes_path(self, override: str | None = None) -> Path:
        """Resolve ``routes.yaml``, honouring ``CascadeSettings.rules_path``."""
        if override:
            return Path(override)
        return self.package_resource("leggie.config", "routes.yaml")

    # ── Writable (user) paths ───────────────────────────────────────

    def writable_path(self, subpath: str = "", cli_override: str | Path | None = None) -> Path:
        """Resolve a user-writable output directory.

        Args:
            subpath: Relative subdirectory under the output root.
            cli_override: Explicit CLI ``--output`` argument.

        Returns:
            Absolute ``Path``, created if it does not exist.
        """
        root = Path(cli_override) if cli_override else Path(self._default_output)
        if subpath:
            root = root / subpath
        root.mkdir(parents=True, exist_ok=True)
        return root

    def checkpoint_path(self, stem: str = "leggie_checkpoint.json") -> Path:
        """Default checkpoint location under the writable output directory."""
        return self.writable_path() / stem

    # ── Hints for pyproject.toml package-data ───────────────────────
    @staticmethod
    def required_package_data() -> list[dict[str, list[str]]]:
        """Return the ``[tool.setuptools.package-data]`` entries needed.

        These should be added to ``pyproject.toml`` so that ``pip install -e .``
        includes the packaged data files.
        """
        return [
            {"leggie.config": ["routes.yaml"]},
            {"leggie.data": ["citation_index.json", "*.json"]},
        ]


# Module-level singleton for convenience
_default_locator: ResourceLocator | None = None


def get_locator(
    default_output: str = "Outputs",
    strategy: Literal["singleton", "fresh"] = "singleton",
) -> ResourceLocator:
    """Get the default ``ResourceLocator``.

    Args:
        default_output: Default output directory name.
        strategy: ``"singleton"`` (default) reuses a module-level instance;
            ``"fresh"`` creates a new one (for testing).
    """
    global _default_locator
    if strategy == "fresh":
        return ResourceLocator(default_output=default_output)
    if _default_locator is None:
        _default_locator = ResourceLocator(default_output=default_output)
    return _default_locator
