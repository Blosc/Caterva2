###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import pathlib
import pickle

# Requirements
import httpx


def split_dsname(dataset):
    ds = str(dataset)
    root_sep = ds.find('/')
    root, dsname = ds[:root_sep], ds[root_sep + 1:]
    return dsname, root


def slice_to_string(slice_):
    if slice_ is None or slice_ == () or slice_ == slice(None):
        return ''
    slice_parts = []
    if not isinstance(slice_, tuple):
        slice_ = (slice_,)
    for index in slice_:
        if isinstance(index, int):
            slice_parts.append(str(index))
        elif isinstance(index, slice):
            start = index.start or ''
            stop = index.stop or ''
            if index.step not in (1, None):
                raise IndexError('Only step=1 is supported')
            # step = index.step or ''
            slice_parts.append(f"{start}:{stop}")
    return ", ".join(slice_parts)


def parse_slice(string):
    if not string:
        return None
    obj = []
    for segment in string.split(','):
        if ':' not in segment:
            segment = int(segment)
        else:
            segment = [int(x) if x else None for x in segment.split(':')]
            segment = slice(*segment)
        obj.append(segment)

    return tuple(obj)


def get_download_url(path, host, params):
    download_ = params.get('download', False)
    if download_ and params.get('slice_') is not None:
        raise ValueError('Cannot download a slice')
    response = httpx.get(f'http://{host}/api/download/{path}', params=params)
    response.raise_for_status()

    if download_:
        return response.json()

    data = response.content
    # TODO: decompression is not working yet. HTTPX does this automatically?
    # data = zlib.decompress(data)
    return pickle.loads(data)


def download_url(url, localpath):
    if url.endswith('.b2'):
        localpath += '.b2'
    with httpx.stream("GET", url) as r:
        r.raise_for_status()
        # Build the local filepath
        localpath = pathlib.Path(localpath)
        localpath.parent.mkdir(parents=True, exist_ok=True)
        with open(localpath, "wb") as f:
            for data in r.iter_bytes():
                f.write(data)
    return localpath


#
# HTTP client helpers
#
def get(url, params=None, headers=None, timeout=5, model=None):
    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    json = response.json()
    return json if model is None else model(**json)


def post(url, json=None):
    response = httpx.post(url, json=json)
    response.raise_for_status()
    return response.json()
