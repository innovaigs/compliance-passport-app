"""
deploy_to_daytona.py — Production Deployment Script for Compliance Passport to Daytona.

1. Builds frontend dist bundle.
2. Tars project assets.
3. Provisions a persistent Daytona container sandbox.
4. Uploads, extracts, installs Python dependencies, and boots uvicorn server.
5. Obtains signed public preview URL.
6. Verifies public /api/health reachability.
"""

import os
import sys
import time
import tarfile
import urllib.request
import certifi
from pathlib import Path

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from daytona import Daytona


def deploy():
    print("=== C1: DEPLOY TO DAYTONA CONTAINER SANDBOX ===")

    project_root = Path(__file__).resolve().parent
    frontend_dir = project_root / "frontend"
    tar_path = project_root / "app_deploy.tar.gz"

    # Step 1: Ensure frontend is built
    dist_dir = frontend_dir / "dist"
    if not dist_dir.exists() or not (dist_dir / "index.html").exists():
        print("Building frontend assets...")
        os.system(f"npm --prefix '{frontend_dir}' run build")

    # Step 2: Create tarball
    print("Packing project tarball...")
    # .env and env.sh must never be packed — credentials reach the sandbox through the
    # boot command's environment below, not by shipping the file.
    exclude_dirs = {
        ".venv", ".git", "node_modules", ".pytest_cache", "__pycache__", "data",
        "app_deploy.tar.gz", ".env", ".env.local", "env.sh",
    }
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in project_root.iterdir():
            if item.name not in exclude_dirs:
                tar.add(item, arcname=item.name)

    print(f"Tarball created: {tar_path.name} ({tar_path.stat().st_size} bytes)")

    # Step 3: Create Daytona persistent sandbox
    api_key = os.environ["DAYTONA_API_KEY"]  # raises if unset; no fallback literal

    print("Provisioning persistent Daytona deployment sandbox...")
    daytona_client = Daytona()
    sandbox = daytona_client.create()
    sandbox_id = getattr(sandbox, "id", "?")
    print(f"DAYTONA SANDBOX ID: {sandbox_id}")

    # Step 4: Upload tarball
    print("Uploading project archive to Daytona container...")
    sandbox.fs.upload_file(str(tar_path), "/tmp/app_deploy.tar.gz")

    # Step 5: Extract and install dependencies
    print("Extracting application archive inside sandbox...")
    sandbox.process.exec("mkdir -p /home/daytona/app && tar -xzf /tmp/app_deploy.tar.gz -C /home/daytona/app")

    print("Installing Python dependencies inside sandbox...")
    sandbox.process.exec("pip install --quiet fastapi uvicorn openpyxl pandas python-docx pypdf sqlalchemy pydantic pydantic-settings httpx")
    sandbox.process.exec("mkdir -p /home/daytona/app/data /home/daytona/app/uploads /home/daytona/app/exports")

    # Step 6: Boot Uvicorn server in background
    print("Boots Uvicorn server on port 8000 inside Daytona sandbox...")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    daytona_key = os.getenv("DAYTONA_API_KEY", "")

    boot_cmd = (
        f"nohup bash -c 'export ANTHROPIC_API_KEY=\"{anthropic_key}\" && "
        f"export DAYTONA_API_KEY=\"{daytona_key}\" && "
        f"cd /home/daytona/app && python -m uvicorn main:app --host 0.0.0.0 --port 8000' > /tmp/uvicorn.log 2>&1 &"
    )
    sandbox.process.exec(boot_cmd)
    time.sleep(3)

    # Step 7: Obtain signed preview URL
    print("Generating signed preview URL from Daytona API...")
    preview_res = sandbox.create_signed_preview_url(8000)
    preview_url = getattr(preview_res, "url", str(preview_res))

    print("\n=======================================================")
    print(f"DAYTONA SANDBOX ID: {sandbox_id}")
    print(f"PUBLIC PREVIEW URL: {preview_url}")
    print("=======================================================\n")

    # Step 8: Verify health from outside sandbox via curl / HTTP request
    health_url = f"{preview_url.rstrip('/')}/api/health"
    print(f"Testing public reachability of {health_url} from outside sandbox...")

    for attempt in range(5):
        try:
            req = urllib.request.Request(health_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                print("Public /api/health Response Code:", resp.status)
                print("Public /api/health Response Body:", data)
                if resp.status == 200:
                    print("\nDEPLOYMENT SUCCESSFUL! Sandbox is publicly reachable and running.\n")
                    break
        except Exception as e:
            print(f"Attempt {attempt+1} health check: {e}")
            time.sleep(2)

    return sandbox_id, preview_url


if __name__ == "__main__":
    deploy()
