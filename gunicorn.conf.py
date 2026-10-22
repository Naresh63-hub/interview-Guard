import os

# Automatically bind to Render's dynamic port (fixes "No open ports detected")
port = os.getenv("PORT", "10000")
bind = f"0.0.0.0:{port}"

# Optimize concurrency for 512MB RAM free tier
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
