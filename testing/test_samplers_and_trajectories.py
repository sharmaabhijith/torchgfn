from typing import Literal, Tuple

import pytest
import torch

from gfn.containers import Trajectories, Transitions
from gfn.containers.replay_buffer import ReplayBuffer
from gfn.gym import Box, DiscreteEBM, HyperGrid
from gfn.gym.graph_building import GraphBuildingOnEdges
from gfn.gym.helpers.box_utils import BoxPBEstimator, BoxPBMLP, BoxPFEstimator, BoxPFMLP
from gfn.modules import DiscreteGraphPolicyEstimator, DiscretePolicyEstimator, GFNModule
from gfn.preprocessors import (
    EnumPreprocessor,
    IdentityPreprocessor,
    KHotPreprocessor,
    OneHotPreprocessor,
)
from gfn.samplers import LocalSearchSampler, Sampler
from gfn.utils.modules import MLP, GraphEdgeActionGNN
from gfn.utils.prob_calculations import get_trajectory_pfs
from gfn.utils.training import states_actions_tns_to_traj


def trajectory_sampling_with_return(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"],
    preprocessor_name: Literal["Identity", "KHot", "OneHot", "Enum"],
    delta: float,
    n_components: int,
    n_components_s0: int,
) -> Tuple[Trajectories, Trajectories, GFNModule, GFNModule]:
    if preprocessor_name != "Identity" and env_name != "HyperGrid":
        pytest.skip("Useless tests")
    if (delta != 0.1 or n_components != 1 or n_components_s0 != 1) and env_name != "Box":
        pytest.skip("Useless tests")

    if env_name in ["HyperGrid", "DiscreteEBM"]:
        if env_name == "HyperGrid":
            env = HyperGrid(ndim=2, height=8)
            if preprocessor_name == "KHot":
                preprocessor = KHotPreprocessor(env.height, env.ndim)
            elif preprocessor_name == "OneHot":
                preprocessor = OneHotPreprocessor(
                    n_states=env.n_states, get_states_indices=env.get_states_indices
                )
            elif preprocessor_name == "Identity":
                preprocessor = IdentityPreprocessor(output_dim=env.state_shape[-1])
            elif preprocessor_name == "Enum":
                preprocessor = EnumPreprocessor(env.get_states_indices)
            else:
                raise ValueError("Invalid preprocessor name")

        elif env_name == "DiscreteEBM":
            env = DiscreteEBM(ndim=8)
            preprocessor = IdentityPreprocessor(output_dim=env.state_shape[-1])

        pf_module = MLP(input_dim=preprocessor.output_dim, output_dim=env.n_actions)
        pb_module = MLP(input_dim=preprocessor.output_dim, output_dim=env.n_actions - 1)
        pf_estimator = DiscretePolicyEstimator(
            module=pf_module,
            n_actions=env.n_actions,
            is_backward=False,
            preprocessor=preprocessor,
        )
        pb_estimator = DiscretePolicyEstimator(
            module=pb_module,
            n_actions=env.n_actions,
            is_backward=True,
            preprocessor=preprocessor,
        )

    elif env_name == "Box":
        env = Box(delta=delta)
        pf_module = BoxPFMLP(
            hidden_dim=32,
            n_hidden_layers=2,
            n_components=n_components,
            n_components_s0=n_components_s0,
        )
        pb_module = BoxPBMLP(
            hidden_dim=32,
            n_hidden_layers=2,
            n_components=n_components,
            trunk=pf_module.trunk,
        )
        pf_estimator = BoxPFEstimator(
            env=env,
            module=pf_module,
            n_components=n_components,
            n_components_s0=n_components_s0,
        )
        pb_estimator = BoxPBEstimator(
            env=env, module=pb_module, n_components=n_components
        )
    elif env_name == "GraphBuildingOnEdges":
        env = GraphBuildingOnEdges(
            n_nodes=10,
            state_evaluator=lambda s: torch.zeros(s.batch_shape),
            directed=False,
            device=torch.device("cpu"),
        )
        pf_module = GraphEdgeActionGNN(
            n_nodes=env.n_nodes,
            directed=env.is_directed,
            num_edge_classes=env.num_edge_classes,
            embedding_dim=128,
            is_backward=False,
        )
        pb_module = GraphEdgeActionGNN(
            n_nodes=env.n_nodes,
            directed=env.is_directed,
            num_edge_classes=env.num_edge_classes,
            num_conv_layers=1,
            embedding_dim=128,
            is_backward=True,
        )
        pf_estimator = DiscreteGraphPolicyEstimator(
            module=pf_module,
            is_backward=False,
        )
        pb_estimator = DiscreteGraphPolicyEstimator(
            module=pb_module,
            is_backward=True,
        )
    else:
        raise ValueError("Unknown environment name")

    sampler = Sampler(estimator=pf_estimator)
    # Test mode collects log_probs and estimator_ouputs, not encountered in the wild.
    trajectories = sampler.sample_trajectories(
        env,
        n=5,
        save_logprobs=True,
        save_estimator_outputs=False,  # FIXME: This fails on GraphBuildingOnEdges if True
    )

    states = env.reset(batch_shape=5, random=True)
    bw_sampler = Sampler(estimator=pb_estimator)
    bw_trajectories = bw_sampler.sample_trajectories(
        env, save_logprobs=True, states=states, save_estimator_outputs=False
    )

    return trajectories, bw_trajectories, pf_estimator, pb_estimator


