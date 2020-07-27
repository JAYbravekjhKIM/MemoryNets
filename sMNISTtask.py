import torch
import torch.nn as nn
import torch.optim as optim
import torchvision as T
import numpy as np
from tensorboardX import SummaryWriter
import pickle
import argparse
import time
import os
import sys
from common import henaff_init, cayley_init, random_orthogonal_init
from utils import str2bool, select_network
from torch._utils import _accumulate
from torch.utils.data import Subset
import seaborn as sns
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser(description='auglang parameters')
     
parser.add_argument('--net-type', type=str, default='RNN', choices=['RNN', 'MemRNN', 'RelMemRNN', 'LSTM', 'RelLSTM'], help='options: RNN, MemRNN')
parser.add_argument('--nhid', type=int, default=100, help='hidden size of recurrent net')
parser.add_argument('--save-freq', type=int, default=50, help='frequency to save data')
parser.add_argument('--cuda', type=str2bool, default=True, help='use cuda')
parser.add_argument('--random-seed', type=int, default=400, help='random seed')
parser.add_argument('--permute', type=str2bool, default=True, help='permute the order of sMNIST')
parser.add_argument('--nonlin', type=str, default='modrelu', help='non linearity none, relu, tanh, sigmoid')
parser.add_argument('--lr', type=float, default=1e-4)
parser.add_argument('--rinit', type=str, default="henaff", help='recurrent weight matrix initialization')
parser.add_argument('--iinit', type=str, default="kaiming", help='input weight matrix initialization')
parser.add_argument('--batch', type=int, default=100)
parser.add_argument('--weight_decay', type=float, default=0)
parser.add_argument('--alpha', type=float, default=0.99)
parser.add_argument('--log', action='store_true', default=False, help='Use tensorboardX')
parser.add_argument('--name', type=str, default='default', help='save name')
parser.add_argument('--adam', action='store_true', default=False, help='Use adam')
parser.add_argument('--load', action='store_true', default=False, help='load, dont train')
parser.add_argument('--k', type=int, default=1, help='Attend ever k timesteps')
parser.add_argument('--lastk', type=int, default=10, help='Size of short term bucket')
parser.add_argument('--rsize', type=int, default=10, help='Size of long term bucket')

args = parser.parse_args()

torch.cuda.manual_seed(args.random_seed)
torch.manual_seed(args.random_seed)
np.random.seed(args.random_seed)
rng = np.random.RandomState(100)
if args.permute:
    order = rng.permutation(784)
else:
    order = np.arange(784)

#trainset = T.datasets.MNIST(root='./MNIST', train=True, download=True, transform=T.transforms.ToTensor())
#valset = T.datasets.MNIST(root='./MNIST', train=True, download=True, transform=T.transforms.ToTensor())
#offset = 10000
trainset = T.datasets.MNIST(root='./MNIST', train=True, download=True, transform=T.transforms.ToTensor())
testset = T.datasets.MNIST(root='./MNIST', train=False, download=True, transform=T.transforms.ToTensor())

R = rng.permutation(len(trainset))
train_sampler = torch.utils.data.sampler.SubsetRandomSampler(range(50000))
valid_sampler = torch.utils.data.sampler.SubsetRandomSampler(range(50000, 60000))

trainloader = torch.utils.data.DataLoader(trainset, batch_size=100, shuffle=False, sampler=train_sampler, num_workers=2)
valloader = torch.utils.data.DataLoader(trainset, batch_size=100, shuffle=False, sampler=valid_sampler, num_workers=2)
testloader = torch.utils.data.DataLoader(testset, batch_size=100, num_workers=2)
'''
lengths = (len(trainset) - offset, offset)
trainset, valset = [Subset(trainset, R[offset - length:offset]) for offset, length in
                    zip(_accumulate(lengths), lengths)]
testset = T.datasets.MNIST(root='./MNIST', train=False, download=True, transform=T.transforms.ToTensor())
trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.batch, shuffle=False, num_workers=2)
valloader = torch.utils.data.DataLoader(valset, batch_size=args.batch, shuffle=False, num_workers=2)
testloader = torch.utils.data.DataLoader(testset, batch_size=args.batch, num_workers=2)
'''

