from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
import jwt  # PyJWT (NOT the jwt library)
import bcrypt
import mysql.connector
from mysql.connector import Error
from mysql.connector.pooling import MySQLConnectionPool
from typing import List, Optional
import json
import os
import re
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
import uuid
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
import aiosmtplib
from email.message import EmailMessage
from fastapi.responses import RedirectResponse

# Load environment variables
load_dotenv()

# ============ CONFIGURATION ============
app = FastAPI(title="PK Shop API")
security = HTTPBearer()

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

DATABASE_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'root'),
    'database': os.getenv('DB_NAME', 'ss_bags'),
    'port': int(os.getenv('DB_PORT', 3306))
}

# Add SSL config if provided (required for Aiven)
ssl_ca = os.getenv('DB_SSL_CA')
if ssl_ca:
    # Handling SSL for Aiven (Vercel environments often provide CA as a string)
    if "-----BEGIN CERTIFICATE-----" in ssl_ca:
        ca_path = "/tmp/ca.pem"
        try:
            with open(ca_path, "w") as f:
                f.write(ssl_ca)
            DATABASE_CONFIG['ssl_ca'] = ca_path
        except Exception as e:
            print(f"Warning: Could not write CA certificate to {ca_path}: {e}")
    else:
        DATABASE_CONFIG['ssl_ca'] = ssl_ca
    
    # Aiven often requires verify_identity as well
    if os.getenv('DB_SSL_VERIFY_IDENTITY', 'false').lower() == 'true':
        DATABASE_CONFIG['ssl_verify_identity'] = True

# Initialize connection pool globally
db_pool = None

def init_db_pool():
    global db_pool
    # Only initialize if not already initialized
    if db_pool is not None:
        return db_pool

    try:
        db_pool = MySQLConnectionPool(
            pool_name="mypool",
            pool_size=10,
            pool_reset_session=True,
            **DATABASE_CONFIG
        )
        print("Database connection pool created successfully")
        return db_pool
    except Error as e:
        print(f"Error creating connection pool: {e}")
        # We don't exit(1) here because it crashes the Vercel function
        # Instead, we let get_db handle the error
        return None

# Initial attempt to create pool
init_db_pool()

SECRET_KEY = os.getenv('SECRET_KEY', 'your-super-secret-key-change-this-to-a-very-long-random-string-please')
if SECRET_KEY == 'your-super-secret-key-change-this-to-a-very-long-random-string-please' and os.getenv('DEV_MODE', '').lower() != 'true':
    raise RuntimeError("CRITICAL ERROR: Default SECRET_KEY is being used in production. Please set SECRET_KEY in the environment properly.")

ALGORITHM = "HS256"

# Allow all origins during development
allowed_origins = [os.getenv('FRONTEND_URL', 'http://localhost:3000')]
if os.getenv('DEV_MODE', 'true').lower() == 'true':
    # By standardizing to true fallback, we ensure dev servers pass CORS checks
    allowed_origins = [
        "http://localhost:8000", "http://127.0.0.1:5500", "http://localhost:5500", 
        "http://127.0.0.1:8000", "null", "http://127.0.0.1:5501", "http://localhost:5501", 
        "http://127.0.0.1:3000", "http://localhost:3000", "*"
    ]
else:
    # If not DEV_MODE, adding the local file execution as fallback
    allowed_origins.extend(["null", "file://"])
    
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods including OPTIONS
    allow_headers=["*"],
)

# Create uploads directory if it doesn't exist
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ============ MODELS ============
from pydantic import BaseModel, EmailStr, validator

class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str

    @validator('password')
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(char.isalpha() for char in v) or not any(char.isdigit() for char in v):
            raise ValueError("Password must contain at least one letter and one number")
        return v

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @validator('new_password')
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(char.isalpha() for char in v) or not any(char.isdigit() for char in v):
            raise ValueError("Password must contain at least one letter and one number")
        return v

class AdminLogin(BaseModel):
    email: EmailStr
    password: str

