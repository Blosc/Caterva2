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
import typing

# Requirements
import blosc2
from fastapi import FastAPI, Header, Response, responses
import uvicorn
from watchfiles import awatch

# Project
from caterva2 import utils, models

logger = logging.getLogger('pub')

# Configuration
broker = None
name = None
root = None
nworkers = 1

# State
cache = None
client = None
database = None  # <Database> instance


def get_etag(abspath):
    stat = abspath.stat()
    return f'{stat.st_mtime}:{stat.st_size}'

async def worker(queue):
    while True:
        abspath = await queue.get()
        with utils.log_exception(logger, 'Publication failed'):
            assert isinstance(abspath, pathlib.Path)
            relpath = abspath.relative_to(root)
            key = str(relpath)
            if abspath.is_file():
                print('UPDATE', relpath)
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
                data = {'path': relpath, 'metadata': metadata}
                await client.publish(name, data=data)
                # Update database
                database.etags[key] = get_etag(abspath)
                database.save()
            else:
                print('DELETE', relpath)
                data = {'path': relpath}
                await client.publish(name, data=data)
                # Update database
                if key in database.etags:
                    del database.etags[key]
                    database.save()

        queue.task_done()


async def watchfiles(queue):
    # On start, notify the network about changes to the datasets, changes done since the
    # last run.
    etags = database.etags.copy()
    for abspath, relpath in utils.walk_files(root):
        key = str(relpath)
        val = etags.pop(key, None)
        if val != get_etag(abspath):
            queue.put_nowait(abspath)

    # The etags left are those that were deleted
    for key in etags:
        queue.put_nowait(abspath)
        del database.etags[key]
        database.save()

    # Watch directory for changes
    async for changes in awatch(root):
        paths = set([abspath for change, abspath in changes])
        for abspath in paths:
            abspath = pathlib.Path(abspath)
            queue.put_nowait(abspath)

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
    return [relpath for abspath, relpath in utils.walk_files(root)]

@app.get("/api/info/{path:path}")
async def get_info(
    path: str,
    response: Response,
    if_none_match: typing.Annotated[str | None, Header()] = None
):
    abspath = utils.get_abspath(root, path)

    # Check etag
    etag = database.etags[path]
    if if_none_match == etag:
        return Response(status_code=304)

    # Regular files (.b2)
    if abspath.suffix not in {'.b2frame', '.b2nd'}:
        abspath = utils.get_abspath(cache, f'{path}.b2')

    # Return
    response.headers['Etag'] = etag
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
    parser.add_argument('--statedir', default='_caterva2/pub', type=pathlib.Path)
    parser.add_argument('name')
    parser.add_argument('root', default='data')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker
    name = args.name
    root = pathlib.Path(args.root).resolve()

    # Init cache
    statedir = args.statedir.resolve()
    cache = statedir / 'cache'
    cache.mkdir(exist_ok=True, parents=True)

    # Init database
    model = models.Publisher(etags={})
    database = utils.Database(statedir / 'db.json', model)

    # Register
    host, port = args.http
    data = {'name': name, 'http': f'{host}:{port}'}
    utils.post(f'http://{broker}/api/roots', json=data)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
