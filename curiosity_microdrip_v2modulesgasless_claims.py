"""
Gasless Claims Harvester Module
Monitors protocols with gasless claiming (Gelato/Biconomy) for own addresses.
Defensive: Only executes if reward > 2x gas reimbursement cost.
"""
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from web3 import Web3
from web3.types import Wei, HexBytes

logger = logging.getLogger(__name__)

@dataclass
class ClaimableReward:
    protocol: str
    contract_address: str
    claimable_amount: Wei  # In protocol's token
    usd_value: float
    gasless_supported: bool
    gelato_task_id: Optional[str] = None

class GaslessClaimsDetector:
    """Defensive gasless claims harvester with simulation-first approach."""
    
    # Protocols with known gasless claiming integrations
    GASLESS_PROTOCOLS = {
        'uniswap_v3_staking': {
            'contract': '0x1f98407aaB862CdDeF78Ed252D6f557aA5b0f00d',
            'gelato_integration': True,
            'min_reward_usd': 0.10
        },
        'aave_v3': {
            'contract': '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2',
            'gelato_integration': True,
            'min_reward_usd': 0.15
        },
        'compound_v3': {
            'contract': '0xc3d688B66703497DAA19211EEdff47f25384cdc3',
            'gelato_integration': False,
            'biconomy_integration': True,
            'min_reward_usd': 0.20
        }
    }
    
    def __init__(self, web3_client, wallet_address: str):
        """
        Initialize detector.
        
        Args:
            web3_client: Web3 instance with RPC connection
            wallet_address: Ethereum address to monitor
        """
        self.web3 = web3_client
        self.wallet_address = Web3.to_checksum_address(wallet_address)
        self.gelato_api_key = None  # Would be loaded from env
        
        logger.info(f"GaslessClaimsDetector initialized for {self.wallet_address}")
    
    def check_all_protocols(self) -> List[ClaimableReward]:
        """
        Check all configured protocols for claimable rewards.
        Returns list of opportunities meeting minimum threshold.
        """
        opportunities = []
        
        for protocol_name, config in self.GASLESS_PROTOCOLS.items():
            try:
                reward = self._check_single_protocol(protocol_name, config)
                if reward and reward.usd_value >= config['min_reward_usd']:
                    opportunities.append(reward)
                    logger.info(f"Found claimable reward: {protocol_name} - ${reward.usd_value:.4f}")
            except Exception as e:
                logger.error(f"Failed to check {protocol_name}: {str(e)}")
                continue
        
        return opportunities
    
    def _check_single_protocol(self, protocol_name: str, config: Dict) -> Optional[ClaimableReward]:
        """
        Check single protocol for claimable rewards.
        Implements protocol-specific logic with error handling.
        """
        try:
            contract_address = Web3.to_checksum_address(config['contract'])
            
            if protocol_name == 'uniswap_v3_staking':
                return self._check_uniswap_v3(contract_address)
            elif protocol_name == 'aave_v3':
                return self._check_aave_v3(contract_address)
            elif protocol_name == 'compound_v3':
                return self._check_compound_v3(contract_address)
            else:
                logger.warning(f"Unknown protocol: {protocol_name}")
                return None
                
        except Exception as e:
            logger.error(f"Protocol check failed for {protocol_name}: {str(e)}")
            return None
    
    def _check_uniswap_v3(self, contract_address: str) -> Optional[ClaimableReward]:
        """Check Uniswap V3 staking rewards."""
        # Simplified ABI for reward checking
        reward_abi = '[{"constant":true,"inputs":[{"name":"tokenId","type":"uint256"}],"name":"rewards","outputs":[{"name":"reward","type":"uint256"}],"type":"function"}]'
        
        try:
            contract = self.web3.eth.contract(
                address=contract_address,
                abi=reward_abi
            )
            
            # In production, would fetch all positions for wallet
            # For MVP, checking single test position
            token_id = 12345  # Would be fetched from position manager
            reward_amount = contract.functions.rewards(token_id).call()
            
            if reward_amount > 0:
                # Convert to USD (simplified - would use price feed)
                usd_value = self.web3.from_wei(reward_amount, 'ether') * 3000  # ETH price
                
                return ClaimableReward(
                    protocol='uniswap_v3_staking',
                    contract_address=contract_address,
                    claimable_amount=reward_amount,
                    usd_value=usd_value,
                    gasless_supported=True
                )
        
        except Exception as e:
            logger.error(f"Uniswap V3 check failed: {str(e)}")
        
        return None
    
    def _check_aave_v3(self, contract_address: str) -> Optional[ClaimableReward]:
        """Check Aave V3 rewards."""
        # Placeholder implementation
        # Would use Aave's rewards controller contract
        return None
    
    def _check_compound_v3(self, contract_address: str) -> Optional[ClaimableReward]:
        """Check Compound V3 rewards."""
        # Placeholder implementation
        return None
    
    def estimate_gasless_cost(self, protocol: str) -> float:
        """
        Estimate Gelato/Biconomy gas reimbursement cost.
        Returns cost in USD.
        """
        # Fetch current gas price and estimate
        gas_price = self.web3.eth.gas_price
        estimated_gas = 150000  # Typical claim transaction
        
        # Gelato adds ~20% premium
        gelato_premium = 1.2
        
        # Convert to USD
        eth_cost = (estimated_gas * gas_price * gelato_premium) / 1e18
        eth_price_usd = 3000  # Would fetch from price feed
        
        return eth_cost * eth_price_usd
    
    def should_execute(self, reward: ClaimableReward) -> Tuple[bool, str]:
        """
        Decision logic: Only execute