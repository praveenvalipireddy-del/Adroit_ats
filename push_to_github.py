"""
ADROIT ATS - 1-Click Push to GitHub for Render Deployment
Usage:
    python push_to_github.py https://github.com/YOUR_USERNAME/adroit-ats.git
Or:
    python push_to_github.py
"""

import sys
import os
import dulwich.porcelain as porcelain
from dulwich.repo import Repo

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

def push_repo(remote_url=None):
    repo = Repo(REPO_DIR)
    
    if not remote_url:
        if len(sys.argv) > 1:
            remote_url = sys.argv[1].strip()
        else:
            print("=" * 65)
            print(">> ADROIT ATS - PUSH TO GITHUB FOR RENDER DEPLOYMENT")
            print("=" * 65)
            print("\nPlease enter your GitHub repository URL (e.g. https://github.com/username/adroit-ats.git):")
            remote_url = input("GitHub Repo URL: ").strip()

    if not remote_url:
        print("[-] Error: No GitHub URL provided.")
        return

    # Normalize URL
    if not remote_url.endswith(".git") and "github.com" in remote_url:
        remote_url = remote_url.rstrip("/") + ".git"

    print(f"\n[*] Staging all latest files...")
    porcelain.add(REPO_DIR)
    try:
        porcelain.commit(
            REPO_DIR,
            message=b"Update: Adroit ATS production build ready for Render",
            author=b"Praveen Valipireddy <praveen@adroit-ai.com>",
            committer=b"Praveen Valipireddy <praveen@adroit-ai.com>"
        )
        print("[+] Latest changes committed.")
    except Exception:
        print("[*] Working tree clean, nothing new to commit.")

    print(f"[*] Pushing code to: {remote_url} (branch: main) ...")
    try:
        porcelain.push(repo, remote_url, refspecs=b"refs/heads/main")
        print("\n=======================================================")
        print(">> SUCCESS: Code pushed to GitHub successfully!")
        print(">> Next step: Open https://dashboard.render.com to deploy!")
        print("=======================================================\n")
    except Exception as e:
        print(f"\n[!] Push notice: {e}")
        print("If authentication is required, use a GitHub Personal Access Token in the URL:")
        print("Example: https://<YOUR_TOKEN>@github.com/<USERNAME>/adroit-ats.git")

if __name__ == "__main__":
    push_repo()
