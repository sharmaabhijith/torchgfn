"""This implements a simple dynamic programming algorithm for computing edge flows given a reward function and
a backward transition probability function. Currently it's implemented for uniform P_B, but can
trivially be extended to other P_B manually specified.
"""

import torch
from simple_parsing import ArgumentParser

from gfn.configs import EnvConfig
from gfn.estimators import LogEdgeFlowEstimator
from gfn.modules import Tabular, Uniform
from gfn.parametrizations.edge_flows import FMParametrization
from gfn.preprocessors import EnumPreprocessor
from gfn.validate import validate

parser = ArgumentParser()

parser.add_arguments(EnvConfig, dest="env_config")

args = parser.parse_args()

env_config: EnvConfig = args.env_config


env = env_config.parse(device="cpu")

F_edge = torch.zeros(env.n_states, env.n_actions)

logit_PB = Uniform(output_dim=env.n_actions - 1)

preprocessor = EnumPreprocessor(env)

all_states = env.all_states

all_states_indices = preprocessor(all_states)

# Zeroth step: Define the necessary containers
Y = set()  # Contains the state indices that do not need more visits

# The following represents a queue of indices of the states that need to be visited,
# and their final state flow
U = []

# First step: Fill the terminating flows with the rewards and initialize the state flows
F_edge[all_states_indices, -1] = env.reward(all_states)
F_state = env.reward(all_states)

# Second step: Store the states that have no children besides s_f
for index in all_states_indices[all_states.forward_masks.long().sum(1) == 1].numpy():
    U.append((index, F_edge[index, -1].item()))


# Third Step: Iterate over the states in U and update the flows
while len(U) > 0:
    s_prime_index, F_s_prime = U.pop(0)
    Y.add(s_prime_index)
    state_prime = all_states[[s_prime_index]]

    backward_mask = state_prime.backward_masks[0]
    pb_logits = logit_PB(preprocessor(state_prime))
    pb_logits[~backward_mask] = -float("inf")
    pb = torch.softmax(pb_logits, dim=0)
    for i in range(env.n_actions - 1):
        if backward_mask[i]:
            state = env.backward_step(state_prime, torch.tensor([i]))
            s_index = preprocessor(state)[0].item()
            pb_logits = logit_PB(preprocessor(state_prime))
            F_edge[s_index, i] = F_s_prime * pb[i].item()
            F_state[s_index] = F_state[s_index] + F_edge[s_index, i]
            if all(
                [
                    preprocessor(env.step(state, torch.tensor([j])))[0].item() in Y
                    for j in range(env.n_actions - 1)
                    if state.forward_masks[0, j]
                ]
            ):
                U.append((s_index, F_state[s_index].item()))

print(F_edge)

# Sanity check - should get the right pmf
logF_edge = torch.log(F_edge)
logF_edge_module = Tabular(env, output_dim=env.n_actions - 1)
logF_edge_module.logits = logF_edge[:, :-1]
logF_edge_estimator = LogEdgeFlowEstimator(
    preprocessor=preprocessor, module=logF_edge_module
)
parametrization = FMParametrization(logF=logF_edge_estimator)
print(validate(env, parametrization, n_validation_samples=10000))