class Model(nn.Module):
    def __init__(self, hidden_size, rnn):
        super(Model, self).__init__()
        self.rnn = rnn
        self.rnn.T = 784
        self.hidden_size = hidden_size
        self.lin = nn.Linear(hidden_size, 10)
        self.loss_func = nn.CrossEntropyLoss()
        #self.params = rnn.params() + [self.lin.weight, self.lin.bias]

    def forward(self, inputs, y, order):
        h = None

        hiddens = []
        inputs = inputs[:, order]
        ctr = 0
        va = []
        #for input in torch.unbind(inputs, dim=1):
        for i in range(784):
            inp = inputs[:, i].unsqueeze(1)
            #inp = inputs[:,7*i:7*(i+1)]
            if ctr % args.k == 0:
                self.rnn.app = 1
            else:
                self.rnn.app = 0
            ctr += 1
            h, vals, _ = self.rnn(inp, h, 1.0)
            va.append(vals)
            h.retain_grad()
            hiddens.append(h)
        out = self.lin(h)

        loss = self.loss_func(out, y)
        preds = torch.argmax(out, dim=1)
        correct = torch.eq(preds, y).sum().item()
        return loss, correct, va


def test_model(net, dataloader):
    accuracy = 0
    loss = 0
    net.eval()
    with torch.no_grad():
        for i, data in enumerate(dataloader):

            x, y = data
            x = x.view(-1, 784)
            if CUDA:
                x = x.cuda()
                y = y.cuda()
            loss, c, _ = net.forward(x, y, order)

            accuracy += c

    accuracy /= len(testset)
    return loss, accuracy


def save_checkpoint(state, fname):
    filename = os.path.join(SAVEDIR, fname)
    torch.save(state, filename)


def train_model(net, optimizer, start_epoch, num_epochs):
    train_losses = []
    train_accuracies = []
    test_losses = []
    test_accuracies = []
    save_norms = []
    ta = 0
    chk = 1
    for epoch in range(start_epoch, num_epochs):
        s_t = time.time()
        accs = []
        losses = []
        norms = []
        processed = 0
        net.train()
        correct = 0
        global best_test_loss
        
        for i, data in enumerate(trainloader, 0):
            inp_x, inp_y = data
            inp_x = inp_x.view(-1, 784)
            if chk == 1:
                tx = inp_x[0].unsqueeze(0)
                ty = inp_y[0].unsqueeze(0)
                if CUDA:
                    tx = tx.cuda()
                    ty = ty.cuda()
                chk = 0

            if CUDA:
                inp_x = inp_x.cuda()
                inp_y = inp_y.cuda()
            optimizer.zero_grad()

            loss, c, _ = net.forward(inp_x, inp_y, order)
            correct += c
            processed += inp_x.shape[0]
            #print(loss.item())

            accs.append(correct / float(processed))

            loss.backward()
            #print(i, loss.item())
            losses.append(loss.item())

            optimizer.step()

            norm = torch.nn.utils.clip_grad_norm_(net.parameters(), 'inf')
            norms.append(norm)

        test_loss, test_acc = test_model(net, valloader)
        test_accuracies.append(test_acc)
        test_losses.append(test_loss)
        if test_loss > best_test_loss:
            best_test_loss = test_loss
            tl, ta = test_model(net, testloader)
            torch.save(net.state_dict(), model_dir + 'best_model.pt')


        print('Epoch {}, Time for Epoch: {}, Train Loss: {}, Train Accuracy: {} Test Loss: {} Test Accuracy {}'.format(
            epoch + 1, time.time() - s_t, np.mean(losses), np.mean(accs), test_loss, test_acc))
        train_losses.append(np.mean(losses))
        train_accuracies.append(np.mean(accs))
        save_norms.append(np.mean(norms))
        if args.log:
            writer.add_scalar('Train acc', np.mean(accs), epoch)
            writer.add_scalar('Valid acc', test_acc, epoch)
            writer.add_scalar('Test acc', ta, epoch)

        status = {'start_epoch': epoch+1, 'best_val_loss': best_test_loss, 'model_state': net.state_dict(), 'optimizer_state': optimizer.state_dict()}
        torch.save(status, model_dir + 'status.pt')
        print('model checkpoint saved')
        #tl, ta, vals = net.forward(tx, ty, order)
        #title = str(ty[0].item())
        #mat = np.zeros((112, 112))
        #for j in range(112):
        #    if vals[j][0] is None:
        #        continue
        #    #avg = torch.sum(vals[j][1], dim=1) / vals[j][1].size(1)
        #    for k in range(vals[j][1].size(0)):
        #        mat[j][k] = vals[j][0][k][0]
        #fig, ax = plt.subplots(figsize=(15,10))
        #ax = sns.heatmap(mat, cmap='Greys')
        #ax.set_title(title)
        #name = 'step_' + str(epoch) + '_acc_' + str(test_acc)
        #plt.savefig('heatmaps_mnist/' + name + '.png')
        #plt.close(fig)
        # save data
        '''
        if epoch % SAVEFREQ == 0 or epoch == num_epochs - 1:
            with open(SAVEDIR + '{}_Train_Losses'.format(NET_TYPE), 'wb') as fp:
                pickle.dump(train_losses, fp)

            with open(SAVEDIR + '{}_Test_Losses'.format(NET_TYPE), 'wb') as fp:
                pickle.dump(test_losses, fp)

            with open(SAVEDIR + '{}_Test_Accuracy'.format(NET_TYPE), 'wb') as fp:
                pickle.dump(test_accuracies, fp)

            with open(SAVEDIR + '{}_Train_Accuracy'.format(NET_TYPE), 'wb') as fp:
                pickle.dump(train_accuracies, fp)
            with open(SAVEDIR + '{}_Grad_Norms'.format(NET_TYPE), 'wb') as fp:
                pickle.dump(save_norms, fp)

            save_checkpoint({
                'state_dict': net.state_dict(),
                'optimizer': optimizer.state_dict(),
                'epoch': epoch
            },
                '{}_{}.pth.tar'.format(NET_TYPE, epoch)
            )
        '''
    '''
    best_state = torch.load(os.path.join(SAVEDIR, 'best_model.pth.tar'))
    net.load_state_dict(best_state['state_dict'])
    test_loss, test_acc = test_model(net, testloader)
    with open(os.path.join(SAVEDIR, 'log_test.txt'), 'w') as fp:
        fp.write('Test loss: {} Test accuracy: {}'.format(test_loss, test_acc))
    '''
    return


