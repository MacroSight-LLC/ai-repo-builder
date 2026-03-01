#!/usr/bin/env python3
"""
Build script to create a standalone CUGA executable with embedded assets.
"""

import subprocess
import sys


def build_assets():
    """Build frontend and extension assets."""
    print("🏗️  Building frontend assets...")

    # Build frontend
    subprocess.run(
        ["pnpm", "--filter", "@carbon/ai-chat-examples-web-components-basic", "run", "build"],
        cwd="src/frontend_workspaces",
        check=True,
    )

    # Build extension
    subprocess.run(["pnpm", "--filter", "extension", "run", "release"], cwd="src/frontend_workspaces", check=True)

    print("✅ Assets built successfully")


def embed_assets():
    """Embed assets as base64 data."""
    print("📦 Embedding assets...")
    subprocess.run(["uv", "run", "scripts/embed_assets.py"], check=True)
    print("✅ Assets embedded successfully")


def create_standalone_executable():
    """Create standalone executable using PyInstaller."""
    print("🚀 Creating standalone executable...")

    # Install PyInstaller if not available
    try:
        # import PyInstaller  # Only needed to check if available
        pass
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    # Create PyInstaller spec
    spec_content = '''
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['src/cuga/backend/server/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/cuga/backend/server/embedded_assets.py', 'cuga/backend/server/'),
        ('src/cuga/config.py', 'cuga/'),
        ('src/cuga/settings.toml', 'cuga/'),
    ],
    hiddenimports=[
        'cuga.backend.server.embedded_assets',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='cuga-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
'''

    with open('cuga-server.spec', 'w') as f:
        f.write(spec_content)

    # Build executable
    subprocess.run(["pyinstaller", "--clean", "cuga-server.spec"], check=True)

    print("✅ Standalone executable created in dist/cuga-server")


def main():
    """Main build process."""
    print("🚀 Building standalone CUGA server...")

    try:
        # Step 1: Build assets
        build_assets()

        # Step 2: Embed assets
        embed_assets()

        # Step 3: Create executable
        create_standalone_executable()

        print("🎉 Build completed successfully!")
        print("📁 Executable location: dist/cuga-server")
        print("💡 Run with: ./dist/cuga-server")

    except subprocess.CalledProcessError as e:
        print(f"❌ Build failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
