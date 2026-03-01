#!/usr/bin/env python3
"""
Build script to create CUGA with embedded assets (no external files needed).
This creates a version that doesn't require the frontend-workspaces folder.
"""

import subprocess
import sys
from pathlib import Path


def build_and_embed():
    """Build assets and embed them."""
    print("🏗️  Building and embedding assets...")

    base_dir = Path(__file__).resolve().parents[2]

    # Step 1: Build frontend
    print("📦 Building frontend...")
    subprocess.run(
        ["pnpm", "--filter", "@carbon/ai-chat-examples-web-components-basic", "run", "build"],
        cwd=base_dir / "src" / "frontend_workspaces",
        check=True,
    )

    # Step 2: Build extension
    print("🔧 Building extension...")
    subprocess.run(
        ["pnpm", "--filter", "extension", "run", "release"], cwd=base_dir / "src" / "frontend_workspaces", check=True
    )

    # Step 3: Embed assets
    print("📦 Embedding assets...")
    subprocess.run(["uv", "run", "scripts/embed_assets.py"], cwd=base_dir, check=True)

    print("✅ Build completed successfully!")
    print("")
    print("🎉 Your CUGA server now has embedded assets!")
    print("📁 Assets embedded in: cuga/backend/server/embedded_assets.py")
    print("💡 You can now run the server without the frontend_workspaces folder")
    print("🚀 Start server: uv run cuga/backend/server/main.py")


def main():
    """Main build process."""
    try:
        build_and_embed()
    except subprocess.CalledProcessError as e:
        print(f"❌ Build failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
