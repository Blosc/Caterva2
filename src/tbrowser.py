###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

# Minimal example on browsing a tree of datasets/files

import pathlib

# Project
from caterva2 import utils

from textual.app import App, ComposeResult
from textual.widgets import Tree


class TreeApp(App):

    def __init__(self, args):
        super().__init__()
        self.root = args.root
        self.data = utils.get(f'http://{args.host}/api/list/{args.root}')

    def compose(self) -> ComposeResult:
        path = self.root / pathlib.Path(self.data[0])
        root, _ = path.parts
        tree: Tree[dict] = Tree(root)
        tree.root.expand()
        datasets = tree.root.add("Datasets", expand=True)
        files = tree.root.add("Files", expand=True)
        for dataset in self.data:
            path = self.root / pathlib.Path(dataset)
            _, *parts = path.parts
            if dataset.endswith((".b2nd", ".b2frame")):
                datasets.add_leaf("/".join(parts))
            else:
                files.add_leaf("/".join(parts))
        yield tree


if __name__ == "__main__":
    parser = utils.get_parser()
    parser.add_argument('--host', default='localhost:8002')
    parser.add_argument('--root', default='foo')

    # Go
    args = utils.run_parser(parser)
    app = TreeApp(args)
    app.run()
