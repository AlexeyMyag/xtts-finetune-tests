import wandb
import torch
from torch.optim import Adam
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader

from TTS.tts.datasets import load_tts_samples
from TTS.config.shared_configs import BaseDatasetConfig
from dvae_dataset import DVAEDataset

# WHERE
from models.dvae import DiscreteVAE
from utils.arch_utils import TorchMelSpectrogram

dvae_checkpoint = '/home/ubuntu/test_tts/SimpleTTS/xtts/run/training/XTTS_v2.0_original_model_files/dvae.pth'
mel_norm_file = '/home/ubuntu/test_tts/SimpleTTS/xtts/run/training/XTTS_v2.0_original_model_files/mel_stats.pth'

config_dataset = BaseDatasetConfig(
    formatter="ljspeech",
    dataset_name="ljspeech",
    path="/home/ubuntu/test_tts/sapien-formatted-english-22050",
    meta_file_train="/home/ubuntu/test_tts/sapien-formatted-english-22050/metadata_norm.txt",
    language="en",
)

# Add here the configs of the datasets
DATASETS_CONFIG_LIST = [config_dataset]
GRAD_CLIP_NORM = 0.5
LEARNING_RATE = 5e-05

dvae = DiscreteVAE(
    channels=80,
    normalization=None,
    positional_dims=1,
    num_tokens=1024,
    codebook_dim=512,
    hidden_dim=512,
    num_resnet_blocks=3,
    kernel_size=3,
    num_layers=2,
    use_transposed_convs=False,
)

dvae.load_state_dict(torch.load(dvae_checkpoint), strict=False)
dvae.cuda()
opt = Adam(dvae.parameters(), lr=LEARNING_RATE)
torch_mel_spectrogram_dvae = TorchMelSpectrogram(
    mel_norm_file=mel_norm_file, sampling_rate=22050
).cuda()

train_samples, eval_samples = load_tts_samples(
    DATASETS_CONFIG_LIST,
    eval_split=True,
    eval_split_max_size=256,
    eval_split_size=0.01,
)

eval_dataset = DVAEDataset(eval_samples, 22050, True)
train_dataset = DVAEDataset(train_samples, 22050, False)
epochs = 20
eval_data_loader = DataLoader(
    eval_dataset,
    batch_size=3,
    shuffle=False,
    drop_last=False,
    collate_fn=eval_dataset.collate_fn,
    num_workers=0,
    pin_memory=False,
)

train_data_loader = DataLoader(
    train_dataset,
    batch_size=3,
    shuffle=False,
    drop_last=False,
    collate_fn=train_dataset.collate_fn,
    num_workers=4,
    pin_memory=False,
)

torch.set_grad_enabled(True)
dvae.train()

wandb.init(project='train_dvae')
wandb.watch(dvae)

def to_cuda(x: torch.Tensor) -> torch.Tensor:
    if x is None:
        return None
    if torch.is_tensor(x):
        x = x.contiguous()
        if torch.cuda.is_available():
            x = x.cuda(non_blocking=True)
    return x

@torch.no_grad()
def format_batch(batch):
    if isinstance(batch, dict):
        for k, v in batch.items():
            batch[k] = to_cuda(v)
    elif isinstance(batch, list):
        batch = [to_cuda(v) for v in batch]

    try:
        batch['mel'] = torch_mel_spectrogram_dvae(batch['wav'])
        remainder = batch['mel'].shape[-1] % 4
        if remainder:
            batch['mel'] = batch['mel'][:, :, :-remainder]
    except NotImplementedError:
        pass
    return batch

for i in range(epochs):
    for cur_step, batch in enumerate(train_data_loader):
        opt.zero_grad()
        batch = format_batch(batch)
        recon_loss, commitment_loss, out = dvae(batch['mel'])
        total_loss = recon_loss + commitment_loss
        total_loss.backward()
        clip_grad_norm_(dvae.parameters(), GRAD_CLIP_NORM)
        opt.step()

        log = {'epoch': i,
               'cur_step': cur_step,
               'loss': total_loss.item(),
               'recon_loss': recon_loss.item(),
               'commit_loss': commitment_loss.item()}
        print(f"epoch: {i}", f"step: {cur_step}", f'loss - {total_loss.item()}', f'recon_loss - {recon_loss.item()}', f'commit_loss - {commitment_loss.item()}')
        wandb.log(log)
        torch.cuda.empty_cache()
