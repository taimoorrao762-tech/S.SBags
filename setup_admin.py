import mysql.connector
import bcrypt
from mysql.connector import Error
import os
import getpass
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def setup_admin():
    print("=== S.S BAGS Secure Admin Setup ===")
    
    admin_name = input("Enter Admin Name: ").strip()
    admin_email = input("Enter Admin Email: ").strip()
    
    if not admin_name or not admin_email:
        print("Error: Name and Email are required.")
        return

    # Securely prompt for password
    while True:
        password = getpass.getpass("Enter Admin Password: ")
        if len(password) < 8:
            print("Password must be at least 8 characters long.")
            continue
            
        confirm_password = getpass.getpass("Confirm Password: ")
        if password != confirm_password:
            print("Passwords do not match. Try again.")
            continue
        break

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
        
        # Create bcrypt hash
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Check if email already exists
        cursor.execute("SELECT id FROM admins WHERE email = %s", (admin_email,))
        if cursor.fetchone():
            print("Updating password for existing admin...")
            cursor.execute(
                "UPDATE admins SET password = %s, name = %s WHERE email = %s",
                (hashed_password, admin_name, admin_email)
            )
        else:
            print("Creating new admin user...")
            cursor.execute(
                "INSERT INTO admins (name, email, password, role, status) VALUES (%s, %s, %s, %s, %s)",
                (admin_name, admin_email, hashed_password, 'admin', 'active')
            )
        
        conn.commit()
        print(f"\\n✅ Admin account securely configured for: {admin_email}")
        
    except Error as e:
        print(f"\\n❌ Database error: {e}")
    except Exception as e:
        print(f"\\n❌ Error: {e}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    setup_admin()
