
import sqlite3
import getpass
from argon2 import PasswordHasher
from database import DB_PATH

def create_super_admin():
    hasher = PasswordHasher()

    # Allow only the first Super Admin to be created
    with sqlite3.connect(DB_PATH) as connection:
        existing = connection.execute(
            "SELECT COUNT(*) FROM admins WHERE role = ?",
            ("super_admin",)
        ).fetchone()[0]

        if existing > 0:
            print("A Super Admin already exists.")
            return

        name = input("Enter Super Admin full name: ").strip()
        username = input("Enter username: ").strip().lower()

        if not name or not username:
            print("Name and username are required.")
            return

        password = getpass.getpass("Enter password: ")
        confirm = getpass.getpass("Confirm password: ")

        if password != confirm:
            print("Passwords do not match.")
            return

        if len(password) < 12:
            print("Password must be at least 12 characters.")
            return

        password_hash = hasher.hash(password)

        try:
            connection.execute(
                """
                INSERT INTO admins
                    (name, username, password_hash, role, active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, username, password_hash,
                 "super_admin", 1)
            )
            connection.commit()

            print("Super Admin created successfully.")

        except sqlite3.IntegrityError:
            print("Username already exists.")

if __name__ == "__main__":
    create_super_admin()
