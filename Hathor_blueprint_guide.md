# Hathor Network Blueprint Development - New Features Guide

This document provides a comprehensive guide to the new features available in Hathor Network's blueprint development system. These features are part of a beta branch and are not yet deployed or documented in the official documentation.

## Overview

Hathor Network uses **Blueprints** (smart contracts) written in Python that inherit from the `Blueprint` base class. This guide covers three major new features:

1. **Contract Calling Methods** - Call methods on other contracts
2. **Contract Creation Method** - Create new contracts from within a contract
3. **Token Creation Method** - Create new tokens from within a contract

## 1. Contract Calling Methods

### Description

Blueprints can now call methods on other deployed contracts, enabling complex inter-contract interactions.

### Available Methods

#### `call_public_method`

Calls a public method on another contract with the ability to transfer tokens.

```python
result = self.syscall.call_public_method(
    contract_id,           # ContractId of the target contract
    method_name,          # String name of the method to call
    actions,              # List of NCAction (deposits/withdrawals)
    *args,                # Method arguments
    **kwargs              # Method keyword arguments
)
```

#### `call_view_method`

Calls a view (read-only) method on another contract.

```python
result = self.syscall.call_view_method(
    contract_id,          # ContractId of the target contract
    method_name,          # String name of the method to call
    *args,                # Method arguments
    **kwargs              # Method keyword arguments
)
```

### Key Rules and Restrictions

-   `call_public_method` can **only** be called from public methods (not from view methods)
-   `call_view_method` can be called from both public and view methods
-   Cannot call the `initialize` method of another contract
-   Cannot call methods on the same contract (self-calling)
-   Target contract must be initialized before calling its methods
-   Actions are used to transfer tokens between contracts during the call

### Action Types for Token Transfers

```python
from hathor.nanocontracts.types import NCDepositAction, NCWithdrawalAction

# Deposit tokens to the target contract
actions = [NCDepositAction(token_uid=token_uid, amount=amount)]

# Withdraw tokens from the target contract
actions = [NCWithdrawalAction(token_uid=token_uid, amount=amount)]
```

### Example Usage

```python
# Example from Oasis blueprint calling Dozer pool
def user_deposit(self, ctx: Context, timelock: int, htr_price: Amount) -> None:
    # Prepare actions to add liquidity to Dozer pool
    actions = [
        NCAction(NCActionType.DEPOSIT, HTR_UID, htr_amount),
        NCAction(NCActionType.DEPOSIT, self.token_b, deposit_amount),
    ]

    # Call Dozer pool to add liquidity
    result = self.call_public_method(self.dozer_pool, "add_liquidity", actions)

    # Get quote from Dozer pool (view method)
    quote = self.call_view_method(self.dozer_pool, "quote_token_b", loss_amount)
```

### Reference Files

-   **Implementation**: `hathor/nanocontracts/blueprint_env.py` (lines 175-185, 200-205)
-   **Test Examples**: `tests/nanocontracts/test_call_other_contract.py`
-   **Real Usage**: `hathor/nanocontracts/blueprints/oasis.py` (multiple examples)

## 2. Contract Creation Method

### Description

Blueprints can create new contracts dynamically, enabling factory patterns and complex contract architectures.

### Method Signature

```python
contract_id, result = self.syscall.create_contract(
    blueprint_id,         # BlueprintId of the contract to create
    salt,                 # bytes - unique salt for deterministic address generation
    actions,              # List of NCAction for initial deposits to new contract
    *args,                # Arguments passed to the new contract's initialize method
    **kwargs              # Keyword arguments passed to initialize method
)
```

### Key Features

-   **Deterministic Addresses**: Contract ID is deterministically generated based on:
    -   Parent contract ID
    -   Salt (must be unique and non-empty)
    -   Blueprint ID
-   **Returns**: Both the new contract ID and the result of its `initialize` method
-   **Initial Funding**: Actions can deposit tokens into the new contract during creation
-   **Automatic Initialization**: The new contract's `initialize` method is called automatically

### Restrictions

-   Can only be called from public methods (not view methods)
-   Salt must be non-empty and unique
-   Target blueprint must exist
-   Cannot create a contract that already exists

### Example Usage

```python
# Example from Khensu blueprint creating a Dozer pool
def migrate_liquidity(self, ctx: Context) -> None:
    # Generate unique salt
    salt = self.token_uid + HTR_UID + bytes(str(ctx.timestamp), 'utf-8')

    # Prepare initial deposits for the new contract
    actions = [
        NCAction(NCActionType.DEPOSIT, HTR_UID, self.liquidity_amount),
        NCAction(NCActionType.DEPOSIT, self.token_uid, self.token_reserve),
    ]

    # Create the Dozer pool contract
    pool_id, _ = self.syscall.create_contract(
        self.dozer_pool_blueprint_id,  # Blueprint to instantiate
        salt,                          # Unique salt
        actions,                       # Initial deposits
        HTR_UID,                      # token_a parameter for initialize
        self.token_uid,               # token_b parameter for initialize
        0,                            # fee parameter
        50,                           # protocol_fee parameter
    )

    # Store the new contract ID
    self.lp_contract = pool_id
```

### Utility Functions

