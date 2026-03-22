import mysql.connector
import bcrypt
from mysql.connector import Error
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def update_admin_password():
    try:
        # Connect to database using environment variables
        conn = mysql.connector.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASSWORD', 'root'),
            database=os.getenv('DB_NAME', 'ss_bags'),
            port=int(os.getenv('DB_PORT', 3306))
        )
        cursor = conn.cursor()
        
        # Create new bcrypt hash for 'admin123'
        password = 'admin123'
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Update the admin password
        cursor.execute(
            "UPDATE admins SET password = %s WHERE email = %s",
            (hashed_password, 'admin@ssbags.com')
        )
        
        conn.commit()
        print(f"Admin password updated successfully!")
        print(f"New hash: {hashed_password}")
        
        # Verify the update
        cursor.execute("SELECT id, name, email FROM admins WHERE email = %s", ('admin@ssbags.com',))
        result = cursor.fetchone()
        print(f"Admin user: {result}")
        
    except Error as e:
        print(f"Database error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    update_admin_password()