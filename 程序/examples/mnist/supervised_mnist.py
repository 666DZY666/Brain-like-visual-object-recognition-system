import os
import torch
import numpy as np
import argparse
import matplotlib.pyplot as plt

from time import time as t

from bindsnet.datasets import MNIST
from bindsnet.encoding import poisson_loader
from bindsnet.models import DiehlAndCook2015
from bindsnet.network.monitors import Monitor
from bindsnet.utils import get_square_assignments, get_square_weights
from bindsnet.evaluation import all_activity, proportion_weighting, assign_labels
from bindsnet.analysis.plotting import plot_input, plot_assignments, plot_performance, \
    plot_weights, plot_spikes, plot_voltages

parser = argparse.ArgumentParser()
parser.add_argument('--seed', type=int, default=0)
parser.add_argument('--n_neurons', type=int, default=100)
parser.add_argument('--n_train', type=int, default=60000)
parser.add_argument('--n_test', type=int, default=10000)
parser.add_argument('--n_clamp', type=int, default=1)
parser.add_argument('--exc', type=float, default=22.5)
parser.add_argument('--inh', type=float, default=50.0)
parser.add_argument('--time', type=int, default=350)
parser.add_argument('--dt', type=int, default=1.0)
parser.add_argument('--intensity', type=float, default=0.5)
parser.add_argument('--progress_interval', type=int, default=10)
parser.add_argument('--update_interval', type=int, default=250)
parser.add_argument('--train', dest='train', action='store_true')
parser.add_argument('--test', dest='train', action='store_false')
parser.add_argument('--plot', dest='plot', action='store_true')
parser.add_argument('--gpu', dest='gpu', action='store_true')
parser.set_defaults(plot=False, gpu=False, train=True)

args = parser.parse_args()

seed = args.seed
n_neurons = args.n_neurons
n_train = args.n_train
n_test = args.n_test
n_clamp = args.n_clamp
exc = args.exc
inh = args.inh
time = args.time
dt = args.dt
intensity = args.intensity
progress_interval = args.progress_interval
update_interval = args.update_interval
train = args.train
plot = args.plot
gpu = args.gpu

if gpu:
    torch.set_default_tensor_type('torch.cuda.FloatTensor')
    torch.cuda.manual_seed_all(seed)
else:
    torch.manual_seed(seed)

if not train:
    update_interval = n_test

n_sqrt = int(np.ceil(np.sqrt(n_neurons)))
start_intensity = intensity
per_class = int(n_neurons / 10)

# Build network.
network = DiehlAndCook2015(n_inpt=784, n_neurons=n_neurons, exc=exc, inh=inh, dt=dt, nu_pre=0, nu_post=1e-2, norm=78.4)

# Voltage recording for excitatory and inhibitory layers.
exc_voltage_monitor = Monitor(network.layers['Ae'], ['v'], time=time)
inh_voltage_monitor = Monitor(network.layers['Ai'], ['v'], time=time)
network.add_monitor(exc_voltage_monitor, name='exc_voltage')
network.add_monitor(inh_voltage_monitor, name='inh_voltage')

# Load MNIST data.
images, labels = MNIST('data/MNIST', download=True).get_train()
images = images.view(-1, 784)
images *= intensity

# Lazily encode data as Poisson spike trains.
data_loader = poisson_loader(data=images, time=time)

# Record spikes during the simulation.
spike_record = torch.zeros(update_interval, time, n_neurons)

# Neuron assignments and spike proportions.
assignments = -torch.ones_like(torch.Tensor(n_neurons))
proportions = torch.zeros_like(torch.Tensor(n_neurons, 10))
rates = torch.zeros_like(torch.Tensor(n_neurons, 10))

# Sequence of accuracy estimates.
accuracy = {'all': [], 'proportion': []}

