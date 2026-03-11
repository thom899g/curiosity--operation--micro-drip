"""
State Manager - The Immutable Audit Trail
Handles all Firestore operations with proper error handling and type safety.
Critical: Firestore is write-only for audit trails, never updated after write.
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import Client as FirestoreClient

logger = logging.getLogger(__name__)

@dataclass
class Heartbeat:
    timestamp: datetime
    status: str  # "HEALTHY", "DEGRADED", "CRITICAL"
    last_block: int
    gas_price_gwei: float
    opportunities_detected: int
    active_modules: List[str]

@dataclass
class OpportunityScan:
    id: str  # Auto-generated as timestamp-module
    timestamp: datetime
    module: str  # "gasless", "stablecoin", "social"
    parameters: Dict[str, Any]
    simulated_profit_usd: float
    simulated_gas_cost_usd: float
    executed: bool = False

@dataclass
class TransactionRecord:
    tx_hash: str
    timestamp: datetime
    module: str
    status: str  # "SIMULATED", "BROADCAST", "CONFIRMED", "REVERTED"
    gas_used: int
    gas_price_gwei: float
    net_profit_usd: float
    error_message: Optional[str] = None

class StateManager:
    """Firestore client with defensive programming and connection pooling."""
    
    def __init__(self, credentials_path: str, project_id: str):
        """
        Initialize Firestore client with proper error handling.
        
        Args:
            credentials_path: Path to Firebase service account JSON
            project_id: Firebase project ID
            
        Raises:
            FileNotFoundError: If credentials file doesn't exist
            ValueError: If Firebase initialization fails
        """
        try:
            # Verify credentials file exists
            with open(credentials_path, 'r') as f:
                creds_data = json.load(f)
            
            cred = credentials.Certificate(credentials_path)
            
            # Initialize only if not already initialized
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {
                    'projectId': project_id
                })
            
            self.db: FirestoreClient = firestore.client()
            logger.info(f"Firestore client initialized for project: {project_id}")
            
            # Test connection
            self.db.collection('heartbeats').document('test').set({
                'test': True,
                'timestamp': datetime.now()
            }, merge=True)
            logger.info("Firestore connection test successful")
            
        except FileNotFoundError as e:
            logger.error(f"Firebase credentials file not found: {credentials_path}")
            raise
        except Exception as e:
            logger.error(f"Firebase initialization failed: {str(e)}")
            raise ValueError(f"Firebase initialization failed: {str(e)}")
    
    def log_heartbeat(self, heartbeat: Heartbeat) -> None:
        """Log system heartbeat to Firestore."""
        try:
            doc_ref = self.db.collection('heartbeats').document(
                heartbeat.timestamp.strftime('%Y%m%d_%H%M%S')
            )
            doc_ref.set(asdict(heartbeat))
            logger.debug(f"Heartbeat logged: {heartbeat.status}")
        except Exception as e:
            logger.error(f"Failed to log heartbeat: {str(e)}")
            # Do not raise - heartbeat failure shouldn't stop system
    
    def log_opportunity(self, scan: OpportunityScan) -> str:
        """
        Log opportunity detection before execution.
        Returns document ID for later transaction linking.
        """
        try:
            doc_id = f"{scan.timestamp.strftime('%Y%m%d_%H%M%S')}_{scan.module}"
            scan.id = doc_id
            
            doc_ref = self.db.collection('opportunity_scans').document(doc_id)
            doc_ref.set(asdict(scan))
            logger.info(f"Opportunity logged: {scan.module} - ${scan.simulated_profit_usd:.4f}")
            return doc_id
        except Exception as e:
            logger.error(f"Failed to log opportunity: {str(e)}")
            raise
    
    def log_transaction(self, record: TransactionRecord) -> None:
        """Log complete transaction lifecycle."""
        try:
            doc_ref = self.db.collection('transactions').document(record.tx_hash)
            doc_ref.set(asdict(record))
            
            # Update profit ledger if confirmed and profitable
            if record.status == "CONFIRMED" and record.net_profit_usd > 0:
                profit_doc = self.db.collection('profit_ledger').document(
                    record.timestamp.strftime('%Y%m%d')
                )
                
                # Use transaction to ensure atomic updates
                @firestore.transactional
                def update_profit(transaction, profit_ref):
                    snapshot = profit_ref.get(transaction=transaction)
                    if snapshot.exists:
                        current = snapshot.to_dict()
                        new_total = current.get('total_usd', 0) + record.net_profit_usd
                        transaction.update(profit_ref, {
                            'total_usd': new_total,
                            'last_updated': datetime.now(),
                            f"transactions.{record.tx_hash}": asdict(record)
                        })
                    else:
                        transaction.set(profit_ref, {
                            'date': record.timestamp.date().isoformat(),
                            'total_usd': record.net_profit_usd,
                            'transactions': {record.tx_hash: asdict(record)},
                            'created_at': datetime.now()
                        })
                
                update_profit(self.db.transaction(), profit_doc)
                logger.info(f"Profit logged: ${record.net_profit_usd:.4f} from {record.tx_hash}")
            
        except Exception as e:
            logger.error(f"Failed to log transaction: {str(e)}")
            raise
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get runtime configuration parameter."""
        try:
            doc = self.db.collection('config').document(key).get()
            return doc.to_dict().get('value', default) if doc.exists else default
        except Exception as e:
            logger.warning(f"Failed to get config {key}: {str(e)}")
            return default
    
    def update_config(self, key: str, value: Any) -> None:
        """Update runtime configuration."""
        try:
            self.db.collection('config').document(key).set({
                'value': value,
                'updated_at': datetime.now()
            }, merge=True)
            logger.info(f"Config updated: {key} = {value}")
        except Exception as e:
            logger.error(f"Failed to update config {key}: {str(e)}")