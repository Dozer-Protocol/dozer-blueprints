# Copyright 2022 Hathor Labs
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from hathor.conf.settings import HathorSettings

SETTINGS = HathorSettings(
    P2PKH_VERSION_BYTE=b"\x49",
    MULTISIG_VERSION_BYTE=b"\x87",
    NETWORK_NAME="nano-testnet-alpha",
    BOOTSTRAP_DNS=["alpha.nano-testnet.hathor.network"],
    # Genesis stuff
    GENESIS_OUTPUT_SCRIPT=bytes.fromhex(
        "76a91478e804bf8aa68332c6c1ada274ac598178b972bf88ac"
    ),
    GENESIS_BLOCK_TIMESTAMP=1677601898,
    GENESIS_BLOCK_NONCE=7881594,
    GENESIS_BLOCK_HASH=bytes.fromhex(
        "000003472f6a17c2199e24c481a4326c217d07376acd9598651f8413c008554d"
    ),
    GENESIS_TX1_NONCE=110,
    GENESIS_TX1_HASH=bytes.fromhex(
        "0008f0e9dbe6e4bbc3a85fce7494fee70011b9c7e72f5276daa2a235355ac013"
    ),
    GENESIS_TX2_NONCE=180,
    GENESIS_TX2_HASH=bytes.fromhex(
        "008d81d9d58a43fd9649f33483d804a4417247b4d4e4e01d64406c4177fee0c2"
    ),
    # tx weight parameters. With these settings, tx weight is always 8
    MIN_TX_WEIGHT_K=0,
    MIN_TX_WEIGHT_COEFFICIENT=0,
    MIN_TX_WEIGHT=8,
    CHECKPOINTS=[],
    ENABLE_NANO_CONTRACTS=True,
    ENABLE_ON_CHAIN_BLUEPRINTS=True,
    NC_ON_CHAIN_BLUEPRINT_ALLOWED_ADDRESSES=[
        'WWFiNeWAFSmgtjm4ht2MydwS5GY3kMJsEK',
        'WQFDxic8xWWnMLL4aE5abY2XRKPNvGhtjY',
    ],
    BLUEPRINTS={
        bytes.fromhex(
            "27db2b0b1a943c2714fb19d190ce87dc0094bba463b26452dd98de21a42e96a0"
        ): "Dozer_Pool_v1_1",
        bytes.fromhex(
            "8e424db8e5664ade76226356bcf5ef6ad9d0879bdad6377db835868b17c443ba"
        ): "Oasis",
        bytes.fromhex(
            "7b3ae18c763b2254baf8b9801bc0dcd3e77db57d7de7fd34cc62b526aa91d9fb"
        ): "Crowdsale",
        bytes.fromhex(
            "6cfdd13e8b9c689b8d87bb8100b4e580e0e9d20ee75a8c5aee9e7bef51e0b1a0"
        ): "Dao",
        bytes.fromhex(
            "a9bb69a0ff0cb89c45faf7ef3fcccd14b9f89a4be9f50a230c41c88b698f3c14"
        ): "Khensu",
        bytes.fromhex(
            "ac6bf4f6a89a34e81a21a6e07e24f07739af5c3d6f4c15e16c5ae4e4108aaa48"
        ): "Stake",
        bytes.fromhex(
            "42e7f272b6b966f26576a5c1d0c9637f456168c85e18a3e86c0c60e909a93275"
        ): "Vesting",
        bytes.fromhex(
            "d6c09caa2f1f7ef6a6f416301c2b665e041fa819a792e53b8409c9c1aed2c89a"
        ): "DozerPoolManager",
        bytes.fromhex(
            "a1a655a7ae9a74a000b11ecc570a8a026a7804827d26dd0e30bc00dd3659f6cf"
        ): "TokenManager",
        bytes.fromhex(
            "698fdf724332cc10ec6cd97f36963794a9da515b2114864cad2898a18e917b68"
        ): "DozerTools",
    },
    SOFT_VOIDED_TX_IDS=list(
        map(
            bytes.fromhex,
            [
                "0000003dd5802b05f430a1f54304879173550c0944b49d74321bb9125ee727cb",
            ],
        )
    ),
)
