import numpy as np
import sys
import random

from keras.models import Sequential
from keras.layers import Dense, Flatten, Conv2D, Activation, MaxPooling2D
from keras.optimizers import Adam, Adamax, Nadam
from keras.backend import set_image_dim_ordering
from absl import flags

from pysc2.env import sc2_env, environment
from pysc2.lib import actions
from pysc2.lib import features

from rl.memory import SequentialMemory
from rl.policy import LinearAnnealedPolicy, EpsGreedyQPolicy
from rl.core import Processor
from rl.callbacks import FileLogger, ModelIntervalCheckpoint
from rl.agents.dqn import DQNAgent 
from rl.agents.sarsa import SARSAAgent

## Actions from pySC2 API  ( Move, attack, select , hallucination actions )

_PLAYER_RELATIVE = features.SCREEN_FEATURES.player_relative.index
_PLAYER_FRIENDLY = 1
_PLAYER_NEUTRAL = 3  # beacon/minerals
_PLAYER_HOSTILE = 4
_NO_OP = actions.FUNCTIONS.no_op.id
_MOVE_SCREEN = actions.FUNCTIONS.Move_screen.id
_ATTACK_SCREEN = actions.FUNCTIONS.Attack_screen.id
_SELECT_ARMY = actions.FUNCTIONS.select_army.id
_NOT_QUEUED = [0]
_SELECT_ALL = [0]
_HAL_ADEPT = actions.FUNCTIONS.Hallucination_Adept_quick.id
_HAL_ARCHON = actions.FUNCTIONS.Hallucination_Archon_quick.id

## Size of the screen and length of the window

_SIZE = 64
_WINDOW_LENGTH = 1

## Load and save weights for training

LOAD_MODEL = False #True si ya está creado para entrenar el modelo
SAVE_MODEL = True

## global variable

episode_reward = 0

## Configure Flags for executing model from console

FLAGS = flags.FLAGS
flags.DEFINE_string("mini-game", "HalucinIce", "Name of the minigame")
flags.DEFINE_string("algorithm", "deepq", "RL algorithm to use")

## Processor
# A processor acts as a relationship between an Agent and the Env .
# useful if the agent has different requirements with respect to the form of the observations, actions, and rewards of environment
# How many frames will be an obs ?

class SC2Proc(Processor):
    def process_observation(self, observation):
        """Process the observation as obtained from the environment for use an agent and returns it"""
        obs = observation[0].observation["feature_screen"][_PLAYER_RELATIVE]  # Read the features from the screen . This will change with pix2pix
        return np.expand_dims(obs, axis=2)

    def process_state_batch(self, batch):
        """Processes an entire batch of states and returns it"""
        return batch[0]

    def process_reward(self, reward):
        """Processes the reward as obtained from the environment for use in an agent and returns it """
        reward = 0
        return reward


        ##  Define the environment


class Environment(sc2_env.SC2Env):
    """Starcraft II enviromnet. Implementation details in lib/features.py"""

    def step(self, action):
        """Apply actions, step the world forward, and return observations"""
        global episode_reward  # global variable defined previously

        action = actions_to_choose(
            action)  # Actions of Hallucination and movement  Make a function that selects among hallucination functions
        obs = super(Environment, self).step(
            [actions.FunctionCall(_NO_OP, [])])  ## change the action for Hallucination or attack ?
        # The method calls an observation that moves the screen
        observation = obs
        r = obs[0].reward
        done = obs[0].step_type == environment.StepType.LAST  # Episode_over
        episode_reward += r

        return observation, r, done, {}  # Return observation, reward, and episode_over

    def reset(self):
        # reset the environment
        global episode_reward
        episode_reward = 0
        super(Environment, self).reset()

        return super(Environment, self).step([actions.FunctionCall(_SELECT_ARMY, [_SELECT_ALL])])


