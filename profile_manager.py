import os
import json
import sys
import shutil

def get_base_data_path():
    """Get the path to the application data directory."""
    # Priority 1: Environment Variable (set by run_gui.py launcher)
    env_path = os.environ.get('NEXUS_DATA_PATH')
    if env_path and os.path.exists(env_path):
        return env_path

    # Use current directory to save database
    path = os.path.abspath(os.path.dirname(__file__))
    
    os.makedirs(path, exist_ok=True)
    return path

def get_profiles_config_path():
    return os.path.join(get_base_data_path(), 'profiles.json')

def load_profiles():
    config_path = get_profiles_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            # Migrate old nexus.db files to project.db for all profiles
            migrate_database_files(config)
            return config
        except:
            pass
    
    default_config = {"active_profile": "default", "profiles": {"default": {"name": "Shopno Bilash", "path": get_base_data_path()}}}
    migrate_database_files(default_config)
    return default_config

def migrate_database_files(config):
    """Rename nexus.db to project.db in all profile directories if found."""
    for profile_id, profile in config.get('profiles', {}).items():
        path = profile.get('path')
        if not path or not os.path.exists(path):
            continue
            
        old_db = os.path.join(path, 'nexus.db')
        new_db = os.path.join(path, 'project.db')
        
        if os.path.exists(old_db) and not os.path.exists(new_db):
            try:
                print(f"Migrating {profile_id}: '{old_db}' -> '{new_db}'")
                shutil.move(old_db, new_db)
            except Exception as e:
                print(f"Failed to migrate database for {profile_id}: {e}")

def save_profiles(config):
    config_path = get_profiles_config_path()
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)

def get_active_profile():
    config = load_profiles()
    active_id = config.get('active_profile', 'default')
    profile = config['profiles'].get(active_id)
    if not profile:
        # Fallback to first profile if active one is missing
        active_id = list(config['profiles'].keys())[0]
        profile = config['profiles'][active_id]
    return active_id, profile

def add_profile(profile_id, name, path):
    config = load_profiles()
    config['profiles'][profile_id] = {"name": name, "path": path}
    save_profiles(config)

def switch_profile(profile_id):
    config = load_profiles()
    if profile_id in config['profiles']:
        config['active_profile'] = profile_id
        save_profiles(config)
        return True
    return False

def delete_profile(profile_id):
    config = load_profiles()
    if profile_id in config['profiles'] and profile_id != 'default':
        del config['profiles'][profile_id]
        if config['active_profile'] == profile_id:
            config['active_profile'] = 'default'
        save_profiles(config)
        return True
    return False