@pytest.mark.parametrize(
    "env_name", ["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
)
@pytest.mark.parametrize("preprocessor_name", ["KHot", "OneHot", "Identity", "Enum"])
@pytest.mark.parametrize("delta", [0.1, 0.5, 0.8])
@pytest.mark.parametrize("n_components_s0", [1, 2, 5])
@pytest.mark.parametrize("n_components", [1, 2, 5])
def test_trajectory_sampling(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"],
    preprocessor_name: Literal["KHot", "OneHot", "Identity", "Enum"],
    delta: float,
    n_components_s0: int,
    n_components: int,
):
    _ = trajectory_sampling_with_return(
        env_name,
        preprocessor_name,
        delta,
        n_components_s0,
        n_components,
    )


@pytest.mark.parametrize(
    "env_name", ["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
)
def test_trajectories_getitem(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
):
    try:
        _ = trajectory_sampling_with_return(
            env_name,
            preprocessor_name="KHot" if env_name == "HyperGrid" else "Identity",
            delta=0.1,
            n_components=1,
            n_components_s0=1,
        )
    except Exception as e:
        raise ValueError(f"Error while testing {env_name}") from e


@pytest.mark.parametrize(
    "env_name", ["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
)
def test_trajectories_extend(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
):
    trajectories, *_ = trajectory_sampling_with_return(
        env_name,
        preprocessor_name="KHot" if env_name == "HyperGrid" else "Identity",
        delta=0.1,
        n_components=1,
        n_components_s0=1,
    )
    try:
        trajectories.extend(trajectories[[1, 0]])
    except Exception as e:
        raise ValueError(f"Error while testing {env_name}") from e


@pytest.mark.parametrize(
    "env_name", ["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
)
def test_sub_sampling(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
):
    trajectories, *_ = trajectory_sampling_with_return(
        env_name,
        preprocessor_name="Identity",
        delta=0.1,
        n_components=1,
        n_components_s0=1,
    )
    try:
        _ = trajectories.sample(n_samples=2)
    except Exception as e:
        raise ValueError(f"Error while testing {env_name}") from e


@pytest.mark.parametrize("env_name", ["HyperGrid", "DiscreteEBM", "Box"])
def test_reverse_backward_trajectories(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box"]
):
    """
    Ensures that the vectorized `Trajectories.reverse_backward_trajectories`
    matches the for-loop approach by toggling `debug=True`.
    """
    _, backward_trajectories, *_ = trajectory_sampling_with_return(
        env_name,
        preprocessor_name="Identity",
        delta=0.1,
        n_components=1,
        n_components_s0=1,
    )

    reversed_traj = backward_trajectories.reverse_backward_trajectories()

    for i in range(len(backward_trajectories)):
        terminating_idx = backward_trajectories.terminating_idx[i]
        for j in range(terminating_idx):
            assert torch.all(
                reversed_traj.actions.tensor[j, i]
                == backward_trajectories.actions.tensor[terminating_idx - j - 1, i]
            )
            assert torch.all(
                reversed_traj.states.tensor[j, i]
                == backward_trajectories.states.tensor[terminating_idx - j, i]
            )

        assert torch.all(reversed_traj.actions[terminating_idx, i].is_exit)
        assert torch.all(reversed_traj.states[terminating_idx + 1, i].is_sink_state)


@pytest.mark.parametrize("env_name", ["HyperGrid", "DiscreteEBM", "Box"])
def test_local_search_for_loop_equivalence(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box"]
):
    """
    Ensures that the vectorized `LocalSearchSampler.local_search` matches
    the for-loop approach by toggling `debug=True`.
    """
    # Build environment
    is_discrete = env_name in ["HyperGrid", "DiscreteEBM"]
    if is_discrete:
        if env_name == "HyperGrid":
            env = HyperGrid(ndim=2, height=5)
            preprocessor = KHotPreprocessor(env.height, env.ndim)
        elif env_name == "DiscreteEBM":
            env = DiscreteEBM(ndim=5)
            preprocessor = IdentityPreprocessor(output_dim=env.state_shape[-1])
        else:
            raise ValueError("Unknown environment name")

        # Build pf & pb
        pf_module = MLP(preprocessor.output_dim, env.n_actions)
        pb_module = MLP(preprocessor.output_dim, env.n_actions - 1)
        pf_estimator = DiscretePolicyEstimator(
            module=pf_module,
            n_actions=env.n_actions,
            is_backward=False,
            preprocessor=preprocessor,
        )
        pb_estimator = DiscretePolicyEstimator(
            module=pb_module,
            n_actions=env.n_actions,
            is_backward=True,
            preprocessor=preprocessor,
        )

    else:
        env = Box(delta=0.1)

        # Build pf & pb
        pf_module = BoxPFMLP(
            hidden_dim=32,
            n_hidden_layers=2,
            n_components=1,
            n_components_s0=1,
        )
        pb_module = BoxPBMLP(
            hidden_dim=32,
            n_hidden_layers=2,
            n_components=1,
            trunk=pf_module.trunk,
        )
        pf_estimator = BoxPFEstimator(
            env=env,
            module=pf_module,
            n_components=1,
            n_components_s0=1,
        )
        pb_estimator = BoxPBEstimator(env=env, module=pb_module, n_components=1)

    # Build sampler
    sampler = LocalSearchSampler(pf_estimator=pf_estimator, pb_estimator=pb_estimator)

    # Initial forward-sampler call
    trajectories = sampler.sample_trajectories(env, n=3, save_logprobs=True)

    # Now run local_search in debug mode so that for-loop logic is compared
    # to the vectorized logic.
    # If there's any mismatch, local_search() will raise AssertionError
    try:
        _ = sampler.local_search(
            env,
            trajectories,
            save_logprobs=True,
            back_ratio=0.5,
            use_metropolis_hastings=True,
            debug=True,  # <--- TRIGGER THE COMPARISON
        )
    except Exception as e:
        raise ValueError(
            f"Error while testing LocalSearchSampler.local_search in {env_name}"
        ) from e


@pytest.mark.parametrize(
    "env_name", ["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
)
def test_to_transition(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
):
    """
    Ensures that the `Trajectories.to_transitions` method works as expected.
    """
    trajectories, bwd_trajectories, pf_estimator, _ = trajectory_sampling_with_return(
        env_name,
        preprocessor_name="Identity",
        delta=0.1,
        n_components=1,
        n_components_s0=1,
    )

    try:
        _ = trajectories.to_transitions()

        bwd_trajectories = Trajectories.reverse_backward_trajectories(bwd_trajectories)
        # evaluate with pf_estimator
        backward_traj_pfs = get_trajectory_pfs(
            pf=pf_estimator,
            trajectories=bwd_trajectories,
            recalculate_all_logprobs=False,
        )
        bwd_trajectories.log_probs = backward_traj_pfs
        _ = bwd_trajectories.to_transitions()
    except Exception as e:
        raise ValueError(f"Error while testing {env_name}") from e


@pytest.mark.parametrize(
    "env_name", ["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"]
)
@pytest.mark.parametrize("objects", ["trajectories", "transitions"])
def test_replay_buffer(
    env_name: Literal["HyperGrid", "DiscreteEBM", "Box", "GraphBuildingOnEdges"],
    objects: Literal["trajectories", "transitions"],
):
    """Test that the replay buffer works correctly with different types of objects."""
    if env_name == "HyperGrid":
        env = HyperGrid(ndim=2, height=4)
    elif env_name == "DiscreteEBM":
        env = DiscreteEBM(ndim=8)
    elif env_name == "Box":
        env = Box(delta=0.1)
    elif env_name == "GraphBuildingOnEdges":
        env = GraphBuildingOnEdges(
            n_nodes=10,
            state_evaluator=lambda s: torch.zeros(s.batch_shape),
            directed=False,
            device=torch.device("cpu"),
        )
    else:
        raise ValueError("Unknown environment name")
    replay_buffer = ReplayBuffer(env, capacity=10)
    trajectories, *_ = trajectory_sampling_with_return(
        env_name,
        preprocessor_name="Identity",
        delta=0.1,
        n_components=1,
        n_components_s0=1,
    )
    try:
        if objects == "trajectories":
            # Filter out trajectories that are at max length
            training_objects = trajectories
            training_objects_2 = trajectories[
                trajectories.terminating_idx != trajectories.max_length
            ]
            replay_buffer.add(training_objects_2)

        else:
            training_objects = trajectories.to_transitions()

        # Add objects multiple times to test buffer behavior
        replay_buffer.add(training_objects)
        replay_buffer.add(training_objects)
        replay_buffer.add(training_objects)
        replay_buffer.add(training_objects)

        # Test that we can sample from the buffer
        sampled = replay_buffer.sample(5)
        assert len(sampled) == 5
        if objects == "trajectories":
            assert isinstance(sampled, Trajectories)
        else:
            assert isinstance(sampled, Transitions)

    except Exception as e:
        raise ValueError(f"Error while testing {env_name}") from e


def test_states_actions_tns_to_traj():
    env = HyperGrid(2, 4)
    states = torch.tensor([[0, 0], [0, 1], [0, 2], [-1, -1]])
    actions = torch.tensor([1, 1, 2])
    trajs = states_actions_tns_to_traj(states, actions, env)

    # Test that we can add the trajectories to a replay buffer
    replay_buffer = ReplayBuffer(env, capacity=10)
    replay_buffer.add(trajs)
