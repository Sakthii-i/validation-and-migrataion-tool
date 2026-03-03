"""
Utility script to view decrypted credentials from credential.txt

Usage:
  python view_credentials.py
"""
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

CREDENTIAL_FILE_PATH = Path(__file__).parent / "credential.txt"
ENCRYPTION_SALT = b"validation_tool_salt_v1"


def derive_key_from_password(password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ENCRYPTION_SALT,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def decrypt_and_display():
    if not CREDENTIAL_FILE_PATH.exists():
        print(f"Error: {CREDENTIAL_FILE_PATH} not found")
        return
    
    password = input("Enter credential file password: ").strip()
    
    try:
        # Read encrypted content
        encrypted_content = CREDENTIAL_FILE_PATH.read_text(encoding="utf-8").strip()
        
        # Decrypt
        key = derive_key_from_password(password)
        f = Fernet(key)
        decrypted = f.decrypt(encrypted_content.encode("utf-8")).decode("utf-8")
        
        print("\n" + "="*60)
        print("DECRYPTED CREDENTIALS")
        print("="*60)
        print(decrypted)
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ Failed to decrypt: Invalid password or corrupted file")
        print(f"Error details: {e}\n")


if __name__ == "__main__":
    decrypt_and_display()
