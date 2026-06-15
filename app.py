from flask import Flask
from database import db
from routes import main
import os

import sys

import sys

from profile_manager import get_active_profile, load_profiles

def create_app():
    app = Flask(__name__)

    # Load Active Profile
    profile_id, profile = get_active_profile()
    data_dir = profile.get('path')
    company_name = profile.get('name', 'Shopno Bilash')

    if not data_dir:
        # Fallback logic (Dev mode or standalone run without launcher)
        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
            data_dir = base_path
        else:
            base_path = app.root_path
            data_dir = app.instance_path

    # DB Path
    # Always put DB in the determined data_dir
    data_dir = os.path.abspath(data_dir)
    db_path = os.path.join(data_dir, 'project.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['DATA_FOLDER'] = data_dir # Expose for other modules
    app.config['DATABASE_PATH'] = db_path # Expose DB path for backups
    app.config['COMPANY_NAME'] = company_name
    app.config['PROFILE_ID'] = profile_id
    
    print(f"--- Database Diagnostics ---")
    print(f"Data Directory: {data_dir}")
    print(f"Database Path: {db_path}")
    
    # Ensure data_dir exists and is writable
    try:
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
            print(f"Created data directory: {data_dir}")
        
        # On Mac/Unix, ensure the directory is writable
        if os.name != 'nt':
            try:
                current_mode = os.stat(data_dir).st_mode
                if not (current_mode & 0o200): # Check if writable by owner
                    print(f"Warning: Data directory is NOT writable. Attempting to fix...")
                    os.chmod(data_dir, 0o777)
                    print(f"Successfully updated directory permissions to 0777")
            except Exception as pe:
                print(f"Failed to check/update directory permissions: {pe}")

        # Check if database file exists and is writable
        if os.path.exists(db_path):
            if os.access(db_path, os.W_OK):
                print(f"Database file is writable.")
            else:
                print(f"Database file is NOT writable. Attempting to fix...")
                if os.name != 'nt':
                     try:
                         os.chmod(db_path, 0o666)
                         print(f"Successfully updated database permissions to 0666")
                     except Exception as pe:
                         print(f"Failed to update database permissions: {pe}")
                else:
                    print("On Windows, please check file attributes manually.")
        else:
            print("Database file does not exist yet (will be created).")
    except Exception as e:
        print(f"Error during directory/file check: {e}")
    print(f"---------------------------")
        
    app.config['SECRET_KEY'] = 'dev-key-shopno-bilash'
    
    # Load Admin Config
    # Priority: Data Dir > Bundled
    
    # 1. Try Data Dir
    external_config_path = os.path.join(data_dir, 'admin_config.json')
    # 2. Try bundled (inside _MEI... or source root)
    bundled_config_path = os.path.join(app.root_path, 'admin_config.json')
    
    config_loaded = False
    
    if os.path.exists(external_config_path):
        try:
            import json
            with open(external_config_path, 'r') as f:
                config = json.load(f)
                app.config['ADMIN_PASSWORD'] = config.get('ADMIN_PASSWORD', '1234')
            config_loaded = True
        except:
             pass # Fallback
    
    # Auto-generate if missing in data_dir
    if not config_loaded:
        # If we have a bundled one, try to read it first to get default
        default_password = '1234'
        if os.path.exists(bundled_config_path):
             try:
                import json
                with open(bundled_config_path, 'r') as f:
                    config = json.load(f)
                    default_password = config.get('ADMIN_PASSWORD', '1234')
             except:
                 pass
        
        # Write to data_dir
        try:
            import json
            with open(external_config_path, 'w') as f:
                json.dump({"ADMIN_PASSWORD": default_password}, f)
            app.config['ADMIN_PASSWORD'] = default_password
        except Exception as e:
            print(f"Failed to auto-create config: {e}")
            app.config['ADMIN_PASSWORD'] = default_password # Fallback in memory

    if 'ADMIN_PASSWORD' not in app.config:
        app.config['ADMIN_PASSWORD'] = '1234' # Default Master Password

    # Upload Folder
    # Put uploads in data_dir/uploads
    app.config['UPLOAD_FOLDER'] = os.path.join(data_dir, 'uploads')
    
    # Ensure upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    db.init_app(app)
    
    app.register_blueprint(main)
    
    with app.app_context():
        try:
            db.create_all()
            
            # Auto-migrate missing columns for older SQLite databases
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            
            # Migrate Customer table
            if 'customer' in inspector.get_table_names():
                columns = [c['name'] for c in inspector.get_columns('customer')]
                if 'num_shares' not in columns:
                    try:
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN num_shares FLOAT DEFAULT 1.0'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN father_name VARCHAR(100)'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN mother_name VARCHAR(100)'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN dob VARCHAR(20)'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN religion VARCHAR(50)'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN profession VARCHAR(100)'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN nid_no VARCHAR(50)'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN present_address VARCHAR(255)'))
                        db.session.execute(db.text('ALTER TABLE customer ADD COLUMN permanent_address VARCHAR(255)'))
                        db.session.commit()
                        print("Auto-migrated customer table missing columns.")
                    except Exception as e:
                        print(f"Migration error for customer: {e}")
                        
            # Migrate Transaction table
            if 'transaction' in inspector.get_table_names():
                tx_columns = [c['name'] for c in inspector.get_columns('transaction')]
                if 'installment_type' not in tx_columns:
                    try:
                        db.session.execute(db.text('ALTER TABLE "transaction" ADD COLUMN installment_type VARCHAR(100)'))
                        db.session.execute(db.text('ALTER TABLE "transaction" ADD COLUMN bank_name VARCHAR(100)'))
                        db.session.execute(db.text('ALTER TABLE "transaction" ADD COLUMN transaction_id VARCHAR(100)'))
                        db.session.commit()
                        print("Auto-migrated transaction table missing columns.")
                    except Exception as e:
                        print(f"Migration error for transaction: {e}")
                        
        except Exception as db_err:
            print(f"CRITICAL: Failed to initialize/create all tables: {db_err}")
        
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='127.0.0.1', port=8080, debug=True)