class Product(BaseModel):
    name: str
    description: str
    price: float
    stock: int
    category: str  # For backward compatibility
    category_id: int  # Foreign key reference to categories table
    color: Optional[str] = None
    material: Optional[str] = None
    size: Optional[str] = None

class OrderItem(BaseModel):
    product_id: int
    quantity: int

class CreateOrder(BaseModel):
    user_id: int
    items: List[OrderItem]
    total_amount: float
    status: str = "pending"

class UpdateOrderStatus(BaseModel):
    status: str

# ============ DATABASE CONNECTION ============
def get_db():
    global db_pool
    if db_pool is None:
        # Try to re-initialize if it failed before
        db_pool = init_db_pool()
        if db_pool is None:
            raise HTTPException(status_code=500, detail="Database connection pool not initialized. Check your environment variables and Aiven connection.")
    
    try:
        conn = db_pool.get_connection()
        conn.autocommit = False
        return conn
    except Error as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============ EMAIL UTILITY ============
async def send_email(to_email: str, subject: str, body: str):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    
    if not smtp_server or not smtp_user or not smtp_password:
        print(f"WARNING: Email not configured. Would have sent: {subject} to {to_email}")
        print(f"Body: {body}")
        return False
        
    message = EmailMessage()
    message["From"] = smtp_user
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    
    try:
        await aiosmtplib.send(
            message,
            hostname=smtp_server,
            port=smtp_port,
            start_tls=True,
            username=smtp_user,
            password=smtp_password,
        )
        return True
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False

# ============ AUTH ENDPOINTS ============
@app.options("/api/auth/register")
async def register_options():
    from starlette.responses import Response
    response = Response()
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

