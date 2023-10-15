# 信念更新はモデルでやるが、実環境と相互作用をする場合

# import argparse
# import datetime
# import gym
# import custom_gym
# import numpy as np
# import itertools
# import torch
# from sac import SAC
# from replay_memory import ReplayMemory
# import random
# from config import cfg_seed, cfg_env

# parser = argparse.ArgumentParser(description='PyTorch Soft Actor-Critic Args')
# parser.add_argument('--eval', type=bool, default=True,
#                     help='Evaluates a policy a policy every 10 episode (default: True)')
# parser.add_argument('--batch_size', type=int, default=256, metavar='N',
#                     help='batch size (default: 256)')
# parser.add_argument('--num_steps', type=int, default=40001, metavar='N',
#                     help='maximum number of steps (default: 1000000)')
# parser.add_argument('--updates_per_step', type=int, default=1, metavar='N',
#                     help='model updates per simulator step (default: 1)')
# # parser.add_argument('--start_steps', type=int, default=10000, metavar='N',
# parser.add_argument('--start_steps', type=int, default=4000, metavar='N',
#                     help='Steps sampling random actions (default: 10000)')
# # parser.add_argument('--target_update_interval', type=int, default=1, metavar='N',
# #                     help='Value target update per no. of updates per step (default: 1)')
# parser.add_argument('--replay_size', type=int, default=10000000, metavar='N',
#                     help='size of replay buffer (default: 10000000)')
# # parser.add_argument('--cuda', action="store_true",
# #                     help='run on CUDA (default: False)')
# args = parser.parse_args()

# env_str="pendulum"
# seed = cfg_seed

# if cfg_env == "pendulum":
#     env_name = "CustomPendulum-v0"
# if cfg_env == "cartpole":
#     env_name = "CustomCartPole-v0"

# # Environment
# # env = NormalizedActions(gym.make(args.env_name))
# env = gym.make(env_name)
# env.seed(seed)
# env.action_space.seed(seed)

# torch.manual_seed(seed)
# np.random.seed(seed)
# random.seed(seed)


# import vi_iw

# import pickle
# s_dim = env.reset().flatten().shape[0]
# a_dim = env.action_space.sample().flatten().shape[0]
# z_dim = 1
# offline_data = pickle.load(open("offline_data_"+env_str+".pkl","rb"))
# debug_info = pickle.load(open("offline_data_debug_info_"+env_str+".pkl","rb"))
# debug_info = np.array(debug_info)
# args_init_dict = {"offline_data": offline_data,
#              "s_dim": s_dim,
#              "a_dim": a_dim,
#              "z_dim": z_dim,
# #              "policy":agent.select_action,
#              "mdp_policy":None,
#              "bamdp_policy":None,
#              "debug_info": None,#debug_info,
#              "env" : env}

# vi = vi_iw.iwVI(args_init_dict)

# vi.load()

# # env = vi

# # Agent
# agent = SAC(env.observation_space.shape[0]+z_dim*2, env.action_space)

# # agent.load_checkpoint(ckpt_path="checkpoints/sac_checkpoint_custom_pendulum_", evaluate=False)
# #Tesnorboard
# # writer = SummaryWriter('runs/{}_SAC_{}_{}_{}'.format(datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), args.env_name,
# #                                                              args.policy, "autotune" if args.automatic_entropy_tuning else ""))

# # Memory
# memory = ReplayMemory(args.replay_size, seed)

# # Training Loop
# total_numsteps = 0
# updates = 0

# for i_episode in itertools.count(1):
#     episode_reward = 0
#     episode_steps = 0
#     done = False
#     state = env.reset()
#     belief = vi.get_belief()
#     sads_array=np.empty((0,s_dim*2+a_dim))
#     aug_state = np.hstack([state, belief.numpy()])
#     while not done:
#         aug_state = np.hstack([state, belief.numpy()])
#         if args.start_steps > total_numsteps:
#             action = env.action_space.sample()  # Sample random action
#         else:
#             action = agent.select_action(aug_state)  # Sample action from policy

#         if len(memory) > args.batch_size:
#             # Number of updates per step in environment
#             for i in range(args.updates_per_step):
#                 # Update parameters of all the networks
#                 critic_1_loss, critic_2_loss, policy_loss, ent_loss, alpha = agent.update_parameters(memory, args.batch_size, updates)

#                 # writer.add_scalar('loss/critic_1', critic_1_loss, updates)
#                 # writer.add_scalar('loss/critic_2', critic_2_loss, updates)
#                 # writer.add_scalar('loss/policy', policy_loss, updates)
#                 # writer.add_scalar('loss/entropy_loss', ent_loss, updates)
#                 # writer.add_scalar('entropy_temprature/alpha', alpha, updates)
#                 updates += 1

#         next_state, reward, done, _ = env.step(action) # Step
#         episode_steps += 1
#         total_numsteps += 1
#         episode_reward += reward
#         sads_array = np.vstack([sads_array, 
#                                 np.hstack([state, action, next_state-state])])
#         belief = vi.get_belief(sads_array=sads_array)
#         next_aug_state = np.hstack([next_state, belief.numpy()])

#         # Ignore the "done" signal if it comes from hitting the time horizon.
#         # (https://github.com/openai/spinningup/blob/master/spinup/algos/sac/sac.py)
#         mask = 1 if episode_steps == env._max_episode_steps else float(not done)

#         memory.push(aug_state, action, reward, next_aug_state, mask) # Append transition to memory

#         state = next_state

#     if total_numsteps > args.num_steps:
#         break

#     # writer.add_scalar('reward/train', episode_reward, i_episode)
#     print("Episode: {}, total numsteps: {}, episode steps: {}, reward: {}".format(i_episode, total_numsteps, episode_steps, round(episode_reward, 2)))

#     if i_episode % 10 == 0 and args.eval is True:
#         avg_reward = 0.
#         episodes = 5
#         for _  in range(episodes):
#             state = env.reset()
#             episode_reward = 0
#             done = False
#             belief = vi.get_belief()
#             sads_array=np.empty((0,s_dim*2+a_dim))
#             while not done:
#                 aug_state = np.hstack([state, belief.numpy()])
#                 action = agent.select_action(aug_state, evaluate=True)

#                 next_state, reward, done, _ = env.step(action)
#                 episode_reward += reward
#                 sads_array = np.vstack([sads_array, 
#                                         np.hstack([state, action, next_state-state])])
#                 belief = vi.get_belief(sads_array=sads_array)


#                 state = next_state
#             avg_reward += episode_reward
#         avg_reward /= episodes
#         agent.save_checkpoint(env_name="custom_"+env_str"_bamdp_realbamdpdebug")


#         # writer.add_scalar('avg_reward/test', avg_reward, i_episode)

#         print("----------------------------------------")
#         print("Test Episodes: {}, Avg. Reward: {}".format(episodes, round(avg_reward, 2)))
#         print("----------------------------------------")

# # env.close()