import numpy as np
import torch
import random
import copy

from utils import log_gaussian, kld, logsumexp, torch_from_numpy
from model_bamdp import Encoder, Decoder#, PenaltyModel

device = torch.device('cpu')

class baseVI:
    def __init__(self, args_init_dict):

        offline_data = args_init_dict["offline_data"]
        s_dim = args_init_dict["s_dim"]
        a_dim = args_init_dict["a_dim"]
        z_dim = args_init_dict["z_dim"]
        env = args_init_dict["env"]
        self.policy = args_init_dict["policy"]

        train_valid_ratio = 0.2
        self.validdata_num = int(train_valid_ratio*len(offline_data))
        self.valid_ave_num=1 # validlossを計算するためのサンプル数

        self.nu = 1e0 # KLDによる正則化の重み

        self.gamma = 0.98
        self.s_dim = s_dim
        self.a_dim = a_dim
        self.sa_dim = s_dim + a_dim
        self.sas_dim = 2*s_dim + a_dim
        self.z_dim = z_dim
        self.init_state_fn      = env.reset
        self.rew_fn             = env.env.env.rew_fn
        self._max_episode_steps = env.spec.max_episode_steps
        self.action_space       = env.action_space

        self.offline_data = copy.deepcopy(offline_data) # [M, N , |SAS'R|] : M ... num of MDPs, N ... trajectory length, |SAS'R| ... dim of (s,a,s',r)

        for m in range(len(self.offline_data)):
            self.offline_data[m][:, (self.sa_dim):(self.sas_dim)] -= self.offline_data[m][:, :(self.s_dim)] # ds = s'-s

        self.enc = Encoder(self.s_dim, self.a_dim, self.z_dim)         # q(z|D^train_m)
        self.dec = Decoder(self.s_dim, self.a_dim, self.z_dim)         # p(ds|s,a,z)
        self.prior = torch.nn.Parameter(torch.zeros(2*z_dim), requires_grad=False)  # [mean, logvar] for VAE

        self.mulogvar_list_for_mixture_of_gaussian_belief=None

        # self.lam=1e-4 # ペナルティの係数？
        # self.penalty_model = PenaltyModel(s_dim, a_dim, z_dim) # ibisには要らない
        # self.train_g_m_list=None
        # self.valid_g_m_list=None
        self.initial_belief = torch.nn.Parameter(torch.zeros(2*z_dim))  # [mean, logvar]



        # only used for debug
        self.debug_realenv = env
        self.debug_c_list = args_init_dict["debug_info"][:,1]
        self.debug_realenv_rolloutdata = [None]*len(offline_data)

    def reset_encdec(self):
        self.enc = Encoder(self.s_dim, self.a_dim, self.z_dim)         # q(z|D^train_m)
        self.dec = Decoder(self.s_dim, self.a_dim, self.z_dim)         # p(ds|s,a,z)


    def store_encdec(self):
        self.enc_store = copy.deepcopy(self.enc)         # q(z|D^train_m)
        self.dec_store = copy.deepcopy(self.dec)         # p(ds|s,a,z)


    def restore_encdec(self):
        self.enc = copy.deepcopy(self.enc_store)         # q(z|D^train_m)
        self.dec = copy.deepcopy(self.dec_store)         # p(ds|s,a,z)


    def save(self, ckpt_name="vi_base_ckpt"):
        torch.save({'enc_state_dict': self.enc.state_dict(),
                    'dec_state_dict': self.dec.state_dict(),
                    'prior': self.prior
                   },ckpt_name)

    def load(self, ckpt_name="vi_base_ckpt"):
        checkpoint = torch.load(ckpt_name)
        self.enc.load_state_dict(checkpoint['enc_state_dict'])
        self.dec.load_state_dict(checkpoint['dec_state_dict'])
        self.prior = checkpoint['prior']
        self.update_mulogvar_list_for_mixture_of_gaussian_belief()
        print("load", ckpt_name)


    def reset(self, z=None, fix_init=False):
        self.sim_timestep=0
        if z is None:
            std = torch.exp(0.5 * self.initial_belief[self.z_dim:])
            eps = torch.randn_like(std)
            self.sim_z = (eps*std+self.initial_belief[:self.z_dim]).detach().flatten()
        else:
            self.sim_z = z.flatten()
        self.sim_s = self.init_state_fn(fix_init=fix_init).flatten()
        # self.online_data = torch.empty((0,self.sas_dim+1))
        self.sim_b = self.get_belief(sads_array=None).detach().numpy().flatten()
        sb =np.hstack([self.sim_s, self.sim_b])
        return sb



    def step(self, a, update_belief=True, penalty_flag=False):
        a= a.flatten()
        saz = np.hstack([self.sim_s, a, self.sim_z]).reshape(1,-1)
        ds_mulogvar = self.dec.my_np_forward(saz).flatten()
        ds_mu = ds_mulogvar[:self.s_dim]
        eps = np.random.randn(len(ds_mu)) #* 0. # デバッグ：確定的システムにするなら0をかける
        std = np.exp(0.5 * ds_mulogvar[self.s_dim:])
        ds = (eps*std+ds_mu)
        rew = self.rew_fn(self.sim_s, a)

        self.sim_s = self.sim_s + ds
        done = False
        if self.sim_timestep>=(self._max_episode_steps-1):
            done=True
        if np.abs(self.sim_s).max()>20:
            done = True
        self.sim_timestep+=1

        # if penalty_flag:
        #     with torch.no_grad():
        #         penalty = self.penalty_model(torch.hstack([saz, self.train_g_m_list[self.sim_m]]))
        #     rew -= self.lam * penalty.flatten()[0]

        # current_data = torch.hstack([self.sim_s, a, self.sim_s+ds, torch.Tensor([rew])])
        # self.online_data = torch.vstack([self.online_data, current_data])
        # if update_belief:
        #     self.sim_b = self.get_belief(self.online_data[:, :(self.sas_dim)]).detach().flatten()

        sb = np.hstack([self.sim_s, self.sim_b])
        return sb, rew, done, {}


    def rollout_episode_simenv(self, z_mulogvar, len_data, random_stop=True, zmean=False):

        stateaction_history=[]
        while True:
            if zmean:
                z = z_mulogvar.numpy().flatten()[:1]
            else:
                z = self.sample_z(z_mulogvar, 1).numpy().flatten()
            sb = self.reset(fix_init=True, z=z)
            state = sb[:self.s_dim]
            while True:
                if np.abs(state).max()>1e3:
                    break
                action = self.policy(state, evaluate=self.policy_evaluate)
                stateaction_history.append(np.hstack([state.flatten(), action.flatten(), z]))
                next_sb, reward, done, _ = self.step(action)
                state = next_sb[:self.s_dim]
                if random_stop:
                    if np.random.rand()>self.gamma:
                        break
                else:
                    if done:
                        return np.array(stateaction_history)
            if len(stateaction_history)>(5*len_data):
                break
        stateaction_history = np.array(stateaction_history)
        np.random.shuffle(stateaction_history)
        return stateaction_history[:len_data]


    def get_sim_rollout_data_fixlen(self):
        self.dec.my_np_compile()
        self.policy_evaluate=True
        self.simenv_rolloutdata = [None]*len(self.offline_data)
        for m in range(len(self.offline_data)):
            print(m," ", end="")
            self.simenv_rolloutdata[m] = self.rollout_episode_simenv(self.mulogvar_list_for_mixture_of_gaussian_belief[m], len_data=200, random_stop=False, zmean=True)
        print(" ")


    def get_sim_rollout_data_randomlen(self):
        self.dec.my_np_compile()
        self.policy_evaluate=False
        self.simenv_rolloutdata = [None]*len(self.offline_data)
        for m in range(len(self.offline_data)):
            print(m," ", end="")
            self.simenv_rolloutdata[m] = self.rollout_episode_simenv(self.mulogvar_list_for_mixture_of_gaussian_belief[m], len_data=200, random_stop=True, zmean=False)
        print(" ")


    def get_real_rollout_data(self):
        self.policy_evaluate= True
        def rollout_episode_realenv(temp_c):
            state = self.debug_realenv.reset(fix_init=True)
            done = False
            state_history = []
            self.debug_realenv.env.env.set_params(c=temp_c)
            while not done:
                state_history.append(state)
                with torch.no_grad():
                    action = self.policy(state, evaluate=self.policy_evaluate)  # Sample action from policy
                next_state, reward, done, _ = self.debug_realenv.step(action) # Step
                state = next_state
            return np.array(state_history)

        for m in range(len(self.offline_data)):
            print(m," ", end="")
            self.debug_realenv_rolloutdata[m] = rollout_episode_realenv(self.debug_c_list[m])
        print(" ")



    def get_belief(self, sads_array=None):
        with torch.no_grad():
            if sads_array is None or len(sads_array)==0:
                return self.initial_belief.detach()
            else:
                return self.enc(sads_array[:, :(self.sas_dim)])


    def train_unweighted_vae(self, num_iter, lr, early_stop_step):

        param_list = list(self.enc.parameters())+list(self.dec.parameters())
        loss_fn = self._loss_train_unweighted_vae
        ret = self._train(num_iter, lr, early_stop_step, loss_fn, param_list)
        self.update_mulogvar_list_for_mixture_of_gaussian_belief()
        return ret

    def _train(self, num_iter, lr, early_stop_step, loss_fn, param_list):


        optimizer = torch.optim.Adam(param_list, lr=lr)

        total_idx_list = np.array( range(len(self.offline_data)) )
        train_idx_list = copy.deepcopy(total_idx_list)[self.validdata_num:]
        valid_idx_list = copy.deepcopy(total_idx_list)[:self.validdata_num]
        best_valid_loss = 1e10
        best_valid_iter = 0

        train_curve = []
        valid_curve = []
        for i in range(num_iter):

            with torch.no_grad():
                valid_loss_list = []
                for _ in range(self.valid_ave_num):
                    temp_valid_loss = 0
                    for m in valid_idx_list:
                        temp_valid_loss += loss_fn(m,valid_flag=True).item()
                    temp_valid_loss /= len(valid_idx_list)
                    valid_loss_list.append(temp_valid_loss)
                valid_loss_list = np.array(valid_loss_list)
                valid_loss = valid_loss_list.mean()

            if best_valid_loss>=valid_loss:
                best_valid_loss = valid_loss
                best_valid_iter = i
                self.store_encdec()

            if (i-best_valid_iter)>early_stop_step:
                break


            random.shuffle(train_idx_list)
            train_loss = 0
            for m in train_idx_list:
                loss = loss_fn(m)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                train_loss += loss.item()
            train_loss /= len(train_idx_list)

            print("train: iter",i,
                  " trainloss {:.5f}".format(train_loss),
                  " validloss {:.5f}".format(valid_loss)+"±{:.5f}".format(valid_loss_list.std()),
                  " bestvalidloss {:.5f}".format(best_valid_loss),
                  " last_update", i-best_valid_iter)
            train_curve.append(train_loss)
            valid_curve.append(valid_loss)
        self.restore_encdec()

        print("train: fin")
        return train_curve, valid_curve


    def sample_z(self, z_mulogvar, datanum):
        # # reparametrization trick type A
        std = torch.exp(0.5 * z_mulogvar[self.z_dim:])
        eps = torch.randn(self.z_dim)
        z = (eps*std+z_mulogvar[:self.z_dim]) * torch.ones(datanum, self.z_dim)

        # reparametrization trick type B
        # std = torch.exp(0.5 * z_mulogvar[self.z_dim:]) * torch.ones(datanum, self.z_dim)
        # eps = torch.randn(datanum, self.z_dim)
        # z = eps * std + z_mulogvar[:self.z_dim]
        return z


    def _loss_train_unweighted_vae(self, m, valid_flag=False):
        temp_data_m = self.offline_data[m]
        z_mulogvar = self.enc(temp_data_m[:, :(self.sas_dim)])
        z = self.sample_z(z_mulogvar, 1).flatten() * torch.ones(len(temp_data_m), self.z_dim)

        # if not valid_flag:
        #     z = self.sample_z(z_mulogvar, 1).flatten() * torch.ones(len(temp_data_m), self.z_dim)
        # else:
        #     z = z_mulogvar[:self.z_dim] * torch.ones(len(temp_data_m), self.z_dim)
        #     raise Exception

        saz = torch.cat([temp_data_m[:, :(self.sa_dim)], z], dim=1)
        ds_mulogvar = self.dec(saz)
        ds_m = temp_data_m[:, (self.sa_dim):(self.sas_dim)]

        loss = 0

        # Approximate of E_{z~q}[ - log p(y|x,z) ]
        loss += - log_gaussian(ds_m, # y
                               ds_mulogvar[:, :self.s_dim], # mu
                               ds_mulogvar[:, self.s_dim:] # logvar
                               ).sum()

        # nu * E_{z~q}[ log q(z) - log p(z) ]
        loss += self.nu * kld(z_mulogvar[:self.z_dim],
                              z_mulogvar[self.z_dim:],
                              self.prior[:self.z_dim],
                              self.prior[self.z_dim:])

        return loss


    def update_mulogvar_list_for_mixture_of_gaussian_belief(self):
        with torch.no_grad():
            self.mulogvar_list_for_mixture_of_gaussian_belief = []
            for m in range(len(self.offline_data)):
                temp_data_m = self.offline_data[m]
                z_mulogvar = self.enc(temp_data_m[:, :(self.sas_dim)])
                self.mulogvar_list_for_mixture_of_gaussian_belief.append(z_mulogvar)