@app.post("/api/auth/register")
@limiter.limit("5/minute")
async def register(request: Request, user: UserRegister):
    print(f"Registration attempt for: {user.email}")  # Debug print
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (user.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        
        hashed_pwd = hash_password(user.password)
        # Generate verification token
        verify_token_str = create_token({"sub": user.email, "type": "verify_email"}, timedelta(hours=24))
        
        cursor.execute(
            "INSERT INTO users (name, email, password, is_verified, created_at) VALUES (%s, %s, %s, FALSE, %s)",
            (user.name, user.email, hashed_pwd, datetime.now())
        )
        conn.commit()
        
        # Send verification email asynchronously
        verification_link = f"{os.getenv('FRONTEND_URL', 'http://127.0.0.1:5500')}/?verify_token={verify_token_str}"
        email_body = f"Hello {user.name},\\n\\nPlease verify your S.S BAGS School Bags account by clicking the link below:\\n{verification_link}\\n\\nThis link will expire in 24 hours."
        await send_email(user.email, "Verify Your S.S BAGS School Bags Account", email_body)
        
        return {"message": "Registration successful. Please check your email to verify your account."}
    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        print(f"Database error in registration: {str(e)}")  # Debug print
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.options("/api/auth/login")
async def login_options():
    from starlette.responses import Response
    response = Response()
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, user: UserLogin):
    print(f"Login attempt for: {user.email}")  # Debug print
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id, name, email, password, is_verified FROM users WHERE email = %s", (user.email,))
        db_user = cursor.fetchone()
        
        if not db_user or not verify_password(user.password, db_user['password']):
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
        if not db_user.get('is_verified', False):
            # Fallback for old accounts without the is_verified column
            if db_user.get('is_verified') is not None:
                raise HTTPException(status_code=403, detail="Email not verified. Please check your inbox for the verification link.")
        
        token = create_token({"sub": str(db_user['id']), "role": "user"})
        
        return {
            "token": token,
            "user": {
                "id": db_user['id'],
                "name": db_user['name'],
                "email": db_user['email']
            }
        }
    except HTTPException:
        raise
    except Error as e:
        print(f"Database error in login: {str(e)}")  # Debug print
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/auth/verify-email")
async def verify_email(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "verify_email":
            raise HTTPException(status_code=400, detail="Invalid token type")
            
        email = payload.get("sub")
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET is_verified = TRUE WHERE email = %s", (email,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
            
        conn.commit()
        return {"message": "Email verified successfully"}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Verification link expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid verification link")
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        if 'cursor' in locals():
            cursor.close()
            conn.close()

@app.post("/api/auth/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(request: Request, payload: ForgotPasswordRequest):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id, name FROM users WHERE email = %s", (payload.email,))
        user = cursor.fetchone()
        
        if user:
            # Generate 1 hour reset token
            reset_token_str = create_token({"sub": payload.email, "type": "reset_password"}, timedelta(hours=1))
            
            # Save token to db for invalidation/replay protection
            cursor.execute("UPDATE users SET reset_token = %s WHERE email = %s", (reset_token_str, payload.email))
            conn.commit()
            
            reset_link = f"{os.getenv('FRONTEND_URL', 'http://127.0.0.1:5500')}/?reset_token={reset_token_str}"
            email_body = f"Hello {user['name']},\\n\\nYou requested a password reset.\\nClick the link below to reset your password:\\n{reset_link}\\n\\nIf you did not request this, please ignore this email.\\nThis link expires in 1 hour."
            
            await send_email(payload.email, "Password Reset Request", email_body)
            
        # Always return success to prevent email enumeration (security best practice)
        return {"message": "If that email is registered, a password reset link has been sent to it."}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/auth/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, payload: ResetPasswordRequest):
    try:
        token_payload = jwt.decode(payload.token, SECRET_KEY, algorithms=[ALGORITHM])
        if token_payload.get("type") != "reset_password":
            raise HTTPException(status_code=400, detail="Invalid token type")
            
        email = token_payload.get("sub")
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # Verify token matches database
        cursor.execute("SELECT id, reset_token FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        
        if not user or user['reset_token'] != payload.token:
            raise HTTPException(status_code=400, detail="Invalid or previously used reset token")
            
        hashed_pwd = hash_password(payload.new_password)
        
        # Update password and clear reset token
        cursor.execute("UPDATE users SET password = %s, reset_token = NULL WHERE email = %s", (hashed_pwd, email))
        conn.commit()
        
        return {"message": "Password successfully reset. You can now log in."}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Password reset link expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid reset link")
    except Error as e:
        if 'conn' in locals():
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cursor' in locals():
            cursor.close()
            conn.close()

@app.options("/api/admin/login")
async def admin_login_options():
    from starlette.responses import Response
    response = Response()
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

@app.post("/api/admin/login")
@limiter.limit("5/minute")
async def admin_login(request: Request, admin: AdminLogin):
    print(f"Admin login attempt for: {admin.email}")  # Debug print
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id, email, password, role FROM admins WHERE email = %s", (admin.email,))
        db_admin = cursor.fetchone()
        
        if not db_admin or not verify_password(admin.password, db_admin['password']):
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
        
        token = create_token({"sub": str(db_admin['id']), "role": db_admin['role']})
        
        return {"token": token, "message": "Admin login successful"}
    except HTTPException:
        raise
    except Error as e:
        print(f"Database error in admin login: {str(e)}")  # Debug print
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============ PRODUCT ENDPOINTS ============
@app.get("/api/products")
async def get_products():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute(
            "SELECT p.id, p.name, p.description, p.price, p.stock, p.category, p.category_id, p.color, p.material, p.size, p.created_at, (SELECT GROUP_CONCAT(image_url) FROM product_images WHERE product_id = p.id) as image_urls FROM products p WHERE p.stock > 0"
        )
        products = cursor.fetchall()
        
        # Process the results to convert image_urls to an array
        for product in products:
            if product['image_urls']:
                product['images'] = product['image_urls'].split(',')
            else:
                product['images'] = []
            # Remove the temporary image_urls field
            del product['image_urls']
        
        return {"products": products}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/products")
async def create_product(product: Product, credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Verify category_id exists
        cursor.execute("SELECT id FROM categories WHERE id = %s", (product.category_id,))
        category_exists = cursor.fetchone()
        if not category_exists:
            raise HTTPException(status_code=400, detail="Category does not exist")
        
        # Get category name based on category_id for backward compatibility
        cursor.execute("SELECT name FROM categories WHERE id = %s", (product.category_id,))
        category_record = cursor.fetchone()
        category_name = category_record['name'] if category_record else product.category
        
        cursor.execute(
            "INSERT INTO products (name, description, price, stock, category, category_id, color, material, size, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (product.name, product.description, product.price, product.stock, category_name, product.category_id, product.color, product.material, product.size, datetime.now())
        )
        product_id = cursor.lastrowid
        conn.commit()
        return {"message": "Product created", "id": product_id}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/products/{product_id}")
async def update_product(product_id: int, product: Product, credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Verify category_id exists
        cursor.execute("SELECT id FROM categories WHERE id = %s", (product.category_id,))
        category_exists = cursor.fetchone()
        if not category_exists:
            raise HTTPException(status_code=400, detail="Category does not exist")
        
        # Get category name based on category_id for backward compatibility
        cursor.execute("SELECT name FROM categories WHERE id = %s", (product.category_id,))
        category_record = cursor.fetchone()
        category_name = category_record['name'] if category_record else product.category
        
        cursor.execute(
            "UPDATE products SET name = %s, description = %s, price = %s, stock = %s, category = %s, category_id = %s, color = %s, material = %s, size = %s WHERE id = %s",
            (product.name, product.description, product.price, product.stock, category_name, product.category_id, product.color, product.material, product.size, product_id)
        )
        conn.commit()
        return {"message": "Product updated"}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/products/{product_id}")
async def delete_product(product_id: int, credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
        conn.commit()
        return {"message": "Product deleted"}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/products/{product_id}/images")
async def upload_product_images(product_id: int, files: List[UploadFile] = File(...), credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    # Check if product exists
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
    finally:
        cursor.close()
        conn.close()
    
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 images allowed per upload")
    
    # Validate file sizes (max 5MB each)
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB in bytes
    for i, file in enumerate(files):
        # Seek to end to get file size, then back to start
        file.file.seek(0, 2)  # Seek to end
        file_size = file.file.tell()
        file.file.seek(0)  # Seek back to beginning
        
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"File {i+1} is too large. Maximum size is 5MB.")
    
    uploaded_images = []
    conn = get_db()
    cursor = conn.cursor()
    
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}
    import imghdr

    try:
        for i, file in enumerate(files):
            # Read file contents
            contents = await file.read()
            
            # Validate magic bytes for true file type (not just content-type header)
            image_type = imghdr.what(None, h=contents)
            
            # Validate extension
            ext = file.filename.split('.')[-1].lower()
            if ext not in ALLOWED_EXTENSIONS or image_type not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"File {i+1} is not a valid image. Only JPG, PNG, and WebP are allowed.")
            
            # Generate secure, unique filename
            filename = f"{uuid.uuid4().hex}.{ext}"
            filepath = os.path.join("uploads", filename)
            
            # Save file
            with open(filepath, "wb") as f:
                f.write(contents)
            
            # Store image record in database
            image_url = f"/uploads/{filename}"
            is_primary = (i == 0)  # First image is primary
            
            cursor.execute(
                "INSERT INTO product_images (product_id, image_url, is_primary, sort_order) VALUES (%s, %s, %s, %s)",
                (product_id, image_url, is_primary, i)
            )
            uploaded_images.append(image_url)
        
        # Update image count
        cursor.execute(
            "UPDATE products SET image_count = (SELECT COUNT(*) FROM product_images WHERE product_id = %s) WHERE id = %s",
            (product_id, product_id)
        )
        
        conn.commit()
        return {"message": f"{len(uploaded_images)} images uploaded successfully", "image_urls": uploaded_images}
    
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.get("/api/products/{product_id}/images")
async def get_product_images(product_id: int):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute(
            "SELECT id, image_url, is_primary, sort_order FROM product_images WHERE product_id = %s ORDER BY sort_order",
            (product_id,)
        )
        images = cursor.fetchall()
        return {"images": images}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/products/{product_id}/images/{image_id}")
async def delete_product_image(product_id: int, image_id: int, credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get image info before deleting
        cursor.execute(
            "SELECT image_url FROM product_images WHERE id = %s AND product_id = %s",
            (image_id, product_id)
        )
        image = cursor.fetchone()
        
        if not image:
            raise HTTPException(status_code=404, detail="Image not found for this product")
        
        # Delete image file from disk
        image_path = os.path.join(os.getcwd(), image['image_url'][1:])  # Remove leading slash
        if os.path.exists(image_path):
            os.remove(image_path)
        
        # Delete record from database
        cursor.execute(
            "DELETE FROM product_images WHERE id = %s AND product_id = %s",
            (image_id, product_id)
        )
        
        # Update image count
        cursor.execute(
            "UPDATE products SET image_count = (SELECT COUNT(*) FROM product_images WHERE product_id = %s) WHERE id = %s",
            (product_id, product_id)
        )
        
        conn.commit()
        return {"message": "Image deleted successfully"}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============ ORDER ENDPOINTS ============
@app.post("/api/orders")
async def create_order(order: CreateOrder, credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    user_id = int(payload.get("sub"))
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # TRANSACTION START
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Verify stock availability for all items before creating order
        # Using SELECT ... FOR UPDATE to lock rows during transaction
        for item in order.items:
            cursor.execute("SELECT stock FROM products WHERE id = %s FOR UPDATE", (item.product_id,))
            product = cursor.fetchone()
            if not product or product['stock'] < item.quantity:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for product {item.product_id}. Available: {product['stock'] if product else 0}, Requested: {item.quantity}")
        
        # Insert order
        cursor.execute(
            "INSERT INTO orders (user_id, total_amount, status, delivery_address, created_at) VALUES (%s, %s, %s, %s, %s)",
            (user_id, order.total_amount, order.status, "Pakistan", datetime.now())
        )
        order_id = cursor.lastrowid
        
        # Insert order items and deduct stock
        for item in order.items:
            cursor.execute("SELECT price FROM products WHERE id = %s", (item.product_id,))
            product = cursor.fetchone()
            
            # Insert order item
            cursor.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (%s, %s, %s, %s)",
                (order_id, item.product_id, item.quantity, product['price'])
            )
            
            # Deduct stock from products table
            cursor.execute(
                "UPDATE products SET stock = stock - %s WHERE id = %s",
                (item.quantity, item.product_id)
            )
            
            # Log inventory change
            cursor.execute("SELECT stock FROM products WHERE id = %s", (item.product_id,))
            new_stock = cursor.fetchone()['stock']
            old_stock = new_stock + item.quantity
            
            cursor.execute(
                "INSERT INTO inventory_logs (product_id, old_stock, new_stock, action) VALUES (%s, %s, %s, %s)",
                (item.product_id, old_stock, new_stock, "order_placed")
            )
        
        # Single commit for entire transaction
        conn.commit()
        
        return {"message": "Order created successfully", "order_id": order_id}
    
    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.get("/api/orders/user/{user_id}")
async def get_user_orders(user_id: int, credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    token_user_id = int(payload.get("sub"))
    
    # IDOR Protection: User can only see their own orders
    if token_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden. You can only view your own orders.")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute(
            "SELECT id, user_id, total_amount, status, delivery_address, created_at FROM orders WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,)
        )
        orders = cursor.fetchall()
        
        for order in orders:
            cursor.execute(
                "SELECT oi.product_id, p.name as product_name, oi.quantity, p.price FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.order_id = %s",
                (order['id'],)
            )
            order['items'] = cursor.fetchall()
        
        return {"orders": orders}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/admin/orders")
async def get_admin_orders(credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute(
            "SELECT o.id, o.user_id, u.name as customer_name, u.phone as customer_phone, o.total_amount, o.status, o.delivery_address, o.created_at FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.created_at DESC"
        )
        orders = cursor.fetchall()
        
        for order in orders:
            cursor.execute(
                "SELECT oi.product_id, p.name as product_name, oi.quantity, p.price FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.order_id = %s",
                (order['id'],)
            )
            order['items'] = cursor.fetchall()
        
        return {"orders": orders}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/admin/orders/{order_id}")
async def update_order_status(order_id: int, update: UpdateOrderStatus, credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor()
    admin_id = int(payload.get("sub"))
    
    try:
        cursor.execute("UPDATE orders SET status = %s, updated_at = %s WHERE id = %s", 
                      (update.status, datetime.now(), order_id))
        
        # Log admin activity
        cursor.execute(
            "INSERT INTO activity_logs (admin_id, action, details) VALUES (%s, %s, %s)",
            (admin_id, f"Order {order_id} status updated", f"New status: {update.status}")
        )
        
        conn.commit()
        return {"message": "Order status updated"}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============ ADMIN STATS ENDPOINTS ============
@app.get("/api/admin/stats/users")
async def get_users_stats(credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT COUNT(*) as count FROM users")
        result = cursor.fetchone()
        return result
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/admin/stats/products")
async def get_products_stats(credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT COUNT(*) as count FROM products")
        result = cursor.fetchone()
        return result
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/admin/stats/orders")
async def get_orders_stats(credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Fixed: Include all delivered/shipped/confirmed orders
        cursor.execute("SELECT COUNT(*) as count, COALESCE(SUM(total_amount), 0) as total_sales FROM orders WHERE status IN ('confirmed', 'shipped', 'delivered')")
        result = cursor.fetchone()
        return result
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/admin/customers")
async def get_customers(credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute(
            "SELECT u.id, u.name, u.email, u.phone, COUNT(o.id) as order_count FROM users u LEFT JOIN orders o ON u.id = o.user_id GROUP BY u.id, u.name, u.email, u.phone"
        )
        customers = cursor.fetchall()
        return {"customers": customers}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/admin/customers/{customer_id}")
async def deactivate_customer(customer_id: int, update: dict, credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor()
    admin_id = int(payload.get("sub"))
    
    try:
        cursor.execute("UPDATE users SET status = %s WHERE id = %s", (update.get("status"), customer_id))
        
        cursor.execute(
            "INSERT INTO activity_logs (admin_id, action, details) VALUES (%s, %s, %s)",
            (admin_id, f"Customer {customer_id} deactivated", f"Status: {update.get('status')}")
        )
        
        conn.commit()
        return {"message": "Customer updated"}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/admin/reports")
async def get_reports(period: str = "daily", credentials: HTTPAuthorizationCredentials = Depends(security)): 
    payload = verify_token(credentials)
    
    # âœ… Simple admin check - only "admin" role allowed
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized - Admin only")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Fixed: MySQL-compatible DATE_FORMAT instead of PostgreSQL DATE_TRUNC
        if period == "monthly":
            query = """
                SELECT DATE_FORMAT(created_at, '%Y-%m-01') as date, 
                       COUNT(*) as orders, 
                       COALESCE(SUM(total_amount), 0) as revenue 
                FROM orders 
                WHERE status IN ('confirmed', 'shipped', 'delivered')
                AND created_at >= DATE_SUB(NOW(), INTERVAL 12 MONTH)
                GROUP BY DATE_FORMAT(created_at, '%Y-%m')
                ORDER BY date DESC
            """
        else:  # daily
            query = """
                SELECT DATE(created_at) as date, 
                       COUNT(*) as orders, 
                       COALESCE(SUM(total_amount), 0) as revenue 
                FROM orders 
                WHERE status IN ('confirmed', 'shipped', 'delivered')
                AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                GROUP BY DATE(created_at)
                ORDER BY date DESC
            """
        
        cursor.execute(query)
        report_data = cursor.fetchall()
        
        total_orders = sum(r['orders'] for r in report_data)
        total_revenue = sum(float(r['revenue']) for r in report_data)
        
        return {
            "report_data": report_data,
            "total_orders": total_orders,
            "total_revenue": total_revenue
        }
    except Error as e:
        print(f"Report error: {str(e)}")
        return {"report_data": [], "total_orders": 0, "total_revenue": 0}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/categories")
async def get_categories():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT id, name FROM categories WHERE status = 'active' ORDER BY name")
        categories = cursor.fetchall()
        return {"categories": categories}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/health")
async def health_check():
    return {"status": "API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)