lr = args.lr
random_seed = args.random_seed
NET_TYPE = args.net_type
CUDA = args.cuda
SAVEFREQ = args.save_freq
inp_size = 1
hid_size = args.nhid  # calc_hidden_size(NET_TYPE,165000,1,10)
nonlins = ['relu', 'tanh', 'sigmoid', 'modrelu']
nonlin = args.nonlin.lower()
print(nonlin)
if nonlin not in nonlins:
    nonlin = 'none'
    print('Non lin not found, using no nonlinearity')
decay = args.weight_decay
udir = 'HS_{}_NL_{}_lr_{}_BS_{}_rinit_{}_iinit_{}_decay_{}_alpha_{}'.format(hid_size, nonlin, lr, args.batch,
                                                                            args.rinit, args.iinit, decay, args.alpha)
LOGDIR = './logs/sMNIST/{}/{}/{}/'.format(NET_TYPE, udir, random_seed)
SAVEDIR = './saves/sMNIST/{}/{}/{}/'.format(NET_TYPE, udir, random_seed)
'''
if not os.path.exists(SAVEDIR):
    os.makedirs(SAVEDIR)

with open(SAVEDIR + 'hparams.txt', 'w') as fp:
    for key, val in args.__dict__.items():
        fp.write(('{}: {}'.format(key, val)))
'''
best_test_loss = 0
model_dir = './newImageLogs/' + args.name + '/'
try:
    status = torch.load(model_dir + 'status.pt')
    best_test_loss = status['best_val_loss']
except OSError:
    if not os.path.isdir(model_dir):
        os.makedirs(model_dir)
    status = {'start_epoch': 0}

T = 784
batch_size = args.batch
out_size = 10

rnn = select_network(NET_TYPE, inp_size, hid_size, nonlin, args.rinit, args.iinit, CUDA, args.lastk, args.rsize)
net = Model(hid_size, rnn)
if 'model_state' in status:
    net.load_state_dict(status['model_state'])
net.rnn.T = 784
if CUDA:
    net = net.cuda()
    net.rnn = net.rnn.cuda()
print('sMNIST task')
print(NET_TYPE)
print('Cuda: {}'.format(CUDA))

if args.adam:
    optimizer = optim.Adam(net.parameters(), lr=args.lr)
else:
    optimizer = optim.RMSprop(net.parameters(), lr=args.lr, alpha=args.alpha)

if 'optimizer_state' in status:
    optimizer.load_state_dict(status['optimizer_state'])

if args.log:
    writer = SummaryWriter(model_dir)

start_epoch = status['start_epoch']

num_epochs = 200
train_model(net, optimizer, start_epoch, num_epochs)

'''
python sMNISTtask.py --log --net-type=RelLSTM --k=1 --name=pm_rl0.0001lrk1
'''