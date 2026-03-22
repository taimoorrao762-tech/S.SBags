# S.S BAGS - School Bags Project Deployment Guide

This project is prepared for deployment to **GitHub**, **Aiven (MySQL Database)**, and **Vercel**.

## 1. Setup GitHub Repository

1.  Initialize Git and push your code:
    ```bash
    git init
    git add .
    git commit -m "Prepare for deployment"
    # Create a new repository on GitHub and then:
    git remote add origin YOUR_GITHUB_REPO_URL
    git branch -M main
    git push -u origin main
    ```

## 2. Setup Aiven MySQL Database

1.  Create a free account on [Aiven.io](https://aiven.io/).
2.  Create a **MySQL service**.
3.  In the Aiven console, copy the following details:
    - **Host**
    - **Port**
    - **User**
    - **Password**
    - **Database Name** (e.g., `defaultdb` or create one named `ss_bags`)
    - **SSL CA Certificate** (Download the `ca.pem` file or copy its content).

4.  Run the `combined_database.sql` script on your Aiven database to create the necessary tables.

## 3. Deploy to Vercel

1.  Create an account on [Vercel](https://vercel.com/).
2.  Click **"Add New Project"** and import your GitHub repository.
3.  In the **Environment Variables** section, add the following:

| Name | Value |
| :--- | :--- |
| `DB_HOST` | (Your Aiven Host) |
| `DB_USER` | (Your Aiven User) |
| `DB_PASSWORD` | (Your Aiven Password) |
| `DB_NAME` | (Your Aiven Database Name) |
| `DB_PORT` | `(Usually 3306 or as specified by Aiven)` |
| `DB_SSL_CA` | (Paste the content of `ca.pem` if required) |
| `SECRET_KEY` | (A long random string for JWT) |
| `FRONTEND_URL` | (Your Vercel deployment URL after the first deploy) |

4.  Click **Deploy**.

## ⚠️ Important Note on File Uploads
Vercel has a read-only filesystem. Images uploaded through the admin panel will **not persist** after the server restarts and will not be shared across different visitors. For a production-ready application, please consider using an external service like **Cloudinary** or **AWS S3** for image storage.

---
Created with ❤️ by Antigravity
