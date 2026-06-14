# VPS Production Setup Guide

This guide will help you set up your VPS server, configure Nginx with SSL (Certbot), and set up GitHub Actions for automatic deployment.

## 1. Server Initial Setup
Connect to your VPS:
```bash
ssh root@your_vps_ip
```

### Install Docker and Docker Compose
Run the following commands to install Docker:
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

### 1.1 Firewall Configuration (CRITICAL for GitHub Actions)
If you get a "timeout" error in GitHub Actions, your firewall is likely blocking the connection.
Run these commands to allow SSH:
```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### Clone the Project
```bash
mkdir -p ~/projects
cd ~/projects
git clone your_repository_url Easevent-_backend
cd Easevent-_backend
# Create your .env file manually or let GitHub Actions do it
```

## 2. Nginx and SSL (Certbot)
Install Nginx and Certbot:
```bash
sudo apt update
sudo apt install nginx certbot python3-certbot-nginx -y
```

### Configure Nginx
Create the configuration file:
```bash
sudo nano /etc/nginx/sites-available/easevent
```
Copy the content from the `nginx.conf` file in this repository into that file.

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/easevent /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Get SSL Certificate
```bash
sudo certbot --nginx -d easevent.nitypulse.com
```
Follow the prompts. Certbot will automatically update your Nginx configuration to use HTTPS.

## 3. GitHub Actions Secrets
To enable automatic deployment, go to your GitHub repository:
**Settings > Secrets and variables > Actions > New repository secret**.

Add the following:

| Secret Name | How to get it |
| :--- | :--- |
| **`SSH_HOST`** | Your VPS IP address (e.g., `123.45.67.89`) |
| **`SSH_USER`** | Your SSH user (e.g., `root` or your username) |
| **`SSH_KEY`** | Your **Private** SSH Key (see below) |
| **`SSH_PORT`** | Your SSH port (usually `22`) |

### How to get the SSH Key
1. On your local machine (or the VPS), check if you have a key: `ls -al ~/.ssh`.
2. If not, generate one: `ssh-keygen -t rsa -b 4096`.
3. Display the **Private** key: `cat ~/.ssh/id_rsa`.
4. Copy the **entire** output (including `-----BEGIN RSA PRIVATE KEY-----`) and paste it into the `SSH_KEY` secret.
5. Add the **Public** key to the authorized keys on your VPS: `cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys`.

## 4. Deploy Command
The GitHub Action will run this automatically on every push to `main`:
```bash
cd ~/projects/Easevent-_backend && git pull origin main && docker-compose up -d --build
```