```python
from hathor.nanocontracts.utils import derive_child_contract_id

# Calculate what the contract ID will be before creation
expected_id = derive_child_contract_id(parent_id, salt, blueprint_id)
```

### Reference Files

-   **Implementation**: `hathor/nanocontracts/blueprint_env.py` (lines 230-240)
-   **Test Examples**: `tests/nanocontracts/test_contract_create_contract.py`
-   **Real Usage**: `hathor/nanocontracts/blueprints/khensu.py` (lines 380-400)

## 3. Token Creation Method

### Description

Blueprints can create new tokens as children of the contract, enabling token factory patterns and dynamic token generation.

### Method Signature

```python
token_uid = self.syscall.create_token(
    token_name,           # str - Human-readable name of the token
    token_symbol,         # str - Symbol/ticker of the token (must be unique per contract)
    amount,               # int - Initial amount to mint to the contract
    mint_authority=True,  # bool - Whether contract receives mint authority
    melt_authority=True   # bool - Whether contract receives melt authority
)
```

### Key Features

-   **Child Tokens**: Created tokens are children of the contract
-   **Deterministic IDs**: Token UID is derived from contract ID and token symbol
-   **Initial Minting**: Specified amount is minted to the contract immediately
-   **Authority Management**: Contract can optionally receive mint/melt authorities
-   **Unique Symbols**: Token symbol must be unique within the contract scope

### Restrictions

-   Can only be called from public methods (not view methods)
-   Token symbol must be unique per contract
-   Cannot use reserved token names/symbols (like "Hathor" or "HTR")
-   Must follow token naming conventions

### Example Usage

```python
# Example from test blueprint
@public(allow_deposit=True)
def create_token(
    self,
    ctx: Context,
    token_name: str,
    token_symbol: str,
    amount: int,
    mint_authority: bool,
    melt_authority: bool,
) -> None:
    # Create the token
    token_uid = self.syscall.create_token(
        token_name,
        token_symbol,
        amount,
        mint_authority,
        melt_authority
    )

    # Token is now available in contract's balance
    # Contract has mint/melt authorities if requested
```

### Utility Functions

```python
from hathor.nanocontracts.utils import derive_child_token_id

# Calculate what the token UID will be before creation
expected_token_uid = derive_child_token_id(contract_id, token_symbol)
```

### Reference Files

-   **Implementation**: `hathor/nanocontracts/blueprint_env.py` (lines 260-275)
-   **Test Examples**: `tests/nanocontracts/test_token_creation.py`
-   **Usage Example**: Test blueprint in `tests/nanocontracts/test_token_creation.py` (lines 32-42)

## Additional Syscall Methods

The `BlueprintEnvironment` provides many other useful syscall methods:

### Balance and Authority Management

```python
# Balance queries
balance = self.syscall.get_current_balance(token_uid)
balance_before = self.syscall.get_balance_before_current_call(token_uid)

# Authority checks
can_mint = self.syscall.can_mint(token_uid)
can_melt = self.syscall.can_melt(token_uid)

# Token operations (requires authorities)
self.syscall.mint_tokens(token_uid, amount)
self.syscall.melt_tokens(token_uid, amount)
self.syscall.revoke_authorities(token_uid, revoke_mint=True, revoke_melt=True)
```

### Contract Information

```python
# Get current contract information
contract_id = self.syscall.get_contract_id()
blueprint_id = self.syscall.get_blueprint_id(contract_id)

# Emit custom events
self.syscall.emit_event(data_bytes)

# Upgrade contract blueprint
self.syscall.change_blueprint(new_blueprint_id)
```

## Action Types Reference

```python
from hathor.nanocontracts.types import (
    NCDepositAction,
    NCWithdrawalAction,
    NCGrantAuthorityAction,
    NCAcquireAuthorityAction
)

# Deposit tokens
NCDepositAction(token_uid=token_uid, amount=amount)

# Withdraw tokens
NCWithdrawalAction(token_uid=token_uid, amount=amount)

# Grant mint/melt authorities
NCGrantAuthorityAction(token_uid=token_uid, mint=True, melt=True)

# Acquire authorities from another contract
NCAcquireAuthorityAction(token_uid=token_uid, mint=True, melt=True)
```

## Important Notes

1. **Beta Features**: These features are in a beta branch and not yet in production
2. **Testing**: Comprehensive test suites are available in the `tests/nanocontracts/` directory
3. **Error Handling**: All methods can raise `NCFail` exceptions for various error conditions
4. **Gas/Fuel**: All operations consume computational resources (fuel)
5. **Deterministic**: Contract and token creation is deterministic for reproducibility

## Complete Reference Files

-   **Core Implementation**: `hathor/nanocontracts/blueprint_env.py`
-   **Blueprint Base Class**: `hathor/nanocontracts/blueprint.py`
-   **Test Suite**: `tests/nanocontracts/` (entire directory)
-   **Example Blueprints**:
    -   `hathor/nanocontracts/blueprints/oasis.py` (contract calling)
    -   `hathor/nanocontracts/blueprints/khensu.py` (contract creation)
-   **Types and Utilities**: `hathor/nanocontracts/types.py`, `hathor/nanocontracts/utils.py`

This documentation should provide a comprehensive understanding of these new features for efficient blueprint development on the Hathor Network.
