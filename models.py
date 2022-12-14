import re
from collections import OrderedDict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init
from torch.nn import LayerNorm
from torch.nn.modules import MaxPool1d
from torch.nn.modules.activation import ReLU
from torch.nn.modules.batchnorm import BatchNorm1d, BatchNorm2d
from torch.nn.modules.conv import Conv1d, Conv2d

from embedding import *


class AttentionMatcher(nn.Module):
    def __init__(self):
        super(AttentionMatcher, self).__init__()

    def forward(self, query, support):
        attn = torch.softmax(torch.bmm(query, support.transpose(1, 2)), dim=-1)
        support = torch.matmul(attn, support)
        score = torch.mul(query, support).sum(-1)
        return score


class PatternLearner(nn.Module):
    def __init__(self, input_channels, out_channels=[128, 64, 32, 1]):
        super(PatternLearner, self).__init__()
        self.out_channels = out_channels
        self.input_channles = input_channels
        self.encoder = nn.Sequential(
            OrderedDict(
                [
                    (
                        "conv1",
                        Conv2d(self.input_channles, self.out_channels[0], (1, 3)),
                    ),
                    # BatchNorm2d(128),
                    ("relu1", ReLU()),
                    (
                        "conv2",
                        Conv2d(self.out_channels[0], self.out_channels[1], (1, 1)),
                    ),
                    # BatchNorm2d(64),
                    ("relu2", ReLU()),
                    (
                        "conv3",
                        Conv2d(self.out_channels[1], self.out_channels[2], (1, 1)),
                    ),
                    # BatchNorm2d(32),
                    ("relu3", ReLU()),
                    (
                        "conv4",
                        Conv2d(self.out_channels[2], self.out_channels[3], (1, 1)),
                    ),
                ]
            )
        )

        for name, param in self.encoder.named_parameters():
            if "conv" and "weight" in name:
                torch.nn.init.kaiming_normal_(param)

    def forward(self, x):
        batch_size, num_triples, num_channels, input_length, dim = x.size()
        x = x.view(batch_size * num_triples, num_channels, input_length, dim).transpose(
            2, 3
        )
        x = self.encoder(x)
        return x.view(batch_size, num_triples, -1)


class PatternMatcher(nn.Module):
    def __init__(self):
        super(PatternMatcher, self).__init__()

    def forward(self, query, support):
        batch_size, num_query, dim = query.size()
        support = support.expand(-1, num_query, -1)
        # ??????????????????
        scores = -torch.norm(query - support, 2, -1)
        return scores

class ProtoMatcher(nn.Module):
    def __init__(self):
        super(ProtoMatcher, self).__init__()

    def forward(self, query,proto):
        batch_size, num_query, dim = query.size()
        proto_ = torch.stack(proto,0)
        support = proto_.expand(-1, num_query, -1)
        print(proto_.size(),support.size())
        # ??????????????????
        scores = -torch.norm(query - support, 2, -1)
        return scores


