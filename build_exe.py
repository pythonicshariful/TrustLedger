import PyInstaller.__main__
import os
import shutil
import certifi
import sys

# Clean up previous build
if os.path.exists('dist'):
    shutil.rmtree('dist')
if os.path.exists('build'):
    shutil.rmtree('build')

# Define PyInstaller arguments
# Get certifi pem path
cert_path =  os.path.join(os.path.dirname(certifi.__file__), 'cacert.pem')

# Base arguments
args = [
    'run_gui.py',  # Entry point
    '--name=TrustLedger',
    '--onefile',  # Single executable
    '--noconsole', # Hide console
    # Add data files: source;dest (Windows) or source:dest (Unix)
    f'--add-data=templates{os.pathsep}templates',
    f'--add-data=static{os.pathsep}static',
    f'--add-data=admin_config.json{os.pathsep}.',
    f'--add-data=trustledger.ico{os.pathsep}.', 
    f'--add-data={cert_path}{os.pathsep}certifi', # Explicitly add certifi bundle
    # Hidden imports
    '--hidden-import=engineio.async_drivers.threading',
    '--hidden-import=certifi', 
    '--clean',
]

# Add icon if available and appropriate for the OS
if sys.platform.startswith('win'):
    if os.path.exists('trustledger.ico'):
        args.append('--icon=trustledger.ico')
elif sys.platform == 'darwin':
    # Mac requires .icns
    if os.path.exists('trustledger.icns'):
        args.append('--icon=trustledger.icns')

print("Building with arguments:", args)
PyInstaller.__main__.run(args)

print("Build complete. detailed logs in build/ and output in dist/")
