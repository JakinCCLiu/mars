# Copyright 1999-2020 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import shutil
import tempfile
import time
import unittest

import numpy as np
import scipy.sparse as sps
try:
    import tiledb
except (ImportError, OSError):  # pragma: no cover
    tiledb = None
try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None
try:
    import zarr
    from numcodecs import Zstd, Delta, Blosc
except ImportError:  # pragma: no cover
    zarr = None

from mars.tests.core import TestBase, ExecutorForTest
from mars.context import LocalContext
from mars.tensor import tensor, arange, totiledb, tohdf5, tozarr
from mars.tiles import get_tiled


class Test(TestBase):
    def setUp(self):
        super().setUp()
        self.executor = ExecutorForTest('numpy')

    @unittest.skipIf(tiledb is None, 'tiledb not installed')
    def testStoreTileDBExecution(self):
        ctx = tiledb.Ctx()

        tempdir = tempfile.mkdtemp()
        try:
            # store TileDB dense array
            expected = np.random.rand(8, 4, 3)
            a = tensor(expected, chunk_size=(3, 3, 2))
            save = totiledb(tempdir, a, ctx=ctx)
            self.executor.execute_tensor(save)

            with tiledb.DenseArray(uri=tempdir, ctx=ctx) as arr:
                np.testing.assert_allclose(expected, arr.read_direct())
        finally:
            shutil.rmtree(tempdir)

        tempdir = tempfile.mkdtemp()
        try:
            # store tensor with 1 chunk to TileDB dense array
            a = arange(12)
            save = totiledb(tempdir, a, ctx=ctx)
            self.executor.execute_tensor(save)

            with tiledb.DenseArray(uri=tempdir, ctx=ctx) as arr:
                np.testing.assert_allclose(np.arange(12), arr.read_direct())
        finally:
            shutil.rmtree(tempdir)

        tempdir = tempfile.mkdtemp()
        try:
            # store 2-d TileDB sparse array
            expected = sps.random(8, 7, density=0.1)
            a = tensor(expected, chunk_size=(3, 5))
            save = totiledb(tempdir, a, ctx=ctx)
            self.executor.execute_tensor(save)

            with tiledb.SparseArray(uri=tempdir, ctx=ctx) as arr:
                data = arr[:, :]
                coords = data['coords']
                value = data[arr.attr(0).name]
                ij = tuple(coords[arr.domain.dim(k).name] for k in range(arr.ndim))
                result = sps.coo_matrix((value, ij), shape=arr.shape)

                np.testing.assert_allclose(expected.toarray(), result.toarray())
        finally:
            shutil.rmtree(tempdir)

        tempdir = tempfile.mkdtemp()
        try:
            # store TileDB dense array
            expected = np.asfortranarray(np.random.rand(8, 4, 3))
            a = tensor(expected, chunk_size=(3, 3, 2))
            save = totiledb(tempdir, a, ctx=ctx)
            self.executor.execute_tensor(save)

            with tiledb.DenseArray(uri=tempdir, ctx=ctx) as arr:
                np.testing.assert_allclose(expected, arr.read_direct())
                self.assertEqual(arr.schema.cell_order, 'col-major')
        finally:
            shutil.rmtree(tempdir)

    @unittest.skipIf(h5py is None, 'h5py not installed')
    def testStoreHDF5Execution(self):
        raw = np.random.RandomState(0).rand(10, 20)

        group_name = 'test_group'
        dataset_name = 'test_dataset'

        t1 = tensor(raw, chunk_size=20)
        t2 = tensor(raw, chunk_size=9)

        with self.assertRaises(TypeError):
            tohdf5(object(), t2)

        this = self

        class MockSession:
            def __init__(self):
                self.executor = this.executor

        ctx = LocalContext(MockSession())
        executor = ExecutorForTest('numpy', storage=ctx)
        with ctx:
            with tempfile.TemporaryDirectory() as d:
                filename = os.path.join(d, 'test_store_{}.hdf5'.format(int(time.time())))

                # test 1 chunk
                r = tohdf5(filename, t1, group=group_name, dataset=dataset_name)

                executor.execute_tensor(r)

                with h5py.File(filename, 'r') as f:
                    result = np.asarray(f['{}/{}'.format(group_name, dataset_name)])
                    np.testing.assert_array_equal(result, raw)

                # test filename
                r = tohdf5(filename, t2, group=group_name, dataset=dataset_name)

                executor.execute_tensor(r)

                rt = get_tiled(r)
                self.assertEqual(type(rt.chunks[0].inputs[1].op).__name__, 'SuccessorsExclusive')
                self.assertEqual(len(rt.chunks[0].inputs[1].inputs), 0)

                with h5py.File(filename, 'r') as f:
                    result = np.asarray(f['{}/{}'.format(group_name, dataset_name)])
                    np.testing.assert_array_equal(result, raw)

                with self.assertRaises(ValueError):
                    tohdf5(filename, t2)

                with h5py.File(filename, 'r') as f:
                    # test file
                    r = tohdf5(f, t2, group=group_name, dataset=dataset_name)

                executor.execute_tensor(r)

                with h5py.File(filename, 'r') as f:
                    result = np.asarray(f['{}/{}'.format(group_name, dataset_name)])
                    np.testing.assert_array_equal(result, raw)

                with self.assertRaises(ValueError):
                    with h5py.File(filename, 'r') as f:
                        tohdf5(f, t2)

                with h5py.File(filename, 'r') as f:
                    # test dataset
                    ds = f['{}/{}'.format(group_name, dataset_name)]
                    # test file
                    r = tohdf5(ds, t2)

                executor.execute_tensor(r)

                with h5py.File(filename, 'r') as f:
                    result = np.asarray(f['{}/{}'.format(group_name, dataset_name)])
                    np.testing.assert_array_equal(result, raw)

    @unittest.skipIf(zarr is None, 'zarr not installed')
    def testStoreZarrExecution(self):
        raw = np.random.RandomState(0).rand(10, 20)

        group_name = 'test_group'
        dataset_name = 'test_dataset'

        t = tensor(raw, chunk_size=6)

        with self.assertRaises(TypeError):
            tozarr(object(), t)

        with tempfile.TemporaryDirectory() as d:
            filename = os.path.join(d, 'test_store_{}.zarr'.format(int(time.time())))
            path = '{}/{}/{}'.format(filename, group_name, dataset_name)

            r = tozarr(filename, t, group=group_name, dataset=dataset_name, compressor=Zstd(level=3))
            self.executor.execute_tensor(r)

            arr = zarr.open(path)
            np.testing.assert_array_equal(arr, raw)
            self.assertEqual(arr.compressor, Zstd(level=3))

            r = tozarr(path, t + 2)
            self.executor.execute_tensor(r)

            arr = zarr.open(path)
            np.testing.assert_array_equal(arr, raw + 2)

            filters = [Delta(dtype='i4')]
            compressor = Blosc(cname='zstd', clevel=1, shuffle=Blosc.SHUFFLE)
            arr = zarr.open(path, compressor=compressor, filters=filters)

            r = tozarr(arr, t + 1)
            self.executor.execute_tensor(r)
            result = zarr.open_array(path)
            np.testing.assert_array_equal(result, raw + 1)
