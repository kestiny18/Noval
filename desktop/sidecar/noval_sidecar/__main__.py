import sys

from desktop.sidecar.noval_sidecar.server import SidecarServer


def main() -> int:
    return SidecarServer(sys.stdin.buffer, sys.stdout.buffer).serve()


if __name__ == "__main__":
    raise SystemExit(main())
