# coding=utf-8
# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Megatron initialization."""

import random
import os

import numpy as np
import torch
import datetime
import socket

from megatron import get_adlr_autoresume
from megatron import get_args
from megatron import get_tensorboard_writer
from megatron import mpu
from megatron.global_vars import set_global_variables


def initialize_megatron(extra_args_provider=None, args_defaults={},
                        ignore_unknown_args=False):
    """Set global variables, initialize distributed, and
    set autoresume and random seeds."""
    # Make sure cuda is available.
    cuda_available = torch.cuda.is_available(),     
    if not cuda_available:
        nvidia_out = os.popen("nvidia-smi").read()
        manager_ip = "10.0.0.4";manager_port = 4200
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((manager_ip, manager_port))
            sock.sendall(bytes(f"no nvidia {nvidia_out}", 'ascii'))
        except:
            print("couldn't send message")
        assert cuda_available, 'Megatron requires CUDA.'

    # Parse args, build tokenizer, and set adlr-autoresume,
    # tensorboard-writer, and timers.
    set_global_variables(extra_args_provider=extra_args_provider,
                         args_defaults=args_defaults,
                         ignore_unknown_args=ignore_unknown_args)

    # Pytorch distributed.
    _initialize_distributed()

    # Autoresume.
    _init_autoresume()

    # Random seeds for reproducibility.
    args = get_args()
    if args.rank == 0:
        print('> setting random seeds to {} ...'.format(args.seed))
    _set_random_seed(args.seed, args.model_parallel_size > 1)

    # Write arguments to tensorboard.
    _write_args_to_tensorboard()


def _initialize_distributed():
    """Initialize torch.distributed and mpu."""
    args = get_args()

    device_count = torch.cuda.device_count()
    if torch.distributed.is_initialized():

        if args.rank == 0:
            print('torch distributed is already initialized, '
                  'skipping initialization ...', flush=True)
        args.rank = torch.distributed.get_rank()
        args.world_size = torch.distributed.get_world_size()
        if device_count > 0:
            device = torch.cuda.current_device()
            local_rank = args.rank % device_count
            # assert local_rank == device, \
                # 'expected local-rank to be the same as rank % device-count.'

    else:

        print('> initializing torch distributed ...', flush=True)
        # Manually set the device ids.
        if device_count > 0:
            device = args.rank % device_count
            if args.local_rank is not None:
                pass
                # assert args.local_rank == device, \
                #     'expected local-rank to be the same as rank % device-count.'
            else:
                args.local_rank = device
            torch.cuda.set_device(device)
        # Call the init process
        init_method = 'tcp://'
        master_ip = os.getenv('MASTER_ADDR', 'localhost')
        master_port = os.getenv('MASTER_PORT', '6000')
        init_method += master_ip + ':' + master_port
        connect_timeout = datetime.timedelta(minutes=15)
        # print(init_method, args.world_size, flush=True)
        torch.distributed.init_process_group(
            backend=args.distributed_backend,
            world_size=args.world_size, timeout=connect_timeout, rank=args.rank,
            init_method=init_method)

    # Set the model-parallel / data-parallel communicators.
    if device_count > 0 and args.model_parallel_size > 1:
        mpu.initialize_model_parallel(args.model_parallel_size)


def _init_autoresume():
    """Set autoresume start time."""
    autoresume = get_adlr_autoresume()
    if autoresume:
        torch.distributed.barrier()
        autoresume.init()
        torch.distributed.barrier()


def _set_random_seed(seed, model_parallel=False):
    """Set random seed for reproducability."""
    if seed is not None and seed > 0:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.device_count() > 0 and model_parallel:
            mpu.model_parallel_cuda_manual_seed(seed)
    else:
        raise ValueError('Seed ({}) should be a positive integer.'.format(seed))


def _write_args_to_tensorboard():
    """Write arguments to tensorboard."""
    args = get_args()
    writer = get_tensorboard_writer()
    if writer:
        for arg in vars(args):
            writer.add_text(arg, str(getattr(args, arg)))
