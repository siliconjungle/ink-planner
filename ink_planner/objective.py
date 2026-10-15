"""Offline authored-goal selection over an already checked compiled portfolio.

The ordinary compiled Ink function and the runtime's shared Wasm ABI decide.
This command does not admit plans, fit models, or run application actions.
Compiled modules/host adapters are trusted executable deployment inputs.
"""
import argparse
import subprocess
from pathlib import Path


def select(host_dir, goal_module, adaptive, configuration, output, previous_goal=None, node="node"):
    paths = [Path(x).resolve() for x in (host_dir, goal_module, adaptive, configuration)]
    host, module, request, config = paths
    for path in (request, config, *([Path(previous_goal)] if previous_goal else [])):
        if path.stat().st_size > 4_000_000:
            raise ValueError("objective input byte budget")
    worker = Path(__file__).with_suffix(".mjs")
    result = subprocess.run([node, str(worker), str(host), str(module), str(request), str(config),
        str(Path(previous_goal).resolve()) if previous_goal else "-", str(Path(output).resolve())],
        capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise ValueError(result.stderr[-4000:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host-dir", "goal-module", "adaptive", "configuration"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--previous-goal", type=Path)
    parser.add_argument("--node", default="node")
    parser.add_argument("-o", type=Path, required=True)
    args = parser.parse_args()
    select(args.host_dir, args.goal_module, args.adaptive, args.configuration, args.o,
        args.previous_goal, args.node)


if __name__ == "__main__":
    main()
