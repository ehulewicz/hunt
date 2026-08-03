import os

os.environ.setdefault(
    "PERSONAL_OS_API_DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/postgres",
)
os.environ.setdefault(
    "PERSONAL_OS_API_SUPABASE_URL",
    "https://exampleprojectref.supabase.co",
)
os.environ.setdefault(
    "PERSONAL_OS_API_ALLOWED_USER_ID",
    "c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f",
)
os.environ.setdefault("PERSONAL_OS_API_ALLOWED_EMAIL", "eahtree@gmail.com")
