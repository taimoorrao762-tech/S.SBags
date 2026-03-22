import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv

load_dotenv()

def update_schema():
    print("Updating S.S BAGS Database Schema...")
    try:
        conn = mysql.connector.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASSWORD', 'root'),
            database=os.getenv('DB_NAME', 'ss_bags'),
            port=int(os.getenv('DB_PORT', 3306))
        )
        cursor = conn.cursor()

        # Add is_verified column
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT FALSE")
            print("✅ Added 'is_verified' column to users table.")
        except Error as err:
            if err.errno == 1060: # Duplicate column error
                print("ℹ️ 'is_verified' column already exists.")
            else:
                print(f"❌ Error adding 'is_verified': {err}")

        # Add reset_token column
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN reset_token VARCHAR(255) DEFAULT NULL")
            print("✅ Added 'reset_token' column to users table.")
        except Error as err:
            if err.errno == 1060:
                print("ℹ️ 'reset_token' column already exists.")
            else:
                print(f"❌ Error adding 'reset_token': {err}")

        # Remove phone column
        try:
            cursor.execute("ALTER TABLE users DROP COLUMN phone")
            print("✅ Dropped 'phone' column from users table.")
        except Error as err:
            if err.errno == 1091: # Column doesn't exist error
                print("ℹ️ 'phone' column already removed.")
            else:
                print(f"❌ Error dropping 'phone': {err}")

        conn.commit()
        print("Database update complete!")

    except Error as e:
        print(f"❌ Database connection error: {e}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    update_schema()