spikes = {}
for layer in set(network.layers) - {'X'}:
    spikes[layer] = Monitor(network.layers[layer], state_vars=['s'], time=time)
    network.add_monitor(spikes[layer], name='%s_spikes' % layer)

# Train the network.
print('Begin training.\n')
start = t()

for i in range(n_train):
    if i % progress_interval == 0:
        print('Progress: %d / %d (%.4f seconds)' % (i, n_train, t() - start))
        start = t()

    if i % update_interval == 0 and i > 0:
        # Get network predictions.
        all_activity_pred = all_activity(spike_record, assignments, 10)
        proportion_pred = proportion_weighting(spike_record, assignments, proportions, 10)

        # Compute network accuracy according to available classification strategies.
        accuracy['all'].append(100 * torch.sum(labels[i - update_interval:i].long()
                                               == all_activity_pred) / update_interval)
        accuracy['proportion'].append(100 * torch.sum(labels[i - update_interval:i].long()
                                                      == proportion_pred) / update_interval)

        print('\nAll activity accuracy: %.2f (last), %.2f (average), %.2f (best)'
              % (accuracy['all'][-1], np.mean(accuracy['all']), np.max(accuracy['all'])))
        print('Proportion weighting accuracy: %.2f (last), %.2f (average), %.2f (best)\n'
              % (accuracy['proportion'][-1], np.mean(accuracy['proportion']),
                 np.max(accuracy['proportion'])))

        # Assign labels to excitatory layer neurons.
        assignments, proportions, rates = assign_labels(spike_record, labels[i - update_interval:i], 10, rates)

    # Get next input sample.
    sample = next(data_loader)
    inpts = {'X': sample}

    # Run the network on the input.
    choice = np.random.choice(int(n_neurons / 10), size=n_clamp, replace=False)
    clamp = {'Ae': per_class * labels[i].long() + torch.Tensor(choice).long()}
    network.run(inpts=inpts, time=time, clamp=clamp)

    # Get voltage recording.
    exc_voltages = exc_voltage_monitor.get('v')
    inh_voltages = inh_voltage_monitor.get('v')

    # Add to spikes recording.
    spike_record[i % update_interval] = spikes['Ae'].get('s').t()

    # Optionally plot various simulation information.
    if plot:
        inpt = inpts['X'].view(time, 784).sum(0).view(28, 28)
        input_exc_weights = network.connections[('X', 'Ae')].w
        square_weights = get_square_weights(input_exc_weights.view(784, n_neurons), n_sqrt, 28)
        square_assignments = get_square_assignments(assignments, n_sqrt)
        voltages = {'Ae': exc_voltages, 'Ai': inh_voltages}

        if i == 0:
            inpt_axes, inpt_ims = plot_input(images[i].view(28, 28), inpt, label=labels[i])
            spike_ims, spike_axes = plot_spikes({layer: spikes[layer].get('s') for layer in spikes})
            weights_im = plot_weights(square_weights)
            assigns_im = plot_assignments(square_assignments)
            perf_ax = plot_performance(accuracy)
            voltage_ims, voltage_axes = plot_voltages(voltages)

        else:
            inpt_axes, inpt_ims = plot_input(images[i].view(28, 28), inpt, label=labels[i], axes=inpt_axes,
                                             ims=inpt_ims)
            spike_ims, spike_axes = plot_spikes({layer: spikes[layer].get('s') for layer in spikes},
                                                ims=spike_ims, axes=spike_axes)
            weights_im = plot_weights(square_weights, im=weights_im)
            assigns_im = plot_assignments(square_assignments, im=assigns_im)
            perf_ax = plot_performance(accuracy, ax=perf_ax)
            voltage_ims, voltage_axes = plot_voltages(voltages, ims=voltage_ims, axes=voltage_axes)

        plt.pause(1e-8)

    network.reset_()  # Reset state variables.

print('Progress: %d / %d (%.4f seconds)\n' % (n_train, n_train, t() - start))
print('Training complete.\n')