def actions_to_choose(action):
    hall = [_HAL_ADEPT, _HAL_ARCHON]
    action = actions.FunctionCall(random.choice(hall), [_NOT_QUEUED])
    return action


## Agent architecture using keras rl


### Model
# Agents representation of the environment. ( How the agent thinks the environment works)

#### 1. 256 , 127, 256 are the channels- depth of the first layer, one can be colour, edges)
#### 2. Kernel size is the size of the matrix it will be use to make the convolution ( impair size is better)
#### 3. strides are the translation that kernel size will be making
#### 4. The Neural net architecture is CONV2D-RELU-MAXPOOL-FLATTEN+FULLYCONNECTED

def neural_network_model(input, actions):
    model = Sequential()
    model.add(Conv2D(256, kernel_size=(5, 5), input_shape=input))
    model.add(Activation('relu'))

    model.add(MaxPooling2D(pool_size=(2, 2), strides=None, padding='valid', data_format=None))
    model.add(Flatten())
    model.add(Dense(actions))  # This means fully connected ?
    model.add(Activation('softmax'))

    model.compile(loss="categorical_crossentropy",
                  optimizer="adam",
                  metrics=["accuracy"])

    return model


def training_game():
    env = Environment(map_name="HallucinIce", visualize=True, game_steps_per_episode=150, agent_interface_format=features.AgentInterfaceFormat(
        feature_dimensions=features.Dimensions(screen=64, minimap=32)
    ))

    input_shape = (_SIZE, _SIZE, 1)
    nb_actions = _SIZE * _SIZE  # Should this be an integer

    model = neural_network_model(input_shape, nb_actions)
    # memory : how many subsequent observations should be provided to the network?
    memory = SequentialMemory(limit=5000, window_length=_WINDOW_LENGTH)

    processor = SC2Proc()

    ### Policy
    # Agent´s behaviour function. How the agent pick actions
    # LinearAnnealedPolicy is a wrapper that transforms the policy into a linear incremental linear solution . Then why im not see LAP with other than not greedy ?
    # EpsGreedyQPolicy is a way of selecting random actions with uniform distributions from a set of actions . Select an action that can give max or min rewards
    # BolztmanQPolicy . Assumption that it follows a Boltzman distribution. gives the probability that a system will be in a certain state as a function of that state´s energy??

    policy = LinearAnnealedPolicy(EpsGreedyQPolicy(), attr="eps", value_max=1, value_min=0.7, value_test=.0,
                                  nb_steps=1e6)
    # policy = (BoltzmanQPolicy( tau=1., clip= (-500,500)) #clip defined in between -500 / 500


    ### Agent
    # Double Q-learning ( combines Q-Learning with a deep Neural Network )
    # Q Learning -- Bellman equation

    dqn = DQNAgent(model=model, nb_actions=nb_actions, memory=memory,
                   nb_steps_warmup=500, target_model_update=1e-2, policy=policy,
                   batch_size=150, processor=processor)

    dqn.compile(Adam(lr=.001), metrics=["mae"])


    ## Save the parameters and upload them when needed

    name = "HallucinIce"
    w_file = "dqn_{}_weights.h5f".format(name)
    check_w_file = "train_w" + name + "_weights.h5f"

    if SAVE_MODEL:
        check_w_file = "train_w" + name + "_weights_{step}.h5f"

    log_file = "training_w_{}_log.json".format(name)
    callbacks = [ModelIntervalCheckpoint(check_w_file, interval=1000)]
    callbacks += [FileLogger(log_file, interval=100)]

    if LOAD_MODEL:
        dqn.load_weights(w_file)

    dqn.fit(env, callbacks=callbacks, nb_steps=1e7, action_repetition=2,
            log_interval=1e4, verbose=2)

    dqn.save_weights(w_file, overwrite=True)
    dqn.test(env, action_repetition=2, nb_episodes=30, visualize=False)

if __name__ == '__main__':
    FLAGS(sys.argv)
    training_game()

