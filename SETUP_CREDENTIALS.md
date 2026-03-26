# Setup Instructions

## Encrypted Credentials Setup

Your Snowflake and Databricks credentials are stored encrypted in `api/credential.txt` using Fernet encryption.

### 1. Environment Variable Setup

Before running the application, you must set the `CREDENTIAL_PASSWORD` environment variable:

**Option A: Using .env file (Local Development)**

1. Create a `.env` file in the project root with your credential password
2. Add: `CREDENTIAL_PASSWORD=<your-password-here>`
3. Make sure `.env` is in your `.gitignore` (it is already configured)
4. To run the app locally: the `.env` file will be automatically loaded

**Option B: Docker/Production**

Set the environment variable before running:
```bash
# Linux/Mac
export CREDENTIAL_PASSWORD=<your-password-here>

# Windows PowerShell
$env:CREDENTIAL_PASSWORD = "<your-password-here>"

# Or in docker-compose.yml:
environment:
  - CREDENTIAL_PASSWORD=<your-password-here>
```

⚠️ **NEVER commit or expose the actual password in code, documentation, or version control!**

### 2. Password Validation

The password is validated using SHA256 hashing in `api/auth.py`:
```
HARDCODED_FILE_PASSWORD_HASH = "cde0a2b0950a47712ac7040323874f3fa2cc292d37d2cc798d270b9be067add2"
```

This ensures:
- The actual password is never stored in code
- Only the hash is stored for validation
- The app can auto-decrypt credentials securely

### 3. How It Works

1. **User selects Snowflake** from the source engine dropdown
2. **User clicks "Establish Connections"** button  
3. **App automatically:**
   - Reads `CREDENTIAL_PASSWORD` from environment
   - Decrypts `api/credential.txt` using the password
   - Loads Snowflake and Databricks credentials
   - Establishes connections
4. **No manual password input required** - credentials stay encrypted and secure

### 4. Security Best Practices

✅ **DO:**
- Keep `.env` file in `.gitignore` (already configured)
- Use strong passphrases for production
- Rotate credentials regularly
- Store credentials in secure vaults (for production)

❌ **DON'T:**
- Commit `.env` file to git
- Expose the password in logs or error messages
- Store plaintext credentials anywhere
- Share the `.env` file in version control

### 5. Troubleshooting

**Error: "CREDENTIAL_PASSWORD environment variable is not set"**
- Make sure `.env` file exists in the project root
- Or set the environment variable before running the app
- Verify the password matches the hash

**Error: "Failed to decrypt credentials"**
- The password might be incorrect
- The encrypted file might be corrupted
- Make sure `api/credential.txt` exists and is properly encrypted

To re-encrypt credentials:
```bash
cd api
python encrypt_credentials.py
# Enter your password when prompted
```
