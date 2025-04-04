"""
Copied and Adapted from https://github.com/Tikquuss/GflowNets_Tutorial
"""

from typing import List, Literal, Tuple

import torch
from einops import rearrange

from gfn.actions import Actions
from gfn.env import DiscreteEnv
from gfn.states import DiscreteStates


class HyperGrid(DiscreteEnv):
    def __init__(
        self,
        ndim: int = 2,
        height: int = 4,
        R0: float = 0.1,
        R1: float = 0.5,
        R2: float = 2.0,
        reward_cos: bool = False,
        device: Literal["cpu", "cuda"] | torch.device = "cpu",
    ):
        """HyperGrid environment from the GFlowNets paper.
        The states are represented as 1-d tensors of length `ndim` with values in
        {0, 1, ..., height - 1}.

        Args:
            ndim: dimension of the grid. Defaults to 2.
            height: height of the grid. Defaults to 4.
            R0: reward parameter R0. Defaults to 0.1.
            R1: reward parameter R1. Defaults to 0.5.
            R2: reward parameter R1. Defaults to 2.0.
            reward_cos: Which version of the reward to use. Defaults to False.
            device: The device to use for the environment.
        """
        self.ndim = ndim
        self.height = height
        self.R0 = R0
        self.R1 = R1
        self.R2 = R2
        self.reward_cos = reward_cos

        s0 = torch.zeros(ndim, dtype=torch.long, device=device)
        sf = torch.full((ndim,), fill_value=-1, dtype=torch.long, device=device)
        n_actions = ndim + 1

        state_shape = (self.ndim,)

        super().__init__(
            n_actions=n_actions,
            s0=s0,
            state_shape=state_shape,
            sf=sf,
        )

    def update_masks(self, states: DiscreteStates) -> None:
        """Update the masks based on the current states."""
        # Not allowed to take any action beyond the environment height, but
        # allow early termination.
        # TODO: do we need to handle the conditional case here?
        states.set_nonexit_action_masks(
            states.tensor == self.height - 1,
            allow_exit=True,
        )
        states.backward_masks = states.tensor != 0

    def make_random_states_tensor(self, batch_shape: Tuple[int, ...]) -> torch.Tensor:
        """Creates a batch of random states.

        Args:
            batch_shape: Tuple indicating the shape of the batch.

        Returns the batch of random states as tensor of shape (*batch_shape, *state_shape).
        """
        return torch.randint(
            0, self.height, batch_shape + self.s0.shape, device=self.device
        )

    def step(self, states: DiscreteStates, actions: Actions) -> torch.Tensor:
        """Take a step in the environment.

        Args:
            states: The current states.
            actions: The actions to take.

        Returns the new states after taking the actions as a tensor of shape (*batch_shape, *state_shape).
        """
        new_states_tensor = states.tensor.scatter(-1, actions.tensor, 1, reduce="add")
        assert new_states_tensor.shape == states.tensor.shape
        return new_states_tensor

    def backward_step(self, states: DiscreteStates, actions: Actions) -> torch.Tensor:
        """Take a step in the environment in the backward direction.

        Args:
            states: The current states.
            actions: The actions to take.

        Returns the new states after taking the actions as a tensor of shape (*batch_shape, *state_shape).
        """
        new_states_tensor = states.tensor.scatter(-1, actions.tensor, -1, reduce="add")
        assert new_states_tensor.shape == states.tensor.shape
        return new_states_tensor

    def reward(self, final_states: DiscreteStates) -> torch.Tensor:
        r"""In the normal setting, the reward is:
        R(s) = R_0 + 0.5 \prod_{d=1}^D \mathbf{1} \left( \left\lvert \frac{s^d}{H-1}
          - 0.5 \right\rvert \in (0.25, 0.5] \right)
          + 2 \prod_{d=1}^D \mathbf{1} \left( \left\lvert \frac{s^d}{H-1} - 0.5 \right\rvert \in (0.3, 0.4) \right)

        Args:
            final_states: The final states.

        Returns the reward as a tensor of shape `batch_shape`.
        """
        final_states_raw = final_states.tensor
        R0, R1, R2 = (self.R0, self.R1, self.R2)
        ax = abs(final_states_raw / (self.height - 1) - 0.5)
        if not self.reward_cos:
            reward = (
                R0 + (0.25 < ax).prod(-1) * R1 + ((0.3 < ax) * (ax < 0.4)).prod(-1) * R2
            )
        else:
            pdf_input = ax * 5
            pdf = 1.0 / (2 * torch.pi) ** 0.5 * torch.exp(-(pdf_input**2) / 2)
            reward = R0 + ((torch.cos(ax * 50) + 1) * pdf).prod(-1) * R1

        assert reward.shape == final_states.batch_shape
        return reward

    def get_states_indices(self, states: DiscreteStates) -> torch.Tensor:
        """Get the indices of the states in the canonical ordering.

        Args:
            states: The states to get the indices of.

        Returns the indices of the states in the canonical ordering as a tensor of shape `batch_shape`.
        """
        states_raw = states.tensor

        canonical_base = self.height ** torch.arange(
            self.ndim - 1, -1, -1, device=states_raw.device
        )
        indices = (canonical_base * states_raw).sum(-1).long()
        assert indices.shape == states.batch_shape
        return indices

    def get_terminating_states_indices(self, states: DiscreteStates) -> torch.Tensor:
        """Get the indices of the terminating states in the canonical ordering from the submitted states.

        Canonical ordering is returned as a tensor of shape `batch_shape`.
        """
        return self.get_states_indices(states)

    @property
    def n_states(self) -> int:
        return self.height**self.ndim

    @property
    def n_terminating_states(self) -> int:
        return self.n_states

    @property
    def true_dist_pmf(self) -> torch.Tensor:
        all_states = self.all_states
        assert torch.all(
            self.get_states_indices(all_states)
            == torch.arange(self.n_states, device=self.device)
        )
        true_dist = self.reward(all_states)
        true_dist /= true_dist.sum()
        return true_dist

    def all_indices(self) -> List[Tuple[int, ...]]:
        """Generate all possible indices for the grid.

        Returns:
            List of index tuples representing all possible grid positions
        """

        def _all_indices(dim: int, height: int) -> List[Tuple[int, ...]]:
            if dim == 1:
                return [(i,) for i in range(height)]
            return [
                (i, *j) for i in range(height) for j in _all_indices(dim - 1, height)
            ]

        return _all_indices(self.ndim, self.height)

    @property
    def log_partition(self) -> float:
        grid = self.build_grid()
        rewards = self.reward(grid)
        return rewards.sum().log().item()

    def build_grid(self) -> DiscreteStates:
        "Utility function to build the complete grid"
        H = self.height
        ndim = self.ndim
        grid_shape = (H,) * ndim + (ndim,)  # (H, ..., H, ndim)
        grid = torch.zeros(grid_shape, device=self.device)
        for i in range(ndim):
            grid_i = torch.linspace(start=0, end=H - 1, steps=H)
            for _ in range(i):
                grid_i = grid_i.unsqueeze(1)
            grid[..., i] = grid_i

        rearrange_string = " ".join([f"n{i}" for i in range(1, ndim + 1)])
        rearrange_string += " ndim -> "
        rearrange_string += " ".join([f"n{i}" for i in range(ndim, 0, -1)])
        rearrange_string += " ndim"
        grid = rearrange(grid, rearrange_string).long()
        return self.states_from_tensor(grid)

    @property
    def all_states(self) -> DiscreteStates:
        grid = self.build_grid()
        flat_grid = rearrange(grid.tensor, "... ndim -> (...) ndim")
        return self.states_from_tensor(flat_grid)

    @property
    def terminating_states(self) -> DiscreteStates:
        return self.all_states
