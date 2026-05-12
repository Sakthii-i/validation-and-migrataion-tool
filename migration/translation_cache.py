import hashlib
import json
import threading
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import sqlite3
import os

class TranslationCache:
    """Persistent cache for translated SQL with connection pooling."""

    # Bump this version when translation rules change to invalidate stale entries.
    RULES_VERSION = "2"
    
    def __init__(self, db_path: str = "translation_cache.db", ttl_days: int = 30):
        self.db_path = db_path
        self.ttl = timedelta(days=ttl_days)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_db()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a persistent SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        return self._conn

    def _init_db(self):
        """Initialize SQLite database"""
        with self._lock:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS translations (
                    hash TEXT PRIMARY KEY,
                    original_sql TEXT,
                    translated_sql TEXT,
                    explanation TEXT,
                    created_at TIMESTAMP,
                    last_accessed TIMESTAMP,
                    access_count INTEGER DEFAULT 1
                )
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_last_accessed 
                ON translations(last_accessed)
            ''')
            conn.commit()
    
    def _compute_hash(self, sql: str) -> str:
        """Compute hash of SQL for cache key, including rules version."""
        normalized = ' '.join(sql.split())
        versioned = f"v{self.RULES_VERSION}:{normalized}"
        return hashlib.sha256(versioned.encode()).hexdigest()
    
    def get(self, sql: str) -> Optional[Dict[str, Any]]:
        """Get cached translation if exists and not expired"""
        hash_key = self._compute_hash(sql)
        
        with self._lock:
            conn = self._get_conn()
            c = conn.cursor()
            
            c.execute('''
                SELECT original_sql, translated_sql, explanation, created_at
                FROM translations
                WHERE hash = ?
            ''', (hash_key,))
            
            row = c.fetchone()
            
            if row:
                original, translated, explanation, created_at = row
                created = datetime.fromisoformat(created_at)
                
                if datetime.now() - created < self.ttl:
                    c.execute('''
                        UPDATE translations 
                        SET last_accessed = ?, access_count = access_count + 1
                        WHERE hash = ?
                    ''', (datetime.now().isoformat(), hash_key))
                    conn.commit()
                    
                    return {
                        'original': original,
                        'translated': translated,
                        'explanation': explanation
                    }
        
        return None
    
    def set(self, sql: str, translated: str, explanation: str = ""):
        """Store translation in cache"""
        hash_key = self._compute_hash(sql)
        now = datetime.now().isoformat()
        
        with self._lock:
            conn = self._get_conn()
            c = conn.cursor()
            
            c.execute('''
                INSERT OR REPLACE INTO translations 
                (hash, original_sql, translated_sql, explanation, created_at, last_accessed)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (hash_key, sql, translated, explanation, now, now))
            
            conn.commit()
    
    def clear_all(self):
        """Remove all cache entries completely"""
        with self._lock:
            conn = self._get_conn()
            c = conn.cursor()
            
            c.execute('DELETE FROM translations')
            
            deleted = c.rowcount
            conn.commit()
            
            return deleted

    def clear_expired(self):
        """Remove expired entries"""
        cutoff = (datetime.now() - self.ttl).isoformat()
        
        with self._lock:
            conn = self._get_conn()
            c = conn.cursor()
            
            c.execute('''
                DELETE FROM translations
                WHERE created_at < ?
            ''', (cutoff,))
            
            deleted = c.rowcount
            conn.commit()
        
        return deleted
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            conn = self._get_conn()
            c = conn.cursor()
            
            c.execute('SELECT COUNT(*) FROM translations')
            total = c.fetchone()[0]
            
            c.execute('''
                SELECT SUM(access_count), AVG(access_count) 
                FROM translations
            ''')
            row = c.fetchone()
            total_access, avg_access = row if row else (0, 0)
        
        return {
            'total_entries': total,
            'total_accesses': total_access or 0,
            'avg_access_per_entry': round(avg_access, 2) if avg_access else 0
        }