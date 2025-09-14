# Copyright 2025 Hathor Labs
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

"""
Comprehensive test suite for DozerTools contract.

This main test file imports and consolidates all DozerTools test categories:
- Core functionality tests
- Edge case and boundary condition tests
- Validation and error handling tests

Run with: pytest test_dozer_tools.py
"""

# Import all test categories for DozerTools - pytest will automatically discover them
from tests.nanocontracts.blueprints.test_dozer_tools_core import *
from tests.nanocontracts.blueprints.test_dozer_tools_edge_cases import *
from tests.nanocontracts.blueprints.test_dozer_tools_validation import *