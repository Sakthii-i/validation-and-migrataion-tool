"""
Utility script to encrypt credential.txt file.

Usage:
  python encrypt_credentials.py

This will read credential.txt (plain text), encrypt it with the password,
and save it back as encrypted content.
"""

import sys
from pathlib import Path

# Add parent directory to path to import auth module
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.auth import _encrypt_content, CREDENTIAL_FILE_PATH


def encrypt_credential_file():
    """Encrypt the credential file with the configured password."""
    
    # The password that matches HARDCODED_FILE_PASSWORD_HASH
    # Current hash: 10a6e6cc8311a3e2bcc09bf6c199adecd5dd59408c343e926b129c4914f3cb01
    # This corresponds to password: "Sakthi@123"
    password = input("Enter the credential file password: ").strip()
    
    if not CREDENTIAL_FILE_PATH.exists():
        print(f"Error: {CREDENTIAL_FILE_PATH} not found")
        return
    
    # Read current content
    current_content = CREDENTIAL_FILE_PATH.read_text(encoding="utf-8").strip()
    
    # Check if already encrypted (Fernet tokens start with 'gAAAAA')
    if current_content.startswith("gAAAAA"):
        print("File appears to already be encrypted.")
        choice = input("Re-encrypt anyway? (y/n): ").strip().lower()
        if choice != "y":
            print("Aborted.")
            return
    
    # Encrypt
    try:
        encrypted = _encrypt_content(current_content, password)

        # Write encrypted
        CREDENTIAL_FILE_PATH.write_text(encrypted, encoding="utf-8")
        print(f"Successfully encrypted {CREDENTIAL_FILE_PATH}")
        print("\nThe file is now encrypted. Use the same password to unlock it in the application.")
        
    except Exception as e:
        print(f"Error encrypting file: {e}")


if __name__ == "__main__":
    encrypt_credential_file()
