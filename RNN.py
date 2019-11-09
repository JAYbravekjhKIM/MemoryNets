import torch
import torch.nn as nn
from common import modrelu, henaff_init,cayley_init,random_orthogonal_init
from exp_numpy import expm
import sys
verbose = False

class RNN(nn.Module):
    def __init__(self, inp_size, hid_size, nonlin, bias=True, cuda=False, r_initializer=None,
                 i_initializer=nn.init.xavier_normal_):
        super(RNN, self).__init__()
        self.cudafy = cuda
        self.hidden_size = hid_size

        # Add Non linearity
        if nonlin == 'relu':
            self.nonlinearity = nn.ReLU()
        if nonlin == 'modrelu':
            self.nonlinearity = modrelu(hid_size)
        elif nonlin == 'tanh':
            self.nonlinearity = nn.Tanh()
        elif nonlin == 'sigmoid':
            self.nonlinearity = nn.Sigmoid()
        else:
            self.nonlinearity = None

        self.r_initializer = r_initializer
        self.i_initializer = i_initializer

        # Create linear layer to act on input X
        self.U = nn.Linear(inp_size, hid_size, bias=bias)
        self.V = nn.Linear(hid_size, hid_size, bias=False)
        self.i_initializer = i_initializer
        self.r_initializer = r_initializer
        self.memory = []
        self.app = 1

        self.reset_parameters()

    def reset_parameters(self):
        if self.r_initializer == "cayley":
            self.V.weight.data = torch.as_tensor(cayley_init(self.hidden_size))
            A = self.V.weight.data.triu(diagonal=1)
            A = A - A.t()
            self.V.weight.data = expm(A)
        elif self.r_initializer == "henaff":
            self.V.weight.data = torch.as_tensor(henaff_init(self.hidden_size))
            A = self.V.weight.data.triu(diagonal=1)
            A = A - A.t()
            self.V.weight.data = expm(A)
        elif self.r_initializer == "random":
            self.V.weight.data = torch.as_tensor(random_orthogonal_init(self.hidden_size))
            A = self.V.weight.data.triu(diagonal=1)
            A = A - A.t()
            self.V.weight.data = expm(A)
        elif self.r_initializer == 'xavier':
            nn.init.xavier_normal_(self.V.weight.data)
        elif self.r_initializer == 'kaiming':
            nn.init.kaiming_normal_(self.V.weight.data)
        if self.i_initializer == "xavier":
            nn.init.xavier_normal_(self.U.weight.data)
        elif self.i_initializer == 'kaiming':
            nn.init.kaiming_normal_(self.U.weight.data)


    def forward(self, x, hidden, attn, cont=False):
        if hidden is None:
            hidden = x.new_zeros(x.shape[0], self.hidden_size,requires_grad=True)
            self.memory = []
        elif cont:
            hidden = hidden.detach()
            self.memory = []

        h = self.U(x) + self.V(hidden)
        if self.nonlinearity:
            h = self.nonlinearity(h)
        self.memory.append(h)
        return h, (None, None)

class MemRNN(nn.Module):
    def __init__(self, inp_size, hid_size, nonlin, bias=True, cuda=False, r_initializer=None,
                 i_initializer=nn.init.xavier_normal_):
        super(MemRNN, self).__init__()
        self.cudafy = cuda
        self.hidden_size = hid_size

        # Add Non linearity
        if nonlin == 'relu':
            self.nonlinearity = nn.ReLU()
        if nonlin == 'modrelu':
            self.nonlinearity = modrelu(hid_size)
        elif nonlin == 'tanh':
            self.nonlinearity = nn.Tanh()
        elif nonlin == 'sigmoid':
            self.nonlinearity = nn.Sigmoid()
        else:
            self.nonlinearity = None

        self.tanh = nn.Tanh()
        self.softmax = nn.Softmax(dim=0)
        # Create linear layer to act on input X
        self.U = nn.Linear(inp_size, hid_size, bias=bias)
        self.V = nn.Linear(hid_size, hid_size, bias=False)
        self.Ua = nn.Linear(hid_size, hid_size, bias=False)
        self.Va = nn.Linear(hid_size, hid_size, bias=False)
        self.v = nn.Parameter(torch.Tensor(1,hid_size))
        nn.init.xavier_normal_(self.v.data)

        self.i_initializer = i_initializer
        self.r_initializer = r_initializer
        self.ctr = 0
        self.app = 1

        self.reset_parameters()

    def reset_parameters(self):
        if self.r_initializer == "cayley":
            self.V.weight.data = torch.as_tensor(cayley_init(self.hidden_size))
            A = self.V.weight.data.triu(diagonal=1)
            A = A - A.t()
            self.V.weight.data = expm(A)
        elif self.r_initializer == "henaff":
            self.V.weight.data = torch.as_tensor(henaff_init(self.hidden_size))
            A = self.V.weight.data.triu(diagonal=1)
            A = A - A.t()
            self.V.weight.data = expm(A)
        elif self.r_initializer == "random":
            self.V.weight.data = torch.as_tensor(random_orthogonal_init(self.hidden_size))
            A = self.V.weight.data.triu(diagonal=1)
            A = A - A.t()
            self.V.weight.data = expm(A)
        elif self.r_initializer == 'xavier':
            nn.init.xavier_normal_(self.V.weight.data)
        elif self.r_initializer == 'kaiming':
            nn.init.kaiming_normal_(self.V.weight.data)
        if self.i_initializer == "xavier":
            nn.init.xavier_normal_(self.U.weight.data)
        elif self.i_initializer == 'kaiming':
            nn.init.kaiming_normal_(self.U.weight.data)

    def forward(self, x, hidden, attn, cont=False):
        if hidden is None:
            self.count = 0
            #hidden = x.new_zeros(x.shape[0], self.hidden_size, requires_grad=True)
            hidden = x.new_zeros(x.shape[0], self.hidden_size, requires_grad=False)
            self.memory = []
            h = self.U(x) + self.V(hidden)
            self.st = h
        elif cont:
            self.count = 0
            hidden = hidden.detach()
            self.memory = []
            h = self.U(x) + self.V(hidden)
            self.st = h

        else:
            all_hs = torch.stack(self.memory)
            Uahs = self.Ua(all_hs)
            #print(Uahs.size())

            es = torch.matmul(self.tanh(self.Va(self.st).expand_as(Uahs) + Uahs), self.v.unsqueeze(2)).squeeze(2)
            #print(es.shape)
            alphas = self.softmax(es)
            all_hs = torch.stack(self.memory,0)
            ct = torch.sum(torch.mul(alphas.unsqueeze(2).expand_as(all_hs), all_hs), dim=0)
            #ct = torch.sum(alphas.unsqueeze(2) * all_hs, dim=0)
            self.st = 0.5 * (all_hs[-1] + ct * attn)
            h = self.U(x) + self.V(self.st)

        if self.nonlinearity:
            h = self.nonlinearity(h)
        h.retain_grad()
        if self.app == 1:
            self.memory.append(h)
        #print(h)
        if self.count == 0:
            self.count = 1
            return h, (None, None)
        else:
            return h, (es, alphas)
