import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

print(f"URL: {url}")
print(f"Key starts with: {key[:10]}...")

try:
    supabase: Client = create_client(url, key)
    print("[OK] Supabase Client Initialized")
    
    # Try a simple select to verify connection and table existence
    res = supabase.table("users").select("*").limit(1).execute()
    print("[OK] Connection Successful!")
    print(f"Users found: {len(res.data)}")

except Exception as e:
    print(f"[ERROR] Connection Failed: {e}")
    print("\nTip: Make sure you have run the SQL script in the Supabase SQL Editor to create the 'users' table.")
