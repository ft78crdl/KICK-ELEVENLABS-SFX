"""
KICK-ELEVENLABS-SFX Widget Server
A web-based SFX player that works with any streaming software supporting browser sources.
"""

import os
import sys
import json
import time
import uuid
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

import requests
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from mutagen.mp3 import MP3

# Determine base path (works for both script and PyInstaller exe)
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# Load configuration
CONFIG_PATH = BASE_DIR / "config.json"

def load_config() -> Dict[str, Any]:
    """Load configuration from config.json"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: config.json not found at {CONFIG_PATH}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in config.json: {e}")
        sys.exit(1)

CONFIG = load_config()

# Directory paths
AUDIO_CACHE_DIR = BASE_DIR / "audio_cache"
SFX_LIBRARY_DIR = BASE_DIR / "sfx_library"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Ensure directories exist
AUDIO_CACHE_DIR.mkdir(exist_ok=True)
SFX_LIBRARY_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
LOG_FILE = BASE_DIR / CONFIG.get("logging", {}).get("log_file", "logs/sfx_log.txt")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ] if CONFIG.get("logging", {}).get("enabled", True) else [logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__, 
            template_folder=str(TEMPLATES_DIR),
            static_folder=str(STATIC_DIR))
app.config['SECRET_KEY'] = str(uuid.uuid4())
CORS(app)
# Initialize SocketIO with maximum compatibility
try:
    # Simple initialization without specifying async_mode
    socketio = SocketIO(app, 
                       cors_allowed_origins="*",
                       logger=False, 
                       engineio_logger=False,
                       ping_timeout=60,
                       ping_interval=25)
    logger.info("SocketIO initialized successfully")
except Exception as e:
    logger.error(f"SocketIO initialization failed: {e}")
    logger.error("Server will run without real-time overlay updates")
    # Create minimal socketio stub
    class SocketIOStub:
        def emit(self, *args, **kwargs): pass
        def on(self, event): 
            def decorator(f): return f
            return decorator
    socketio = SocketIOStub()

# Global state
last_play_time: float = 0

# Track connected clients
connected_clients: set = set()


class ElevenLabsError(Exception):
    """Custom exception for ElevenLabs API errors"""
    pass


def get_local_sfx_files() -> Dict[str, Path]:
    """Get all local SFX files from the library"""
    sfx_files = {}
    if SFX_LIBRARY_DIR.exists():
        for file in SFX_LIBRARY_DIR.glob("*.mp3"):
            # Key is filename without extension, lowercase for matching
            sfx_files[file.stem.lower()] = file
    return sfx_files


def check_local_library(prompt: str) -> Optional[Path]:
    """Check if prompt matches a local SFX file"""
    if not CONFIG.get("sfx_generation", {}).get("enable_local_library", True):
        return None
    
    sfx_files = get_local_sfx_files()
    prompt_lower = prompt.lower().strip()
    
    # Exact match
    if prompt_lower in sfx_files:
        logger.info(f"Local SFX match found: {prompt_lower}")
        return sfx_files[prompt_lower]
    
    return None


def get_audio_duration(filepath: Path) -> float:
    """Get duration of an MP3 file in seconds"""
    try:
        audio = MP3(str(filepath))
        return audio.info.length
    except Exception as e:
        logger.warning(f"Could not get audio duration: {e}")
        return 10.0  # Default fallback


def generate_elevenlabs_sfx(prompt: str) -> tuple[bytes, float]:
    """
    Generate SFX using ElevenLabs API
    Returns: (audio_data, duration_seconds)
    """
    api_key = CONFIG.get("elevenlabs_api_key", "")
    if not api_key or api_key == "YOUR_ELEVENLABS_API_KEY_HERE":
        raise ElevenLabsError("API_KEY_NOT_CONFIGURED")
    
    sfx_config = CONFIG.get("sfx_generation", {})
    max_duration = sfx_config.get("max_duration", 20)
    prompt_influence = sfx_config.get("prompt_influence", 0.5)
    
    # Clamp values
    max_duration = max(1, min(22, max_duration))  # ElevenLabs max is 22 seconds
    prompt_influence = max(0.0, min(1.0, prompt_influence))
    
    url = "https://api.elevenlabs.io/v1/sound-generation"
    
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "text": prompt,
        "duration_seconds": max_duration,
        "prompt_influence": prompt_influence
    }
    
    logger.info(f"Generating SFX: '{prompt}' (duration: {max_duration}s, influence: {prompt_influence})")
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            audio_data = response.content
            # Estimate duration (actual duration determined after save)
            return audio_data, max_duration
        elif response.status_code == 401:
            raise ElevenLabsError("INVALID_API_KEY")
        elif response.status_code == 429:
            # Check if it's rate limit or quota
            error_text = response.text.lower()
            if "quota" in error_text or "limit" in error_text:
                raise ElevenLabsError("QUOTA_EXCEEDED")
            raise ElevenLabsError("RATE_LIMITED")
        elif response.status_code == 400:
            raise ElevenLabsError("INVALID_PROMPT")
        else:
            error_msg = response.text[:100] if response.text else "Unknown error"
            raise ElevenLabsError(f"API_ERROR_{response.status_code}: {error_msg}")
            
    except requests.exceptions.Timeout:
        raise ElevenLabsError("TIMEOUT")
    except requests.exceptions.RequestException as e:
        raise ElevenLabsError(f"NETWORK_ERROR: {e}")


def process_sfx_request(prompt: str, sender: str) -> Dict[str, Any]:
    """
    Process an SFX request - check local library or generate via API
    Returns dict with audio_url, duration, is_local, etc.
    """
    # Check local library first
    local_file = check_local_library(prompt)
    
    if local_file:
        # Use local file - serve directly without caching
        duration = get_audio_duration(local_file)
        
        return {
            "success": True,
            "audio_url": f"/audio/local/{local_file.name}",
            "duration": duration,
            "is_local": True,
            "prompt": prompt,
            "sender": sender
        }
    else:
        # Generate via ElevenLabs
        try:
            audio_data, estimated_duration = generate_elevenlabs_sfx(prompt)
            
            # Save to cache
            filename = f"gen_{uuid.uuid4().hex[:8]}.mp3"
            filepath = AUDIO_CACHE_DIR / filename
            
            with open(filepath, "wb") as f:
                f.write(audio_data)
            
            # Get actual duration
            actual_duration = get_audio_duration(filepath)
            
            logger.info(f"Generated SFX saved: {filename} ({actual_duration:.1f}s)")
            
            return {
                "success": True,
                "audio_url": f"/audio/generated/{filename}",
                "duration": actual_duration,
                "is_local": False,
                "prompt": prompt,
                "sender": sender
            }
            
        except ElevenLabsError as e:
            logger.error(f"ElevenLabs error: {e}")
            return {
                "success": False,
                "error": str(e),
                "prompt": prompt,
                "sender": sender
            }


def cleanup_old_cache(max_age_hours: int = 24):
    """Clean up old generated audio files (only cache AI-generated files now)"""
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    cleaned_count = 0
    
    for file in AUDIO_CACHE_DIR.glob("*.mp3"):
        # Only clean generated files (gen_*) and old local files from previous versions (local_*)
        if file.name.startswith("gen_") or file.name.startswith("local_"):
            file_age = now - file.stat().st_mtime
            if file_age > max_age_seconds:
                try:
                    file.unlink()
                    cleaned_count += 1
                    logger.debug(f"Cleaned up old cache file: {file.name}")
                except Exception as e:
                    logger.warning(f"Failed to delete cache file {file.name}: {e}")
    
    if cleaned_count > 0:
        logger.info(f"Cache cleanup: removed {cleaned_count} old files")


# Flask Routes

@app.route("/")
def widget():
    """Serve the browser source widget"""
    overlay_config = CONFIG.get("overlay", {})
    template_name = overlay_config.get("template", "dark")
    
    # Try to load specified template, fall back to dark theme
    try:
        return render_template(f"overlays/{template_name}.html", config=overlay_config)
    except:
        logger.warning(f"Template 'overlays/{template_name}.html' not found, using dark theme")
        return render_template("overlays/dark.html", config=overlay_config)


@app.route("/admin")
def admin_panel():
    """Simple admin/status panel"""
    return render_template("admin/admin.html", 
                          config=CONFIG,
                          connected_clients=len(connected_clients),
                          local_sfx_count=len(get_local_sfx_files()))


@app.route("/trigger", methods=["GET", "POST"])
def trigger_sfx():
    """
    Main endpoint for triggering SFX
    
    Supports both:
    - GET with query params: /trigger?prompt=explosion&sender=JohnDoe
    - GET with base64: /trigger?encodedPrompt=ZXhwbG9zaW9u&sender=JohnDoe
    - POST with JSON body: {"prompt": "explosion", "sender": "JohnDoe"}
    - POST with base64: {"encodedPrompt": "ZXhwbG9zaW9u", "sender": "JohnDoe"}
    
    Returns JSON with:
    - success: bool
    - prompt: string (the original prompt)
    - sender: string (who triggered it)
    - is_local: bool (if it was a local file)
    - duration: float (audio duration in seconds)
    - durationMs: int (duration in milliseconds for Streamer.bot Delay)
    - error: string (if failed, contains error code)
    """
    global last_play_time
    
    # Parse input from GET query params or POST JSON body
    if request.method == "GET":
        # Check for base64 encoded prompt first, then fall back to raw prompt
        encoded_prompt = request.args.get("encodedPrompt", "").strip()
        if encoded_prompt:
            try:
                import base64
                prompt = base64.b64decode(encoded_prompt).decode('utf-8').strip()
            except Exception as e:
                logger.warning(f"Failed to decode base64 prompt: {e}")
                prompt = ""
        else:
            prompt = request.args.get("prompt", "").strip()
        sender = request.args.get("sender", "Anonymous").strip()
    else:
        try:
            data = request.json or {}
        except:
            data = {}
        
        # Check for base64 encoded prompt first, then fall back to raw prompt
        encoded_prompt = data.get("encodedPrompt", "").strip()
        if encoded_prompt:
            try:
                import base64
                prompt = base64.b64decode(encoded_prompt).decode('utf-8').strip()
            except Exception as e:
                logger.warning(f"Failed to decode base64 prompt: {e}")
                prompt = ""
        else:
            prompt = data.get("prompt", "").strip()
        sender = data.get("sender", "Anonymous").strip()
    
    # Handle empty prompt
    if not prompt:
        return jsonify({
            "success": False, 
            "error": "NO_PROMPT",
            "prompt": "",
            "sender": sender,
            "duration": 0,
            "durationMs": 0
        }), 400
    
    logger.info(f"SFX request from {sender}: '{prompt}'")
    
    # Process the request (synchronous - waits for generation)
    result = process_sfx_request(prompt, sender)
    
    if result["success"]:
        # Update last play time
        last_play_time = time.time()
        
        # Emit to all connected widget clients
        overlay_config = CONFIG.get("overlay", {})
        
        emit_data = {
            "audio_url": result["audio_url"],
            "duration": result["duration"],
            "prompt": result["prompt"] if overlay_config.get("show_prompt", True) else "",
            "sender": result["sender"] if overlay_config.get("show_sender", True) else "",
            "show_overlay": overlay_config.get("enabled", True),
            "display_duration_after_audio": overlay_config.get("display_duration_after_audio", 2000)
        }
        
        socketio.emit("play_sfx", emit_data)
        logger.info(f"SFX emitted to {len(connected_clients)} client(s)")
        
        duration = result["duration"]
        return jsonify({
            "success": True,
            "prompt": prompt,
            "sender": sender,
            "is_local": result.get("is_local", False),
            "duration": duration,
            "durationMs": int(duration * 1000)
        })
    else:
        # Failed - return 200 so Streamer.bot can parse the JSON error
        return jsonify({
            "success": False,
            "error": result.get("error", "UNKNOWN_ERROR"),
            "prompt": prompt,
            "sender": sender,
            "duration": 0,
            "durationMs": 0
        })


@app.route("/audio/<path:filename>")
def serve_audio(filename):
    """Serve audio files - route to local or generated based on filename"""
    # Security: only allow mp3 files
    if not filename.endswith(".mp3"):
        return "Invalid file type", 400
    
    # Check if it's a local file (no hash in filename)
    if not any(char in filename for char in ['_', '-']) or len(filename.split('_')[0]) < 10:
        # Likely a local file - serve directly from sfx_library
        local_path = SFX_LIBRARY_DIR / filename
        if local_path.exists():
            return send_file(local_path, mimetype="audio/mpeg")
    
    # Generated file - serve from cache
    cache_path = AUDIO_CACHE_DIR / filename
    if not cache_path.exists():
        return "File not found", 404
    
    return send_file(cache_path, mimetype="audio/mpeg")


@app.route("/audio/local/<filename>")
def serve_local_audio(filename):
    """Serve local SFX files directly from sfx_library"""
    # Security: only allow mp3 files from sfx_library
    if not filename.endswith(".mp3"):
        return "Invalid file type", 400
    
    filepath = SFX_LIBRARY_DIR / filename
    if not filepath.exists():
        return "File not found", 404
    
    return send_file(filepath, mimetype="audio/mpeg")


@app.route("/audio/generated/<filename>") 
def serve_generated_audio(filename):
    """Serve generated SFX files from audio_cache"""
    # Security: only allow mp3 files from audio_cache
    if not filename.endswith(".mp3"):
        return "Invalid file type", 400
    
    filepath = AUDIO_CACHE_DIR / filename
    if not filepath.exists():
        return "File not found", 404
    
    return send_file(filepath, mimetype="audio/mpeg")


@app.route("/status")
def status():
    """Health check / status endpoint"""
    return jsonify({
        "status": "running",
        "connected_clients": len(connected_clients),
        "local_sfx_count": len(get_local_sfx_files()),
        "config": {
            "max_duration": CONFIG.get("sfx_generation", {}).get("max_duration", 20),
            "prompt_influence": CONFIG.get("sfx_generation", {}).get("prompt_influence", 0.5),
            "overlay_enabled": CONFIG.get("overlay", {}).get("enabled", True),
            "local_library_enabled": CONFIG.get("sfx_generation", {}).get("enable_local_library", True)
        }
    })


@app.route("/sounds")
def list_sounds():
    """List available local SFX files"""
    sfx_files = get_local_sfx_files()
    # Return actual filenames (not lowercase keys) for display
    actual_filenames = sorted([file.name for file in sfx_files.values()])
    return jsonify({
        "count": len(actual_filenames),
        "sounds": actual_filenames
    })


@app.route("/config", methods=["GET"])
def get_config():
    """Get current configuration (excluding API key)"""
    safe_config = {k: v for k, v in CONFIG.items() if k != "elevenlabs_api_key"}
    safe_config["elevenlabs_api_key"] = "***configured***" if CONFIG.get("elevenlabs_api_key", "").startswith("sk_") else "NOT SET"
    return jsonify(safe_config)


@app.route("/reload-config", methods=["POST"])
def reload_config():
    """Reload configuration from file"""
    global CONFIG
    try:
        CONFIG = load_config()
        logger.info("Configuration reloaded")
        return jsonify({"success": True, "message": "Configuration reloaded"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# WebSocket events

@socketio.on("connect")
def handle_connect():
    """Handle new WebSocket connection"""
    connected_clients.add(request.sid)
    logger.info(f"Client connected: {request.sid} (total: {len(connected_clients)})")


@socketio.on("disconnect")
def handle_disconnect():
    """Handle WebSocket disconnection"""
    connected_clients.discard(request.sid)
    logger.info(f"Client disconnected: {request.sid} (total: {len(connected_clients)})")


@socketio.on("ping")
def handle_ping():
    """Handle ping from client"""
    emit("pong", {"timestamp": time.time()})


# Startup tasks

def get_lan_ip() -> str:
    """Get the LAN IP address of this machine"""
    import socket
    try:
        # Connect to external address to determine which interface is used
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def startup_tasks():
    """Run startup tasks"""
    server_config = CONFIG.get("server", {})
    host = server_config.get("host", "0.0.0.0")
    port = server_config.get("port", 5123)
    
    lan_ip = get_lan_ip()
    
    logger.info("")
    logger.info("=" * 55)
    logger.info("   SFX Widget Server")
    logger.info("=" * 55)
    logger.info("")
    logger.info("  Widget URLs (add as Browser Source):")
    logger.info(f"    Local:    http://127.0.0.1:{port}/")
    if lan_ip:
        logger.info(f"    Network:  http://{lan_ip}:{port}/")
    logger.info("")
    logger.info("  Trigger URLs (for Streamer.bot):")
    logger.info(f"    http://127.0.0.1:{port}/trigger?prompt=TEST&sender=TEST")
    logger.info("")
    logger.info(f"  Admin Panel: http://127.0.0.1:{port}/admin")
    logger.info("")
    logger.info("=" * 55)
    logger.info(f"  Local SFX files: {len(get_local_sfx_files())}")
    
    # Validate API key
    api_key = CONFIG.get("elevenlabs_api_key", "")
    if not api_key or api_key == "YOUR_ELEVENLABS_API_KEY_HERE":
        logger.warning("  ElevenLabs API key: NOT SET (only local SFX will work)")
    else:
        logger.info("  ElevenLabs API key: Configured")
    
    logger.info("=" * 55)
    logger.info("")
    
    # Clean up old cache
    cleanup_old_cache()


if __name__ == "__main__":
    startup_tasks()
    
    server_config = CONFIG.get("server", {})
    host = server_config.get("host", "127.0.0.1")
    port = server_config.get("port", 5123)
    
    # Run with SocketIO
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
