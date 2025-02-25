"""Hyperbolic layers."""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init
from torch.nn.modules.module import Module
from torch.nn.parameter import Parameter

from layers.att_layers import DenseAtt


def get_dim_act_curv(args):
    """
    Helper function to get dimension and activation at every layer.
    :param args:
    :return:
    """
    if not args.act:
        act = lambda x: x
    else:
        act = getattr(F, args.act)
    acts = [act] * (args.num_layers - 1)
    dims = [args.feat_dim] + ([args.dim] * (args.num_layers - 1))
    if args.task in ['lp', 'rec']:
        dims += [args.dim]
        acts += [act]
        n_curvatures = args.num_layers
    else:
        n_curvatures = args.num_layers - 1
    if args.c is None:
        # create list of trainable curvature parameters
        curvatures = [nn.Parameter(torch.Tensor([1.])) for _ in range(n_curvatures)]
    else:
        # fixed curvature
        curvatures = [torch.tensor([args.c]) for _ in range(n_curvatures)]
        if not args.cuda == -1:
            curvatures = [curv.to(args.device) for curv in curvatures]
    return dims, acts, curvatures



class HNNLayer(nn.Module):
    """
    Hyperbolic neural networks layer.
    """

    def __init__(self, manifold, in_features, out_features, c, dropout, act, use_bias):
        super(HNNLayer, self).__init__()
        self.linear = HypLinear(manifold, in_features, out_features, c, dropout, use_bias)
        self.hyp_act = HypAct(manifold, c, c, act)

    def forward(self, x):
        h = self.linear.forward(x)
        h = self.hyp_act.forward(h)
        return h


class HyperbolicGraphConvolution(nn.Module):
    """
    Hyperbolic graph convolution layer.
    """

    def __init__(self, manifold, in_features, out_features, c_in, c_out, dropout, act, use_bias, use_att):
        super(HyperbolicGraphConvolution, self).__init__()
        self.linear = HypLinear(manifold, in_features, out_features, c_in, dropout, use_bias)
        self.agg = HypAgg(manifold, c_in, use_att, out_features, dropout)
        self.hyp_act = HypAct(manifold, c_in, c_out, act)

    def forward(self, input):
        x, adj = input
        h = self.linear.forward(x)
        h = self.agg.forward(h, adj)
        h = self.hyp_act.forward(h)
        output = h, adj
        return output


class HypLinear(nn.Module):
    """
    Hyperbolic linear layer.
    """

    def __init__(self, manifold, in_features, out_features, c, dropout, use_bias):
        super(HypLinear, self).__init__()
        self.manifold = manifold
        self.in_features = in_features
        self.out_features = out_features
        self.c = c
        self.dropout = dropout
        self.use_bias = use_bias
        self.bias = nn.Parameter(torch.Tensor(out_features))
        self.weight = nn.Parameter(torch.Tensor(out_features, in_features))
        self.reset_parameters()

    def reset_parameters(self):
        init.xavier_uniform_(self.weight, gain=math.sqrt(2))
        init.constant_(self.bias, 0)

    def forward(self, x):
        #print("Hyplin")
        
        drop_weight = F.dropout(self.weight, self.dropout, training=self.training)
        #print(self.weight.shape)
        mv = self.manifold.mobius_matvec(drop_weight, x, self.c)
        #print(mv.shape)
        #print("mv")
        #print(mv)
        res = self.manifold.proj(mv, self.c)
        if self.use_bias: 
            #print("self.bias")
            #print(self.bias)
            bias = self.manifold.proj_tan0(self.bias, self.c)
            hyp_bias = self.manifold.expmap0(bias, self.c)
            hyp_bias = self.manifold.proj(hyp_bias, self.c)
            res = self.manifold.mobius_add(res, hyp_bias, c=self.c)
            res = self.manifold.proj(res, self.c)
            #print("res")
            #print(res)

        return res
        

    def extra_repr(self):
        return 'in_features={}, out_features={}, c={}'.format(
                self.in_features, self.out_features, self.c
        )


class HypAgg(Module):
    """
    Hyperbolic aggregation layer.
    """

    def __init__(self, manifold, c, use_att, in_features, dropout):
        super(HypAgg, self).__init__()
        self.manifold = manifold
        self.c = c
        self.use_att = use_att

        self.in_features = in_features
        self.dropout = dropout
        if use_att:
            self.att = DenseAtt(in_features, dropout, lambda x: x)

    def forward(self, x, adj):
        #print("HypAgg")
        #print(x)
        x_tangent = self.manifold.logmap0(x, c=self.c)
        #print("x_tangent")
        #print(x_tangent)
        if self.use_att:
            # TODO : merge in sparse att layer
            adj = self.att(x_tangent, adj)
        support_t = torch.spmm(adj, x_tangent)
        #print("adj")
        #print(adj)

        #print("support_t")
        #print(torch.isnan(support_t).sum())
        output = self.manifold.proj(self.manifold.expmap0(support_t, c=self.c), c=self.c)
        #print(torch.isnan(output).sum())
        return output

    def extra_repr(self):
        return 'c={}, use_att={}'.format(
                self.c, self.use_att
        )


class HypAct(Module):
    """
    Hyperbolic activation layer.
    """

    def __init__(self, manifold, c_in, c_out, act):
        super(HypAct, self).__init__()
        self.manifold = manifold
        self.c_in = c_in
        self.c_out = c_out
        self.act = act

    def forward(self, x):
        #print("HypAct")
        xt = self.act(self.manifold.logmap0(x, c=self.c_in))
        #print(torch.isnan(xt).sum())
        xt = self.manifold.proj_tan0(xt, c=self.c_out)
        #print(torch.isnan(xt).sum())
        
        return self.manifold.proj(self.manifold.expmap0(xt, c=self.c_out), c=self.c_out)

    def extra_repr(self):
        return 'c_in={}, c_out={}'.format(
                self.c_in, self.c_out
        )
