###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import contextlib
import logging
import pathlib

# Requirements
import blosc2
from fastapi import FastAPI, responses
import uvicorn
from watchfiles import Change, awatch

# Project
import utils


logger = logging.getLogger('pub')

# Configuration
broker = None
name = None
root = None
nworkers = 1

# State
cache = None
client = None


async def worker(queue):
    while True:
        abspath, change = await queue.get()
        with utils.log_exception(logger, 'Publication failed'):
            if abspath.is_file():
                relpath = pathlib.Path(abspath).relative_to(root)

                # Load metadata
                if abspath.suffix in {'.b2frame', '.b2nd'}:
                    metadata = utils.read_metadata(abspath)
                else:
                    # Compress regular files in publisher's cache
                    b2path = cache / f'{relpath}.b2'
                    utils.compress(abspath, b2path)
                    metadata = utils.read_metadata(b2path)

                # Publish
                metadata = metadata.model_dump()
                data = {'change': change.name, 'path': relpath, 'metadata': metadata}
                await client.publish(name, data=data)

        queue.task_done()


async def watchfiles(queue):
    # Notify the network about available datasets
    # TODO Notify only about changes from previous run, for this purpose we need to
    # persist state
    for path, relpath in utils.walk_files(root):
        queue.put_nowait((path, Change.added))

    # Watch directory for changes
    async for changes in awatch(root):
        for change, path in changes:
            queue.put_nowait((path, change))
    print('THIS SHOULD BE PRINTED ON CTRL+C')


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect to broker
    global client
    client = utils.start_client(f'ws://{broker}/pubsub')

    # Create queue and start workers
    queue = asyncio.Queue()
    tasks = []
    for i in range(nworkers):
        task = asyncio.create_task(worker(queue))
        tasks.append(task)

    # Watch dataset files (must wait before publishing)
    await client.wait_until_ready()
    asyncio.create_task(watchfiles(queue))

    yield

    # Cancel worker tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # Disconnect from broker
    await utils.disconnect_client(client)


app = FastAPI(lifespan=lifespan)

@app.get("/api/list")
async def get_list():
    return [relpath for path, relpath in utils.walk_files(root)]

@app.get("/api/info/{path:path}")
async def get_info(path):
    abspath = utils.get_abspath(root, path)

    suffix = abspath.suffix
    if suffix not in {'.b2frame', '.b2nd'}:
        abspath = utils.get_abspath(cache, f'{path}.b2')

    return utils.read_metadata(abspath)

@app.get("/api/download/{path:path}")
async def get_download(path: str, nchunk: int = -1):
    if nchunk < 0:
        utils.raise_bad_request('Chunk number required')

    abspath = utils.get_abspath(root, path)

    suffix = abspath.suffix
    if suffix == '.b2nd':
        array = blosc2.open(abspath)
        schunk = array.schunk
    elif suffix == '.b2frame':
        schunk = blosc2.open(abspath)
    else:
        relpath = pathlib.Path(abspath).relative_to(root)
        b2path = cache / f'{relpath}.b2'
        schunk = blosc2.open(b2path)

    chunk = schunk.get_chunk(nchunk)
    downloader = utils.iterchunk(chunk)

    return responses.StreamingResponse(downloader)


if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8001')
    parser.add_argument('name')
    parser.add_argument('root', default='data')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker
    name = args.name
    root = pathlib.Path(args.root).resolve()

    # Init cache and database
    var = pathlib.Path('var/pub').resolve()
    cache = var / 'cache'
    cache.mkdir(exist_ok=True, parents=True)

    # Register
    host, port = args.http
    data = {'name': name, 'http': f'{host}:{port}'}
    utils.post(f'http://{broker}/api/roots', json=data)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
