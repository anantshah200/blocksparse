
"""Cuda op Python library."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os.path
import numpy as np
import tensorflow as tf
from tensorflow.python.framework import ops

data_files_path = tf.resource_loader.get_data_files_path()
_op_module = tf.load_op_library(os.path.join(data_files_path, 'blocksparse_ops.so'))

############################## Top-K #####################################


top_k_op                = _op_module.topk
rectified_top_k_op      = _op_module.rectified_top_k
masked_softmax_op       = _op_module.masked_softmax
masked_top_k_softmax_op = _op_module.masked_top_k_softmax
masked_softmax_grad_op  = _op_module.masked_softmax_grad
ew_dx_dzza_op           = _op_module.ew_dx_dzza

def top_k(x, k):
    assert k <= x.shape[-1].val <= 1024
    return top_k_op(x, k)

def rectified_top_k(x, k, rebase=True):
    assert k <= x.shape[-1].value <= 1024
    return rectified_top_k_op(x, k, rebase=rebase)

@ops.RegisterGradient("RectifiedTopK")
def rectified_top_k_grad(op, dz):
    # same grad as relu
    return ew_dx_dzza_op(dz, op.outputs[0], op=RELU_OP)

@ops.RegisterGradient("Topk")
def top_k_grad(op, grad, _):

  in_shape  = array_ops.shape(op.inputs[0])
  ind_shape = array_ops.shape(op.outputs[1])

  ind_lastdim = array_ops.gather(ind_shape, array_ops.size(ind_shape) - 1)
  # Flatten indices to 2D.
  ind_2d = array_ops.reshape(op.outputs[1], array_ops.stack([-1, ind_lastdim]))

  in_lastdim = array_ops.gather(in_shape, array_ops.size(in_shape) - 1)
  outerdim   = array_ops.shape(ind_2d)[0]
  # Compute linear indices (flattened to 1D).
  ind = array_ops.reshape(ind_2d + array_ops.expand_dims(
      math_ops.range(0, outerdim * in_lastdim, in_lastdim), -1), [-1])

  # Substitute grad to appropriate locations and fill the rest with zeros,
  # finally reshaping it to the original input shape.
  return [
      array_ops.reshape(
          sparse_ops.sparse_to_dense(
              ind,
              array_ops.reshape(math_ops.reduce_prod(in_shape), [1]),
              array_ops.reshape(grad, [-1]),
              validate_indices=False), in_shape),
      array_ops.zeros([], dtype=dtypes.int32)
  ]


def rectified_top_k_test(x, k, rebase=True):

    a = np.argsort(x)[:,::-1]
    y = np.zeros(x.shape, dtype=np.float32)
    for i in range(x.shape[0]):

        # get min value among topk
        base = max(x[i,a[i,k-1]], 0.0) if rebase else 0.0
        #print(base, a[i,k-1])

        # write just the topk values from x to y
        y[i,a[i,:k]] = np.maximum(x[i,a[i,:k]], base) - base

    return y


def masked_top_k_softmax(x, k, mask=None, scale=1.0):

    assert k <= x.shape[-1].value <= 1024

    if mask is not None:
        x_shape = x.shape.as_list()
        m_shape = mask.shape.as_list()

        assert len(x_shape) == len(m_shape)
        for i in range(len(m_shape)):
            assert m_shape[i] in (1, x_shape[i])
        mask = [ mask ]
    else:
        mask = []

    return masked_top_k_softmax_op(x, k, scale, mask)


def softmax(x, scale=1.0, bench=0):
    return masked_softmax_op(x, scale, [], bench=bench)

def masked_softmax(x, mask=None, scale=1.0, bench=0):
    if mask is not None:
        x_shape = x.shape.as_list()
        m_shape = mask.shape.as_list()

        assert len(x_shape) == len(m_shape)
        for i in range(len(m_shape)):
            assert m_shape[i] in (1, x_shape[i])
        mask = [ mask ]
    else:
        mask = []

    return masked_softmax_op(x, scale, mask, bench=bench)


@ops.RegisterGradient("MaskedTopKSoftmax")
def masked_top_k_softmax_grad(op, dy):

    n_mask = op.get_attr("n_mask")
    mask   = [ op.inputs[3] ] if n_mask else []
    dx = masked_softmax_grad_op(dy, op.outputs[0], op.inputs[2], mask)
    if n_mask:
        return (dx, None, None, None)
    return (dx, None, None)

@ops.RegisterGradient("MaskedSoftmax")
def masked_softmax_grad(op, dy):

    bench  = op.get_attr("bench")
    n_mask = op.get_attr("n_mask")
    mask   = [ op.inputs[2] ] if n_mask else []
    dx = masked_softmax_grad_op(dy, op.outputs[0], op.inputs[1], mask, bench=bench)
    if n_mask:
        return (dx, None, None)
    return (dx, None)

def masked_softmax_test(x, mask=None, scale=1.0):
    x_shape = x.shape

    if mask is not None:
        x = x.reshape(-1, mask.size)
        y = np.empty(x.shape, dtype=np.float32)
        y.fill(-np.finfo(np.float32).max)
        nz = np.nonzero(mask.reshape(-1))
        y[:,nz] = x[:,nz] * mask.reshape(1,-1)[:,nz] * scale
    else:
        y = x * scale

    y = y.reshape(-1, x_shape[-1])
    m = np.max(y, axis=1, keepdims=True)
    z = np.exp(y - m) / np.sum(np.exp(y - m), axis=1, keepdims=True)

    return z.reshape(x_shape)

def masked_top_k_softmax_test(x, k, mask=None, scale=1.0):

    x_shape = x.shape

    if mask is not None:
        x = x.reshape(-1, mask.size)
        y = np.empty(x.shape, dtype=np.float32)
        y.fill(-np.finfo(np.float32).max)
        nz = np.nonzero(mask.reshape(-1))
        y[:,nz] = x[:,nz] * mask.reshape(1,-1)[:,nz] * scale
    else:
        y = x * scale

    y = y.reshape(-1, x_shape[-1])
    a = np.argsort(y)[:,::-1]
    z = np.zeros(y.shape, dtype=np.float32)
    for i in range(y.shape[0]):
        # get max value among top_k
        max_val = y[i,a[i,0]]
        # compute softmax on just the top_k values
        z[i,a[i,:k]] = np.exp(y[i,a[i,:k]] - max_val) / np.sum(np.exp(y[i,a[i,:k]] - max_val))

    return z.reshape(x_shape)

def masked_softmax_grad_test(dy, y, mask=None, scale=1.0):

    if mask is None:
        mask = 1.0

    return (dy - np.sum(dy * y, axis=-1, keepdims=True)) * y * mask * scale

# m = np.zeros((10,10), dtype=np.float32)
# for y, x in np.ndindex(m.shape):
#     if x <= y: m[y,x] = 1.0
# x = np.arange(1,101, dtype=np.float32).reshape(1,10,10)
# y = masked_top_k_softmax_test(x, 5, mask=m)

############################## Transpose 0213 #####################################

transpose_0213_op = _op_module.transpose0213

def transpose_0213(x):
    return transpose_0213_op(x)

@ops.RegisterGradient("Transpose0213")
def transpose_0213_grad(op, dy):
    return transpose_0213_op(dy)


############################## Blocksparse Transformer #####################################


import scipy.sparse as sparse

blocksparse_transformer_nt = _op_module.blocksparse_transformer_nt
blocksparse_transformer_nn = _op_module.blocksparse_transformer_nn
blocksparse_transformer_tn = _op_module.blocksparse_transformer_tn

blocksparse_masked_softmax      = _op_module.blocksparse_masked_softmax
blocksparse_masked_softmax_grad = _op_module.blocksparse_masked_softmax_grad

blocksparse_softmax      = _op_module.blocksparse_softmax
blocksparse_softmax_grad = _op_module.blocksparse_softmax_grad

# save a bit of gpu memory by only creating one copy of identical constant lookup tables
g_lookup_cache = dict(nt=list(), nn=list(), tn=list(), sm=list())

def get_constant(lut, name):
    global g_lookup_cache

    default_graph = tf.get_default_graph()
    for np_entry, tf_entry in g_lookup_cache[name]:
        if np_entry.dtype == lut.dtype and np_entry.shape == lut.shape and tf_entry.graph is default_graph:
            if np.abs(np_entry.astype(np.int64) - lut.astype(np.int64)).sum() == 0:
                # found an exact match
                return tf_entry

    tf_entry = tf.constant(lut, name=name+"_lut")
    g_lookup_cache[name].append( (lut, tf_entry) )
    return tf_entry


class BlocksparseTransformer(object):

    # TODO: support save restore of this object..
    # but for now just rely on hyperparameter regeneration of the object state
    # def __getstate__(self):
    #     return (self.layout, self.blk_size, self.softmax_mask, self.name)

    # def __setstate__(self, state):
    #     self.__init__(*state)

    def __init__(self, layout, block_size=64, heads=None, mask_callback=None, name=None):

        if len(layout.shape) == 2:
            assert heads is not None, "heads must be explicitly specified when using shared layouts per head"
            # broadcast same layout over all heads
            layout = np.expand_dims(layout, 0)

        if heads is None:
            heads = layout.shape[0]

        assert block_size in (16,32,64), "Block sizes of 16, 32 and 64 currently supported"
        assert len(layout.shape) == 3, "bad layout shape: " + str(layout.shape)
        assert layout.shape[1] == layout.shape[2], "layout should be square"

        #self.layout       = layout > 0  # save boolean version for serialization purposes, TODO: save packbits or csr version
        self.blk_size     = block_size
        self.name         = name
        self.heads        = heads
        self.lut_heads    = layout.shape[0]
        self.ctx_blks     = layout.shape[1]
        self.blk_shape    = (block_size, block_size)
        self.nn_max       = 0
        self.tn_max       = 0

        if layout.dtype != np.int32:
            layout = layout.astype(np.int32)

        self.nt_lut  = list()
        self.nn_lut  = list()
        self.tn_lut  = list()
        self.nt_list = list()
        self.nn_list = list()
        self.tn_list = list()
        blocks = None
        for head in range(layout.shape[0]):

            # convert to csr for vastly more efficient python iteration on large sparse layouts
            csr = sparse.csr_matrix(layout[head,:,:])
            ys, xs, bs = sparse.find(csr) # xs is in sorted order by default
            if blocks is None:
                blocks = len(bs)
            else:
                assert len(bs) == blocks, "number of layout blocks must be equal across heads"

            # make blocks contiguous along the rows
            nt_list = sorted( zip(ys, xs) )
            ys = [b[0] for b in nt_list]
            xs = [b[1] for b in nt_list]

            nt_lut = np.array(nt_list, dtype=np.int32)
            nn_lut, nn_list, nn_max = self.xn_lut(ys, xs, blocks)
            tn_lut, tn_list, tn_max = self.xn_lut(xs, ys, blocks)

            self.nt_lut.append(nt_lut)
            self.nn_lut.append(nn_lut)
            self.tn_lut.append(tn_lut)
            self.nt_list.append(nt_list)
            self.nn_list.append(nn_list)
            self.tn_list.append(tn_list)
            self.nn_max = max(self.nn_max, nn_max)
            self.tn_max = max(self.tn_max, tn_max)

        self.blocks = blocks
        self.nt_lut = get_constant(np.array(self.nt_lut, dtype=np.int32), name="nt")
        self.nn_lut = get_constant(np.array(self.nn_lut, dtype=np.int32), name="nn")
        self.tn_lut = get_constant(np.array(self.tn_lut, dtype=np.int32), name="tn")

        if mask_callback is not None:
            self.init_softmax_mask(mask_callback)
        else:
            self.softmax_mask    = None
            self.softmax_mask_np = None

    def init_softmax_mask(self, mask_callback):

        if self.blk_size == 64:
            dtype = np.uint64
        elif self.blk_size == 32:
            dtype = np.uint32
        else:
            dtype = np.uint16

        masks = []
        # for now assume one softmax mask per sparsity specificaiton
        for h in range(self.lut_heads):
            head_mask = []
            for b, (q, k) in enumerate(self.nt_list[h]):
                mask = mask_callback(self.blk_shape, h, q, k, b)
                bits = np.packbits(mask.reshape(-1,8)[:,::-1]).view(dtype)
                head_mask.append(bits)
            masks.append(head_mask)

        # numpy mask for test code
        self.softmax_mask_np = np.array(masks, dtype=dtype) # heads, blocks, blk_size
        # tf mask for kernels.  Transpose to:      heads, blk_size, blocks
        self.softmax_mask    = get_constant(np.transpose(self.softmax_mask_np, [0, 2, 1]).copy(), name="sm")

    def xn_lut(self, ys, xs, blocks):

        # build list of y's connected to each x and map to block id
        py_lut = [list() for y in range(self.ctx_blks)]
        for b in range(blocks):
            py_lut[ ys[b] ].append(( b, xs[b] ))

        # build header into variable lengh lookup tables (luts)
        # the header contains the offset and size of the lut for that output block
        max_lut = 0
        offset  = self.ctx_blks
        np_lut  = np.empty((offset + blocks, 2), dtype=np.int32)

        for i, lut in enumerate(py_lut):
            np_lut[i] = offset,  len(lut)
            max_lut = max(max_lut, len(lut))
            for entry in lut:
                np_lut[offset] = entry
                offset += 1

        return np_lut, py_lut, max_lut

    # return the coordinate (q, k) in the layout that corresponds to a given block id
    def block_coord(self, block, head=0): return self.nt_list[head][block]

    def nt_test(self, A, B):
        # A and B have shape (batch, ctx_size, state_size)
        # reshape to         (batch, ctx_blks, blk_size, heads, head_state)
        shape = list(A.shape)
        shape[1:] = [self.ctx_blks, self.blk_size, self.heads, shape[2]//self.heads]
        batch_size = shape[0]

        A = A.reshape(shape)
        B = B.reshape(shape)
        C = np.empty([batch_size, self.heads, self.blocks, self.blk_size, self.blk_size], dtype=np.float32)
        for n in range(batch_size):
            for h in range(self.heads):
                lut_head = h if self.lut_heads > 1 else 0
                for b, (y, x) in enumerate(self.nt_list[lut_head]):
                    C[n,h,b,:,:] = np.dot( A[n,y,:,h,:], B[n,x,:,h,:].T )
        return C

    def nn_test(self, A, B):
        # B and C have shape (batch, ctx_size, state_size)
        # reshape to         (batch, ctx_blks, blk_size, heads, head_state)
        shapeB = B.shape
        shapeC = list(B.shape)
        shapeC[1:] = [self.ctx_blks, self.blk_size, self.heads, shapeC[2]//self.heads]
        batch_size = shapeC[0]

        B = B.reshape(shapeC)
        C = np.zeros(shapeC, dtype=np.float32)
        for n in range(batch_size):
            for h in range(self.heads):
                lut_head = h if self.lut_heads > 1 else 0
                for x, lut in enumerate(self.nn_list[lut_head]):
                    for b, y in lut:
                        C[n,x,:,h,:] += np.dot( A[n,h,b,:,:], B[n,y,:,h,:] )
        return C.reshape(shapeB)

    def tn_test(self, A, B):
        # B and C have shape (batch, ctx_size, state_size)
        # reshape to         (batch, ctx_blks, blk_size, heads, head_state)
        shapeB = B.shape
        shapeC = list(B.shape)
        shapeC[1:] = [self.ctx_blks, self.blk_size, self.heads, shapeC[2]//self.heads]
        batch_size = shapeC[0]

        B = B.reshape(shapeC)
        C = np.zeros(shapeC, dtype=np.float32)
        for n in range(batch_size):
            for h in range(self.heads):
                lut_head = h if self.lut_heads > 1 else 0
                for x, lut in enumerate(self.tn_list[lut_head]):
                    for b, y in lut:
                        C[n,x,:,h,:] += np.dot( A[n,h,b,:,:].T, B[n,y,:,h,:] )
        return C.reshape(shapeB)


    def masked_softmax_test(self, x, scale=1.0):

        y = np.empty_like(x)
        m = self.softmax_mask_np # heads, blocks, blk_size
        bsize = self.blk_size
        for n in range(x.shape[0]):
            for h in range(x.shape[1]):
                hl = h if self.lut_heads > 1 else 0
                for lut in self.nn_list[hl]:
                    xm = np.full((len(lut), bsize * bsize), -np.finfo(np.float32).max, dtype=np.float32)
                    for i, (b, k) in enumerate(lut):
                        xb = x[n,h,b,:,:].reshape(-1)
                        if m is None:
                            # apply scale
                            xm[i,:] = xb * scale
                        else:
                            # apply mask and scale to x block
                            mask  = np.unpackbits(m[hl,b,:].view(np.uint8)).reshape(-1,8)[:,::-1].reshape(-1)
                            nzIdx = np.nonzero(mask)
                            xm[i,nzIdx] = xb[nzIdx] * scale
                    # compute softmax for collection of k blocks
                    xm = xm.reshape((len(lut), bsize, bsize))
                    xm = np.exp(xm - np.max(xm, axis=(0,2), keepdims=True))
                    ym = xm / np.sum(xm, axis=(0,2), keepdims=True)
                    for i, (b, k) in enumerate(lut):
                        y[n,h,b,:,:] = ym[i]
        return y


    def masked_softmax_grad_test(self, dy, y, scale=1.0):

        dx = np.empty_like(dy)
        for n in range(dy.shape[0]):
            for h in range(dy.shape[1]):
                hl = h if self.lut_heads > 1 else 0
                for lut in self.nn_list[hl]:

                    bs  = [ b for b, k in lut ]
                    dyb = dy[n,h,bs,:,:]
                    yb  =  y[n,h,bs,:,:]

                    dxb = (dyb - np.sum(dyb * yb, axis=(0,2), keepdims=True)) * yb * scale

                    for i, (b, k) in enumerate(lut):
                        dx[n,h,b,:,:] = dxb[i,:,:]
        return dx

    def nt_op(self, a, b, name=None, bench=0):

        return blocksparse_transformer_nt(
            a, b, self.nt_lut, self.nn_lut, self.tn_lut, CT=tf.float16,
            heads=self.heads, blocks=self.blocks, blk_size=self.blk_size, ctx_blks=self.ctx_blks,
            nn_max=self.nn_max, tn_max=self.tn_max, bench=bench, name=name
        )

    def nn_op(self, a, b, name=None, bench=0):

        return blocksparse_transformer_nn(
            a, b, self.nt_lut, self.nn_lut, self.tn_lut,
            heads=self.heads, blocks=self.blocks, blk_size=self.blk_size, ctx_blks=self.ctx_blks,
            nn_max=self.nn_max, tn_max=self.tn_max, bench=bench, name=name
        )

    def tn_op(self, a, b, name=None, bench=0):

        return blocksparse_transformer_tn(
            a, b, self.nt_lut, self.nn_lut, self.tn_lut,
            heads=self.heads, blocks=self.blocks, blk_size=self.blk_size, ctx_blks=self.ctx_blks,
            nn_max=self.nn_max, tn_max=self.tn_max, bench=bench, name=name
        )

    def query_key_op(self, q, k, name=None, bench=0):

        return blocksparse_transformer_nt(
            q, k, self.nt_lut, self.nn_lut, self.tn_lut, CT=tf.bfloat16,
            heads=self.heads, blocks=self.blocks, blk_size=self.blk_size, ctx_blks=self.ctx_blks,
            nn_max=self.nn_max, tn_max=self.tn_max, bench=bench, name=name
        )

    def weight_value_op(self, w, v, name=None, bench=0):

        return blocksparse_transformer_nn(
            w, v, self.nt_lut, self.nn_lut, self.tn_lut,
            heads=self.heads, blocks=self.blocks, blk_size=self.blk_size, ctx_blks=self.ctx_blks,
            nn_max=self.nn_max, tn_max=self.tn_max, bench=bench, name=name
        )

    def masked_softmax(self, x, scale=1.0):
        if self.softmax_mask is None:
            return self.softmax(x, scale)
        return blocksparse_masked_softmax(x, scale, self.nn_lut, self.softmax_mask, blocks=self.blocks, blk_size=self.blk_size, ctx_blks=self.ctx_blks, lut_max=self.nn_max)

    def softmax(self, x, scale=1.0):
        return blocksparse_softmax(x, scale, self.nn_lut, blocks=self.blocks, blk_size=self.blk_size, ctx_blks=self.ctx_blks, lut_max=self.nn_max)


#  w  = q    . k.T
#  QK = QC   . KC.T   16x16 = 16x64   . 16x64.T
#  QC = QK   . KC     16x64 = 16x16   . 16x64
#  KC = QK.T . QC     16x64 = 16x16.T . 16x64

@ops.RegisterGradient("BlocksparseTransformerNT")
def blocksparse_transformer_nt_grad(op, dw):

    heads    = op.get_attr("heads")
    blocks   = op.get_attr("blocks")
    blk_size = op.get_attr("blk_size")
    ctx_blks = op.get_attr("ctx_blks")
    nn_max   = op.get_attr("nn_max")
    tn_max   = op.get_attr("tn_max")
    bench    = op.get_attr("bench")
    q, k, nt_lut, nn_lut, tn_lut = op.inputs

    dq = blocksparse_transformer_nn(
        dw, k, nt_lut, nn_lut, tn_lut,
        heads=heads, blocks=blocks, blk_size=blk_size, ctx_blks=ctx_blks,
        nn_max=nn_max, tn_max=tn_max, bench=bench)

    dk = blocksparse_transformer_tn(
        dw, q, nt_lut, nn_lut, tn_lut,
        heads=heads, blocks=blocks, blk_size=blk_size, ctx_blks=ctx_blks,
        nn_max=nn_max, tn_max=tn_max, bench=bench)

    return (dq, dk, None, None, None)

#  y  = w    . v
#  QC = QK   . VC     16x64 = 16x16   . 16x64
#  QK = QC   . VC.T   16x16 = 16x64   . 16x64.T
#  VC = QK.T . QC     16x64 = 16x16.T . 16x64

@ops.RegisterGradient("BlocksparseTransformerNN")
def blocksparse_transformer_nn_grad(op, dy):

    heads    = op.get_attr("heads")
    blocks   = op.get_attr("blocks")
    blk_size = op.get_attr("blk_size")
    ctx_blks = op.get_attr("ctx_blks")
    nn_max   = op.get_attr("nn_max")
    tn_max   = op.get_attr("tn_max")
    bench    = op.get_attr("bench")
    w, v, nt_lut, nn_lut, tn_lut = op.inputs

    dw = blocksparse_transformer_nt(
        dy, v, nt_lut, nn_lut, tn_lut, CT=tf.float16,
        heads=heads, blocks=blocks, blk_size=blk_size, ctx_blks=ctx_blks,
        nn_max=nn_max, tn_max=tn_max, bench=bench)

    dv = blocksparse_transformer_tn(
        w, dy, nt_lut, nn_lut, tn_lut,
        heads=heads, blocks=blocks, blk_size=blk_size, ctx_blks=ctx_blks,
        nn_max=nn_max, tn_max=tn_max, bench=bench)

    return (dw, dv, None, None, None)


@ops.RegisterGradient("BlocksparseMaskedSoftmax")
def blocksparse_masked_softmax_op_grad(op, dy):

    blocks   = op.get_attr("blocks")
    blk_size = op.get_attr("blk_size")
    ctx_blks = op.get_attr("ctx_blks")
    lut_max  = op.get_attr("lut_max")
    y        = op.outputs[0]
    scale    = op.inputs[1]
    lut      = op.inputs[2]
    mask     = op.inputs[3]

    dx = blocksparse_masked_softmax_grad(dy, y, scale, lut, mask, blocks=blocks, blk_size=blk_size, ctx_blks=ctx_blks, lut_max=lut_max)

    return (dx, None, None, None)

@ops.RegisterGradient("BlocksparseSoftmax")
def blocksparse_softmax_op_grad(op, dy):

    blocks   = op.get_attr("blocks")
    blk_size = op.get_attr("blk_size")
    ctx_blks = op.get_attr("ctx_blks")
    lut_max  = op.get_attr("lut_max")
    y        = op.outputs[0]
    scale    = op.inputs[1]
    lut      = op.inputs[2]

    dx = blocksparse_softmax_grad(dy, y, scale, lut, blocks=blocks, blk_size=blk_size, ctx_blks=ctx_blks, lut_max=lut_max)

    return (dx, None, None)


# morton order (z-order)
def morton(x, y):
    answer = 0
    bits = max(len(bin(x)), len(bin(y))) - 2
    for i in range(bits):
        mshifted = 1 << i;
        shift = i
        answer |= ((x & mshifted) << shift) | ((y & mshifted) << (shift + 1))
        #print mshifted, shift, answer, bin(answer)
    return answer