import sys
import requests
import json
import logging
import time
import base64
import obsws_python as obs
from datetime import datetime, timedelta
from pathlib import Path

# --- OPTIONAL IMPORTS ---
try:
    from mutagen.mp3 import MP3
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# --- PORTABLE PATH SETUP ---
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = Path(sys.executable).parent
else:
    SCRIPT_DIR = Path(__file__).parent.absolute()
    
CONFIG_FILE = SCRIPT_DIR / "config.json"
OUTPUT_DIR = SCRIPT_DIR / "sfx_temp"
LIBRARY_DIR = SCRIPT_DIR / "sfx_library"
LOG_DIR = SCRIPT_DIR / "logs"
STATUS_FILE = SCRIPT_DIR / "sfx_status.txt"

# Ensure directories exist
OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
LIBRARY_DIR.mkdir(exist_ok=True)

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "sfx_log.txt", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SFXHandler:
    def __init__(self):
        self.load_config()
        self.obs_client = None

    def load_config(self):
        if not CONFIG_FILE.exists():
            logger.error(f"Config file missing: {CONFIG_FILE}")
            sys.exit(1)
        
        with open(CONFIG_FILE, 'r') as f:
            self.conf = json.load(f)

    def connect_obs(self):
        try:
            self.obs_client = obs.ReqClient(
                host=self.conf.get("obs_host", "localhost"),
                port=self.conf.get("obs_port", 4455),
                password=self.conf.get("obs_password"),
                timeout=3
            )
        except Exception as e:
            logger.error(f"OBS Connection failed: {e}")
            self.obs_client = None

    def cleanup_old_files(self):
        days = self.conf.get("cleanup_days", 3)
        cutoff = datetime.now() - timedelta(days=days)
        cleaned_count = 0
        
        for file in OUTPUT_DIR.glob("*.mp3"):
            try:
                if datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
                    file.unlink()
                    meta = file.with_suffix('.json')
                    if meta.exists(): meta.unlink()
                    cleaned_count += 1
            except Exception:
                pass
        
        if cleaned_count > 0:
            logger.info(f"Cleaned {cleaned_count} old files.")

    def get_duration(self, filepath):
        if MUTAGEN_AVAILABLE:
            try: return MP3(filepath).info.length
            except: pass
        
        try:
            meta_path = filepath.with_suffix('.json')
            if meta_path.exists():
                with open(meta_path, 'r') as f:
                    return json.load(f).get("duration", 5)
        except: pass

        return 5.0

    def get_library_match(self, prompt):
        mapping = self.conf.get("library_map", {})
        cleaned_prompt = prompt.lower().strip()
        
        # Strict match only
        if cleaned_prompt in mapping:
            f_path = LIBRARY_DIR / mapping[cleaned_prompt]
            if f_path.exists(): return f_path
            
        return None

    def generate_audio(self, prompt, user):
        if self.conf.get("test_mode", False):
            return None, "TEST_MODE"

        lib_file = self.get_library_match(prompt)
        if lib_file:
            return lib_file, "LIBRARY"

        api_key = self.conf.get("elevenlabs_api_key")
        if not api_key: return None, "NO_API_KEY"

        try:
            res = requests.post(
                "https://api.elevenlabs.io/v1/sound-generation",
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "text": prompt, 
                    "duration_seconds": self.conf.get("max_duration", 10),
                    "prompt_influence": self.conf.get("prompt_influence", 0.3) 
                },
                timeout=15
            )
            
            if res.status_code == 200:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_prompt = "".join(c for c in prompt if c.isalnum())[:20]
                filepath = OUTPUT_DIR / f"{timestamp}_{safe_prompt}.mp3"

                with open(filepath, 'wb') as f:
                    f.write(res.content)
                
                with open(filepath.with_suffix('.json'), 'w') as f:
                    json.dump({"duration": self.conf.get("max_duration", 10)}, f)

                return filepath, "SUCCESS"
            else:
                # --- UPDATED ERROR PARSING ---
                error_text = res.text.lower()
                
                if "quota" in error_text:
                    err = "QUOTA_EXCEEDED"
                elif "moderation" in error_text:
                    err = "MODERATION_BLOCKED"
                else:
                    err = f"API_{res.status_code}"
                
                logger.warning(f"API Error: {res.text[:100]}")
                return None, err
                # -----------------------------

        except Exception as e:
            logger.error(f"Request Error: {e}")
            return None, str(e)

    def write_status(self, message):
        """Writes status message to the text file."""
        try:
            with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                f.write(message)
        except Exception as e:
            logger.error(f"Failed to write status file: {e}")

    def trigger_obs(self, filepath, prompt, user, status):
        if not self.obs_client:
            self.connect_obs()
            if not self.obs_client: return

        scene = self.obs_client.get_current_program_scene().current_program_scene_name
        source_name = self.conf.get("obs_source_name", "SFX Audio 1")
        
        display_text = prompt if status in ["SUCCESS", "LIBRARY"] else f"[{status}] {prompt}"
        
        try:
            self.obs_client.set_input_settings("SFX Prompt Text", {"text": display_text}, True)
            self.obs_client.set_input_settings("SFX Prompt Sender", {"text": f"Sender: {user}"}, True)
        except: pass

        try:
            self.obs_client.set_input_settings(source_name, {"local_file": str(filepath)}, True)
            
            group_name = self.conf.get("obs_group_name", "SFX")
            items = self.obs_client.get_scene_item_list(scene).scene_items
            group_id = next((i['sceneItemId'] for i in items if i['sourceName'] == group_name), None)
            
            if group_id:
                self.obs_client.set_scene_item_enabled(scene, group_id, True)
            
            self.obs_client.trigger_media_input_action(source_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
            
            duration = self.get_duration(filepath)
            time.sleep(duration + 1)
            
            if group_id:
                self.obs_client.set_scene_item_enabled(scene, group_id, False)

        except Exception as e:
            logger.error(f"OBS Error: {e}")

def main():
    handler = SFXHandler()
    handler.cleanup_old_files()

    if len(sys.argv) < 2: return
    
    raw_input = sys.argv[1]
    user = sys.argv[2] if len(sys.argv) > 2 else "unknown"

    try: prompt = base64.b64decode(raw_input).decode('utf-8')
    except: prompt = raw_input

    # Generate or Find Audio
    filepath, status = handler.generate_audio(prompt, user)

    # --- UPDATE STATUS FILE ---
    if status == "SUCCESS":
        status_msg = f"🔊 Playing: {prompt}"
    elif status == "LIBRARY":
        status_msg = f"🔊 Library: {prompt}"
    elif filepath is None:
        status_msg = f"❌ Failed: {status[:50]}"
    else:
        status_msg = f"⚠️ {status}: {prompt}"
    
    handler.write_status(status_msg)
    # --------------------------

    if filepath:
        handler.trigger_obs(filepath, prompt, user, status)

if __name__ == "__main__":
    main()