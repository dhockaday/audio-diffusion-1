import torch
import torch.nn as nn
import torch.nn.functional as F

from torchaudio.transforms import MelSpectrogram

def adversarial_g_loss(features_stft_disc_G_x, features_wave_disc_G_x, lengths_stft, lengths_wave):
    wave_disc_names = lengths_wave.keys()
    
    stft_loss = F.relu(1-features_stft_disc_G_x[-1]).sum(dim=3).squeeze()/lengths_stft[-1].squeeze()
    wave_loss = torch.cat([F.relu(1-features_wave_disc_G_x[key][-1]).sum(dim=2).squeeze()/lengths_wave[key][-1].squeeze() for key in wave_disc_names])
    loss = torch.cat([stft_loss, wave_loss]).mean()
    
    return loss

def feature_loss(features_stft_disc_x, features_wave_disc_x, features_stft_disc_G_x, features_wave_disc_G_x, lengths_wave, lengths_stft):
    wave_disc_names = lengths_wave.keys()
    
    stft_loss = torch.stack([((feat_x-feat_G_x).abs().sum(dim=-1)/lengths_stft[i].view(-1,1,1)).sum(dim=-1).sum(dim=-1) for i, (feat_x, feat_G_x) in enumerate(zip(features_stft_disc_x, features_stft_disc_G_x))], dim=1).mean(dim=1, keepdim=True)
    wave_loss = torch.stack([torch.stack([(feat_x-feat_G_x).abs().sum(dim=-1).sum(dim=-1)/lengths_wave[key][i] for i, (feat_x, feat_G_x) in enumerate(zip(features_wave_disc_x[key], features_wave_disc_G_x[key]))], dim=1) for key in wave_disc_names], dim=2).mean(dim=1)
    loss = torch.cat([stft_loss, wave_loss], dim=1).mean()
    
    return loss

def spectral_reconstruction_loss(x, G_x, sr, device, eps=1e-4,):
    L = 0
    for i in range(6,12):
        s = 2**i
        alpha_s = (s/2)**0.5
        melspec = MelSpectrogram(sample_rate=sr, n_fft=s, hop_length=s//4, n_mels=8, wkwargs={"device": device}).to(device)
        S_x = melspec(x)
        S_G_x = melspec(G_x)
        
        loss = (S_x-S_G_x).abs().sum() + alpha_s*(((torch.log(S_x.abs()+eps)-torch.log(S_G_x.abs()+eps))**2).sum(dim=-2)**0.5).sum()
        L += loss
    
    return L

def adversarial_d_loss(features_stft_disc_x, features_wave_disc_x, features_stft_disc_G_x, features_wave_disc_G_x, lengths_stft, lengths_wave):
    wave_disc_names = lengths_wave.keys()
    
    real_stft_loss = F.relu(1-features_stft_disc_x[-1]).sum(dim=3).squeeze()/lengths_stft[-1].squeeze()
    real_wave_loss = torch.stack([F.relu(1-features_wave_disc_x[key][-1]).sum(dim=-1).squeeze()/lengths_wave[key][-1].squeeze() for key in wave_disc_names], dim=1)
    real_loss = torch.cat([real_stft_loss.view(-1,1), real_wave_loss], dim=1).mean()
    
    generated_stft_loss = F.relu(1+features_stft_disc_G_x[-1]).sum(dim=-1).squeeze()/lengths_stft[-1].squeeze()
    generated_wave_loss = torch.stack([F.relu(1+features_wave_disc_G_x[key][-1]).sum(dim=-1).squeeze()/lengths_wave[key][-1].squeeze() for key in wave_disc_names], dim=1)
    generated_loss = torch.cat([generated_stft_loss.view(-1,1), generated_wave_loss], dim=1).mean()
    
    return real_loss + generated_loss



#Taken from https://github.com/rishikksh20/TFGAN/blob/main/utils/timeloss.py
#Licensed under the Apache Licence 2.0

class TimeDomainLoss(nn.Module):
    """Time domain loss module."""
    def __init__(self, batch_size ,segment_size=3200, 
                 T_frame_sizes=[1, 240, 480, 960],
                 T_hop_sizes=[1, 120, 240, 480]):
        super(TimeDomainLoss, self).__init__()
        self.shapes = []
        self.strides = []
        self.seg_size = segment_size
        for i in range(len(T_frame_sizes)):
            no_over_lap = T_frame_sizes[i] - T_hop_sizes[i]
            self.shapes.append((batch_size,
                               (segment_size - no_over_lap)//T_hop_sizes[i],
                                T_frame_sizes[i]
                                ))
            self.strides.append((segment_size,
                                 T_hop_sizes[i],
                                 1
                                 ))
        self.len = len(self.shapes)
        
    def forward(self, y, y_hat):
        """Calculate time domain loss
        Args:
            y (Tensor): real waveform
            y_hat (Tensor): fake waveform
        Return: 
            total_loss (Tensor): total loss of time domain
            
        """

        # Energy loss & Time loss & Phase loss
        loss_e = torch.zeros(self.len).to(y)
        loss_t = torch.zeros(self.len).to(y)
        loss_p = torch.zeros(self.len).to(y)
        
        for i in range(self.len):
            y_tmp = torch.as_strided(y, self.shapes[i], self.strides[i])
            y_hat_tmp = torch.as_strided(y_hat, self.shapes[i], self.strides[i])
            
            loss_e[i] = F.l1_loss(torch.mean(y_tmp**2, dim=-1), torch.mean(y_hat_tmp**2, dim=-1))
            loss_t[i] = F.l1_loss(torch.mean(y_tmp, dim=-1), torch.mean(y_hat_tmp, dim=-1))
            if i == 0:
                y_phase = F.pad(y_tmp.transpose(1, 2), (1, 0), "constant", 0) - F.pad(y_tmp.transpose(1, 2), (0, 1), "constant", 0)
                y_hat_phase = F.pad(y_hat_tmp.transpose(1, 2), (1, 0), "constant", 0) - F.pad(y_hat_tmp.transpose(1, 2), (0, 1), "constant", 0)
            else:
                y_phase = F.pad(y_tmp, (1, 0), "constant", 0) - F.pad(y_tmp, (0, 1), "constant", 0)
                y_hat_phase = F.pad(y_hat_tmp, (1, 0), "constant", 0) - F.pad(y_hat_tmp, (0, 1), "constant", 0)
            loss_p[i] = F.l1_loss(y_phase, y_hat_phase)
        
        total_loss = torch.sum(loss_e) + torch.sum(loss_t) + torch.sum(loss_p)
        
        return total_loss