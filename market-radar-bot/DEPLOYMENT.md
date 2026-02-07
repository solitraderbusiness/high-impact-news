# Market Radar Bot - Ubuntu 24 Server Deployment Guide

**For users with no programming experience**

This guide will walk you through installing and running Market Radar Bot on your Ubuntu 24 server, step by step.

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Step 1: Connect to Your Server](#step-1-connect-to-your-server)
3. [Step 2: Install Required Software](#step-2-install-required-software)
4. [Step 3: Download the Application](#step-3-download-the-application)
5. [Step 4: Create a Virtual Environment](#step-4-create-a-virtual-environment)
6. [Step 5: Configure the Application](#step-5-configure-the-application)
7. [Step 6: Set Up Telegram Bot](#step-6-set-up-telegram-bot)
8. [Step 7: Initialize the Database](#step-7-initialize-the-database)
9. [Step 8: Test the Application](#step-8-test-the-application)
10. [Step 9: Run as a Background Service](#step-9-run-as-a-background-service)
11. [Step 10: Access the Admin Panel](#step-10-access-the-admin-panel)
12. [Updating the Application](#updating-the-application)
13. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before you begin, make sure you have:
- An Ubuntu 24 server (with SSH access)
- A terminal application (Terminal on Mac/Linux, or PuTTY on Windows)
- About 30 minutes of time

---

## Step 1: Connect to Your Server

Open your terminal and connect to your server:

```bash
ssh your-username@your-server-ip
```

Replace `your-username` with your actual username and `your-server-ip` with your server's IP address.

When prompted, enter your password.

---

## Step 2: Install Required Software

Run these commands one at a time. Copy each command, paste it into your terminal, and press Enter.

**Update your system:**
```bash
sudo apt update && sudo apt upgrade -y
```

**Install Python 3.11 and required tools:**
```bash
sudo apt install -y python3.11 python3.11-venv python3-pip git
```

**Verify Python is installed:**
```bash
python3.11 --version
```

You should see something like: `Python 3.11.x`

---

## Step 3: Download the Application

**Create a directory for the application:**
```bash
mkdir -p ~/apps
cd ~/apps
```

**Download the application (choose one method):**

Option A - If you have the files in a git repository:
```bash
git clone https://github.com/YOUR-REPO/market-radar-bot.git
cd market-radar-bot
```

Option B - If you have the files on your computer, upload them:
```bash
# Run this on YOUR COMPUTER (not the server):
scp -r /path/to/market-radar-bot your-username@your-server-ip:~/apps/
```

Then on the server:
```bash
cd ~/apps/market-radar-bot
```

---

## Step 4: Create a Virtual Environment

A virtual environment keeps this application's packages separate from other programs on your server.

**Create the virtual environment:**
```bash
python3.11 -m venv venv
```

**Activate it:**
```bash
source venv/bin/activate
```

You should see `(venv)` appear at the beginning of your command line.

**Install the application:**
```bash
pip install -e .
```

This will take a minute or two as it downloads all required packages.

---

## Step 5: Configure the Application

**Create your configuration file:**
```bash
cp .env.example .env
```

**Edit the configuration:**
```bash
nano .env
```

In the nano editor, you'll see the configuration file. Change these values:

```bash
# Change this to a secure password for the admin panel
ADMIN_PASSWORD=YourSecurePasswordHere123!

# Change this to a random string (mash your keyboard for 32+ characters)
SECRET_KEY=asdfjkl23489sdfjklsdf234890sdfkjl2349

# Your Telegram credentials (see Step 6)
TELEGRAM_BOT_TOKEN=your-bot-token-here
TELEGRAM_CHAT_ID=your-chat-id-here

# Leave these as-is for now:
ALERT_THRESHOLD=70
POLL_INTERVAL_SECONDS=60
```

**To save and exit nano:**
1. Press `Ctrl + X`
2. Press `Y` to confirm
3. Press `Enter`

---

## Step 6: Set Up Telegram Bot

To receive alerts on Telegram, you need to create a bot:

### Create Your Bot:

1. Open Telegram on your phone or computer
2. Search for `@BotFather` and start a chat
3. Send the message: `/newbot`
4. Follow the prompts:
   - Enter a name for your bot (e.g., "My Market Radar")
   - Enter a username for your bot (must end in `bot`, e.g., "mymarketradar_bot")
5. BotFather will give you a **token** that looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
6. Copy this token

### Get Your Chat ID:

1. Start a chat with your new bot (search for it by username)
2. Send any message to your bot (e.g., "hello")
3. Open this URL in your browser (replace YOUR_TOKEN with your actual token):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
4. Look for `"chat":{"id":` followed by a number - that's your chat ID
5. Copy this number

### Add to Configuration:

Edit your `.env` file again:
```bash
nano .env
```

Update these lines with your values:
```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=987654321
```

Save and exit (`Ctrl+X`, `Y`, `Enter`).

---

## Step 7: Initialize the Database

**Make sure your virtual environment is active** (you should see `(venv)` at the start of your command line). If not:
```bash
cd ~/apps/market-radar-bot
source venv/bin/activate
```

**Initialize the database:**
```bash
radar init-db
```

**Add example watch items and sources:**
```bash
radar seed
```

You should see a list of items being created (Trump, Xi, Fed, ECB, etc.)

---

## Step 8: Test the Application

**Test that Telegram is working:**
```bash
radar send-test
```

You should receive a message on Telegram!

**Run a quick test of the full system:**
```bash
radar run --once
```

This will:
1. Fetch news from configured sources
2. Match against watch items
3. Send alerts if any high-severity matches are found

---

## Step 9: Run as a Background Service

To keep the application running even when you disconnect, we'll set it up as a system service.

### Create the Web Server Service:

```bash
sudo nano /etc/systemd/system/market-radar-web.service
```

Paste this content (change `your-username` to your actual username):

```ini
[Unit]
Description=Market Radar Web Server
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/home/your-username/apps/market-radar-bot
Environment="PATH=/home/your-username/apps/market-radar-bot/venv/bin"
ExecStart=/home/your-username/apps/market-radar-bot/venv/bin/radar serve
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Save and exit (`Ctrl+X`, `Y`, `Enter`).

### Create the Monitor Service:

```bash
sudo nano /etc/systemd/system/market-radar-monitor.service
```

Paste this content (change `your-username` to your actual username):

```ini
[Unit]
Description=Market Radar Monitor
After=network.target market-radar-web.service

[Service]
Type=simple
User=your-username
WorkingDirectory=/home/your-username/apps/market-radar-bot
Environment="PATH=/home/your-username/apps/market-radar-bot/venv/bin"
ExecStart=/home/your-username/apps/market-radar-bot/venv/bin/radar run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Save and exit.

### Enable and Start the Services:

```bash
# Reload systemd to recognize new services
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable market-radar-web
sudo systemctl enable market-radar-monitor

# Start the services
sudo systemctl start market-radar-web
sudo systemctl start market-radar-monitor
```

### Alternative: Single Combined Service

If you prefer a simpler setup with just one service, you can use `radar run --web` which runs both the monitor AND the web server together:

```bash
sudo nano /etc/systemd/system/market-radar.service
```

```ini
[Unit]
Description=Market Radar (Web + Monitor)
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/home/your-username/apps/market-radar-bot
Environment="PATH=/home/your-username/apps/market-radar-bot/venv/bin"
ExecStart=/home/your-username/apps/market-radar-bot/venv/bin/radar run --web
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable market-radar
sudo systemctl start market-radar
```

### Check if They're Running:

```bash
sudo systemctl status market-radar-web
sudo systemctl status market-radar-monitor
```

You should see "active (running)" in green for both.

---

## Step 10: Access the Admin Panel

The admin panel is now running on your server.

### If You Have a Domain:

1. Set up a reverse proxy with Nginx (see below)
2. Access at `https://your-domain.com/admin`

### For Direct Access (Testing):

If your server has a firewall, you need to open port 8000:
```bash
sudo ufw allow 8000
```

Then access: `http://your-server-ip:8000/admin`

**Login credentials:**
- Username: `admin` (or what you set in .env)
- Password: Whatever you set for `ADMIN_PASSWORD` in .env

### Setting Up Nginx (Recommended for Production):

```bash
sudo apt install nginx -y
sudo nano /etc/nginx/sites-available/market-radar
```

Paste this (replace `your-domain.com` with your actual domain):

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/market-radar /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

Now you can access: `http://your-domain.com/admin`

---

## Updating the Application

When there's a new version:

```bash
# Stop the services
sudo systemctl stop market-radar-monitor
sudo systemctl stop market-radar-web

# Go to the application directory
cd ~/apps/market-radar-bot

# Activate virtual environment
source venv/bin/activate

# Update the code (if using git)
git pull

# Update dependencies
pip install -e .

# Restart services
sudo systemctl start market-radar-web
sudo systemctl start market-radar-monitor
```

---

## Troubleshooting

### Check Logs:

```bash
# Web server logs
sudo journalctl -u market-radar-web -f

# Monitor logs
sudo journalctl -u market-radar-monitor -f
```

Press `Ctrl+C` to stop viewing logs.

### Common Issues:

**"Command not found: radar"**
```bash
cd ~/apps/market-radar-bot
source venv/bin/activate
```

**"Permission denied"**
Make sure you're using the correct username in the service files.

**"Address already in use"**
Another program is using port 8000. Either stop it or change PORT in .env:
```bash
# Find what's using port 8000
sudo lsof -i :8000

# Or use a different port
nano .env
# Change PORT=8000 to PORT=8001
```

**"No module named 'radar'" or "ModuleNotFoundError: No module named 'radar'"**

This is the most common deployment error. It occurs when the `radar` package is not installed in your virtual environment. Follow these steps to fix it:

1. First, verify that `pyproject.toml` exists in your project directory:
```bash
ls -la ~/apps/market-radar-bot/pyproject.toml
```

2. If the file is **missing**, you need to create it. See the "Missing pyproject.toml" section below.

3. If the file **exists**, reinstall the package:
```bash
cd ~/apps/market-radar-bot
source venv/bin/activate
pip install -e .
```

4. Verify the `radar` command is available:
```bash
which radar
# Should output: /home/your-username/apps/market-radar-bot/venv/bin/radar
```

5. Restart the service:
```bash
sudo systemctl restart market-radar-web
sudo systemctl restart market-radar-monitor
```

**"status=203/EXEC" in systemd logs**

This error means the executable cannot be found. Usually caused by:
- Missing `radar` command (see above)
- Incorrect paths in the systemd service file
- Virtual environment not properly set up

Check that the paths in your service file match your actual installation:
```bash
cat /etc/systemd/system/market-radar-web.service
# Verify WorkingDirectory and ExecStart paths are correct
```

---

### Missing pyproject.toml

If `pyproject.toml` is missing from your project directory (this can happen after certain git operations), create it with this content:

```bash
cat > ~/apps/market-radar-bot/pyproject.toml << 'EOF'
[project]
name = "market-radar-bot"
version = "0.1.0"
description = "Proactive Market Impact Radar - monitors influential people, institutions, and events for market-moving news"
readme = "README.md"
requires-python = ">=3.11"
license = {text = "MIT"}
authors = [
    {name = "Market Radar Team"}
]

dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "sqlalchemy>=2.0.25",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "python-dotenv>=1.0.0",
    "feedparser>=6.0.10",
    "httpx>=0.26.0",
    "beautifulsoup4>=4.12.0",
    "lxml>=5.1.0",
    "apscheduler>=3.10.4",
    "jinja2>=3.1.3",
    "python-multipart>=0.0.6",
    "itsdangerous>=2.1.2",
    "passlib[bcrypt]>=1.7.4",
    "pytz>=2024.1",
    "structlog>=24.1.0",
    "click>=8.1.7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.1.0",
    "httpx>=0.26.0",
]

[project.scripts]
radar = "radar.cli:cli"

[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["radar*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
EOF
```

Then install the package:
```bash
cd ~/apps/market-radar-bot
source venv/bin/activate
pip install -e .
sudo systemctl restart market-radar-web
sudo systemctl restart market-radar-monitor
```

---

### Verifying Your Installation

Run this checklist to verify everything is set up correctly:

```bash
# 1. Go to your project directory
cd ~/apps/market-radar-bot

# 2. Check required files exist
ls pyproject.toml .env radar/

# 3. Activate virtual environment
source venv/bin/activate

# 4. Verify radar command is installed
which radar
# Expected: /home/your-username/apps/market-radar-bot/venv/bin/radar

# 5. Test the radar command
radar --help
# Should show available commands

# 6. Test Telegram (if configured)
radar send-test

# 7. Check services are running
sudo systemctl status market-radar-web
sudo systemctl status market-radar-monitor
```

---

### Directory Structure Requirements

Your project directory MUST have this structure for the application to work:

```
~/apps/market-radar-bot/           # Main project directory
├── pyproject.toml                 # REQUIRED - Package configuration
├── .env                           # REQUIRED - Environment configuration
├── radar/                         # REQUIRED - Python source code
│   ├── __init__.py
│   ├── cli.py                     # CLI entry point
│   ├── main.py
│   └── ... (other modules)
├── venv/                          # Virtual environment
├── market_radar.db                # SQLite database (created by init-db)
└── tests/                         # Optional test files
```

**IMPORTANT:** The `radar` command is installed from `pyproject.toml`. The `[project.scripts]` section defines:
```toml
[project.scripts]
radar = "radar.cli:cli"
```

This tells Python to create a `radar` command that calls the `cli()` function in `radar/cli.py`.

---

### Other Common Issues:

**"No module named..."**
Reinstall dependencies:
```bash
source venv/bin/activate
pip install -e .
```

**Telegram not sending:**
1. Check your bot token and chat ID
2. Make sure you've sent at least one message to your bot
3. Test with: `radar send-test`

### Restart Everything:

```bash
sudo systemctl restart market-radar-web
sudo systemctl restart market-radar-monitor
```

### View Recent Events:

Access the admin panel at `/admin/events` to see what's been collected.

---

## Quick Reference Commands

```bash
# Activate virtual environment
cd ~/apps/market-radar-bot
source venv/bin/activate

# Check service status
sudo systemctl status market-radar-web
sudo systemctl status market-radar-monitor

# View live logs
sudo journalctl -u market-radar-monitor -f

# Restart services
sudo systemctl restart market-radar-web
sudo systemctl restart market-radar-monitor

# Stop services
sudo systemctl stop market-radar-web
sudo systemctl stop market-radar-monitor

# Run manually (for testing)
radar run --once

# Send test telegram
radar send-test
```

---

## Need Help?

If you encounter issues:
1. Check the logs (see Troubleshooting section)
2. Make sure all configuration is correct in `.env`
3. Verify Telegram bot setup
4. Check that services are running

For programmer assistance, share:
- The error message from logs
- Your `.env` file (remove sensitive values first)
- Output of `sudo systemctl status market-radar-*`
