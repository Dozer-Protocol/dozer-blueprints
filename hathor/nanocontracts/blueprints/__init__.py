# Copyright 2023 Hathor Labs
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

from typing import TYPE_CHECKING, Type

from hathor.nanocontracts.blueprints.bet import Bet
from hathor.nanocontracts.blueprints.mvp_pool import MVP_Pool
from hathor.nanocontracts.blueprints.dozer_pool import Liquidity_Pool

if TYPE_CHECKING:
    from hathor.nanocontracts.blueprint import Blueprint


_blueprints_mapper: dict[str, Type["Blueprint"]] = {
    "Bet": Bet,
    "MVP_Pool": MVP_Pool,
    "Liquidity_Pool": Liquidity_Pool,
}

__all__ = ["Bet", "MVP_Pool", "Liquidity_Pool"]