class MetaP(nn.Module):
    def __init__(self, dataset, parameter):
        super(MetaP, self).__init__()
        self.train_rel_dic = dataset["train_rel_dic"]

        self.dropout_p = parameter["dropout_p"]
        self.dim = parameter["embed_dim"]
        self.embedding = Embedding(dataset, parameter)
        self.device = parameter["device"]
        self.few = parameter["few"]
        self.rum = parameter["rum"]
        self.vbm = parameter["vbm"]
        self.beta = parameter["beta"]
        self.margin = parameter["margin"]

        self.input_channels = 2 if self.rum else 1
        self.aggregator = parameter["aggregator"]
        self.pattern_learner = PatternLearner(input_channels=self.input_channels)

        self.pattern_matcher = PatternMatcher()
        self.criterion = nn.CrossEntropyLoss()
        self.dim = parameter["embed_dim"]
        self.attn_matcher = AttentionMatcher()
        self.proto_matcher = ProtoMatcher()

        self.sup_pat = dict()
        self.qry_pat = dict()
        self.proto = list()

        for key in self.train_rel_dic.keys():
            rel = self.train_rel_dic[key]
            self.sup_pat[rel] = []
            self.qry_pat[rel] = []
            self.proto.append([])

    def split_concat(self, positive, negative):
        pos_neg_e1 = torch.cat(
            [positive[:, :, 0, :], negative[:, :, 0, :]], 1
        ).unsqueeze(2)
        pos_neg_e2 = torch.cat(
            [positive[:, :, 1, :], negative[:, :, 1, :]], 1
        ).unsqueeze(2)
        return pos_neg_e1, pos_neg_e2

    def concat_relation(self, pairs, relation):
        if relation.shape[1] != pairs.shape[1]:
            relation = relation.repeat(1, pairs.shape[1], 1, 1, 1)
        triplet = torch.cat((relation, pairs), dim=-2)
        return triplet[:, :, :, [1, 0, 2], :]

    def get_relation(self, pairs, mean=False):
        """return (tail-head)"""

        relation = (pairs[:, :, :, 1, :] - pairs[:, :, :, 0, :]).unsqueeze(-2)
        if mean:
            relation = torch.mean(relation, dim=1, keepdim=True)
        return relation

    def relation_score(self,query,iseval = False):
        batch_size, num_query, dim = query.size()
        score = torch.zeros(batch_size,num_query).cuda()
        for i in range(batch_size):
            for j in range(num_query):
                temp_score = 0
                for t in range(len(self.proto)):
                    if t!= i:
                        temp_score += torch.norm(query[i][j] - self.proto[t],2,-1)
                score[i][j] = - temp_score / (len(self.proto)-1)

        return score

    def forward(self, task, iseval=False, curr_rel="", select=False, use_conv=False):
        # transfer task string into embedding
        support, support_negative, query, negative = [self.embedding(t) for t in task]

        few = support.shape[1]  # num of few
        num_sn = support_negative.shape[1]  # num of negative support
        num_q = query.shape[1]  # num of positive query
        num_n = negative.shape[1]  # num of negative query
        pos_relation = self.get_relation(support, mean=True)
        support = self.concat_relation(support, pos_relation)
        support_negative = self.concat_relation(support_negative, pos_relation)
        query = self.concat_relation(query, pos_relation)
        negative = self.concat_relation(negative, pos_relation)
        spt_pos = self.pattern_learner(support)
        spt_neg = self.pattern_learner(support_negative)
        qry = torch.cat((query, negative), dim=1)
        qry = self.pattern_learner(qry)

        if not iseval:
            for i in range(len(self.train_rel_dic)):
                rel = self.train_rel_dic[str(i)]
                temp = torch.cat((spt_pos[i],spt_neg[i]),0)
                self.sup_pat[rel] = temp
                self.qry_pat[rel] = qry[i]
                self.proto[i] = torch.mean(spt_pos[i], dim=0, keepdim=True)

        if self.aggregator == "attn":
            qry_spt_pos_score = self.attn_matcher(qry, spt_pos)
            qry_spt_neg_score = self.attn_matcher(qry, spt_neg)
        elif self.aggregator == "max":
            spt_pos = torch.max(spt_pos, dim=1, keepdim=True)[0]
            spt_neg = torch.max(spt_neg, dim=1, keepdim=True)[0]
            qry_spt_pos_score = self.pattern_matcher(qry, spt_pos)
            qry_spt_neg_score = self.pattern_matcher(qry, spt_neg)
        elif self.aggregator == "mean":
            spt_pos = torch.mean(spt_pos, dim=1, keepdim=True)
            spt_neg = torch.mean(spt_neg, dim=1, keepdim=True)
            qry_spt_pos_score = self.pattern_matcher(qry, spt_pos)
            qry_spt_neg_score = self.pattern_matcher(qry, spt_neg)
        elif self.aggregator == "relation":
            spt_pos = torch.mean(spt_pos, dim=1, keepdim=True)
            spt_neg = torch.mean(spt_neg, dim=1, keepdim=True)
            rel_score = self.proto_matcher(qry,self.proto)
            qry_spt_pos_score = self.pattern_matcher(qry, spt_pos)
            print(rel_score.size(),qry_spt_pos_score.size(),qry.size())
            qry_spt_neg_score = self.pattern_matcher(qry, spt_neg)
            relation_score  = self.relation_score(qry,iseval)

        pos_score = self.pattern_matcher(qry[:,:num_q], spt_pos)
        neg_score = self.pattern_matcher(qry[:,:num_q], spt_neg)
        pos = torch.mean(pos_score, dim=1)
        neg = torch.mean(neg_score, dim=1)
        pos_ = torch.mean(pos, dim=0)
        neg_ = torch.mean(neg, dim=0)
        delta_loss = max(0, pos_ - neg_ + self.margin)
        
        score = torch.stack((qry_spt_pos_score, qry_spt_neg_score), dim=-1)
        y_query = torch.ones((score.shape[0], score.shape[1]), dtype=torch.long)
        y_query[:, :num_q] = 0
        if self.vbm:
            if self.aggregator == "relation":
                delta = qry_spt_pos_score - qry_spt_neg_score - 0.3 * relation_score
            else:
                delta = qry_spt_pos_score - self.beta * qry_spt_neg_score
        else:
            delta = qry_spt_pos_score

        p_score = delta[:, :num_q]
        n_score = delta[:, num_q:]
        return score.view(-1, 2), y_query.view(-1).to(self.device), p_score, n_score, delta_loss
