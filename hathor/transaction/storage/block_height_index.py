# Copyright 2021 Hathor Labs
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

from typing import List, Optional, Tuple

from hathor.transaction.genesis import BLOCK_GENESIS
from hathor.util import not_none

BLOCK_GENESIS_HASH: bytes = not_none(BLOCK_GENESIS.hash)


class BlockHeightIndex:
    """Store the block hash for each given height
    """
    def __init__(self) -> None:
        self._index: List[bytes] = [BLOCK_GENESIS_HASH]

    def add(self, height: int, block_hash: bytes, *, can_reorg: bool = False) -> None:
        """Add new element to the cache. Must not be called for re-orgs.
        """
        if len(self._index) < height:
            raise ValueError(f'parent hash required (current height: {len(self._index)}, new height: {height})')
        elif len(self._index) == height:
            self._index.append(block_hash)
        elif self._index[height] != block_hash:
            if can_reorg:
                del self._index[height:]
                self._index.append(block_hash)
            else:
                raise ValueError('adding would cause a re-org, use can_reorg=True to accept re-orgs')
        else:
            # nothing to do (there are more blocks, but the block at height currently matches the added block)
            pass

    def get(self, height: int) -> Optional[bytes]:
        """ Return the block hash for the given height, or None if it is not set.
        """
        if len(self._index) <= height:
            return None
        return self._index[height]

    def get_tip(self) -> bytes:
        """ Return the best block hash, it returns the genesis when there is no other block
        """
        return self._index[-1]

    def get_height_tip(self) -> Tuple[int, bytes]:
        """ Return the best block height and hash, it returns the genesis when there is no other block
        """
        height = len(self._index) - 1
        return height, self._index[height]
