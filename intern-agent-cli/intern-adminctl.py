#!/usr/bin/env python3
"""Administrator CLI for enterprise-only local operations.

This entry point is intentionally separate from ``internctl`` so regular users
cannot accidentally run relay lifecycle operations hidden behind the GUI.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="intern-adminctl",
        description="Manage administrator-only Intern Agents services",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    from commands import feishu_admin, relay

    feishu_admin.setup_parser(subparsers)
    relay.setup_parser(subparsers)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)
    if hasattr(args, "func"):
        sys.exit(args.func(args, enforce_enterprise_boundary=False))
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
