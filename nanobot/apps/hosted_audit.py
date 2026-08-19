"""Prune local-only surfaces and verify the Hosted Worker distribution boundary."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import shutil
from pathlib import Path

FORBIDDEN_DISTRIBUTIONS = {
    "discord.py",
    "lark-oapi",
    "matrix-nio",
    "neonize",
    "qq-botpy",
    "slack-sdk",
    "python-telegram-bot",
}
LOCAL_ONLY_PACKAGES = ("channels", "webui", "sagasmith_local", "cli", "apps/cli")


def nanobot_root() -> Path:
    spec = importlib.util.find_spec("nanobot")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("installed nanobot package is unavailable")
    return Path(next(iter(spec.submodule_search_locations))).resolve()


def prune_local_surfaces(root: Path) -> None:
    for name in LOCAL_ONLY_PACKAGES:
        target = (root / name).resolve()
        if target == root or root not in target.parents:
            raise RuntimeError(f"unsafe Hosted prune target: {target}")
        if target.is_dir():
            shutil.rmtree(target)


def verify(root: Path) -> None:
    present = {
        distribution.metadata["Name"].casefold()
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    forbidden = sorted(name for name in FORBIDDEN_DISTRIBUTIONS if name.casefold() in present)
    if forbidden:
        raise RuntimeError("Hosted image contains Channel SDKs: " + ", ".join(forbidden))
    residual = sorted(name for name in LOCAL_ONLY_PACKAGES if (root / name).exists())
    if residual:
        raise RuntimeError("Hosted image contains local-only packages: " + ", ".join(residual))
    if (root / "web" / "dist").exists():
        raise RuntimeError("Hosted image contains built WebUI assets")
    for required in (
        root / "agent" / "loop.py",
        root / "agent" / "tools" / "mcp.py",
        root / "agent" / "tools" / "structured_output.py",
        root / "apps" / "hosted_worker.py",
        root / "config" / "schema.py",
        root / "session" / "manager.py",
    ):
        if not required.is_file():
            raise RuntimeError(f"Hosted image is missing required Agent Core: {required}")


def verify_runtime_imports() -> None:
    """Prove the pruned package still imports the Hosted Worker runtime graph."""
    from nanobot.agent.context import ContextBuilder  # noqa: F401
    from nanobot.agent.loop import AgentLoop  # noqa: F401
    from nanobot.agent.tools.context import ToolContext
    from nanobot.agent.tools.loader import ToolLoader
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.apps.hosted_worker import create_worker_app  # noqa: F401
    from nanobot.config.schema import ToolsConfig

    context = ToolContext(
        config=ToolsConfig(distribution="hosted"),
        workspace="/nonexistent-hosted-workspace",
    )
    registry = ToolRegistry()
    registered = ToolLoader().load(context, registry)
    if registered or registry.tool_names:
        raise RuntimeError(
            "Hosted runtime exposed local or privileged tools: "
            + ", ".join(sorted(set(registered + registry.tool_names)))
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prune", action="store_true")
    arguments = parser.parse_args()
    root = nanobot_root()
    if arguments.prune:
        prune_local_surfaces(root)
    verify(root)
    verify_runtime_imports()


if __name__ == "__main__":
    main()
