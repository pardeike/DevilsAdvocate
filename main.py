#!/usr/bin/env python3
"""DevilsAdvocate stdio MCP or owned tmux worker."""
import argparse
import os
from pathlib import Path

from devils_advocate.server import MCP
from devils_advocate.worker import run


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--caller', choices=['codex', 'claude'])
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('mode', nargs='?', choices=['worker'])
    parser.add_argument('folder', nargs='?', type=Path)
    args = parser.parse_args()
    if args.mode == 'worker':
        if not args.folder:
            parser.error('worker requires its owned session folder')
        run(args.folder)
    else:
        MCP(args.caller, args.data_dir).serve()


if __name__ == '__main__':
    main()
