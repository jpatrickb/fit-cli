import sys

GLOBAL_FLAGS = {"--toon", "--table"}
GLOBAL_OPTIONS = {"--db"}  # take a value


def _hoist_global_options(argv: list[str]) -> list[str]:
    """Let --toon / --table / --db go anywhere (`fit gym show --table`), not only before the command."""
    front, rest, i = [], [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            rest += argv[i:]
            break
        if arg in GLOBAL_FLAGS or any(arg.startswith(o + "=") for o in GLOBAL_OPTIONS):
            front.append(arg)
        elif arg in GLOBAL_OPTIONS and i + 1 < len(argv):
            front += [arg, argv[i + 1]]
            i += 1
        else:
            rest.append(arg)
        i += 1
    return front + rest


def main() -> None:
    from fit.cli import app

    app(args=_hoist_global_options(sys.argv[1:]), prog_name="fit")
