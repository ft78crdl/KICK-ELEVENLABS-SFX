# Overlay Template System

The SFX widget now supports multiple overlay themes as complete template files.

## File Structure

```
templates/
├── admin/
│   └── admin.html (admin panel)
└── overlays/
    ├── dark.html (default dark theme)
    ├── light.html (clean white theme)  
    ├── minimal.html (ultra-minimal)
    ├── neon.html (retro cyberpunk theme)
    ├── user_custom.html (copy for customization)
    └── README.md (this file)
```

## Available Templates

- **`dark`** (default) - Dark theme with green accents
- **`light`** - Clean white theme with blue accents  
- **`minimal`** - Ultra-minimal black overlay
- **`neon`** - Retro cyberpunk neon theme
- **`gothic`** - Medieval marquee style with animated icon
- **`user_custom`** - Copy this to create your own theme

## Configuration

Edit `config.json`:

```json
{
    "overlay": {
        "enabled": true,
        "template": "dark",
        "position": "bottom-left",
        "show_prompt": true,
        "show_sender": true,
        "display_duration_after_audio": 2000
    }
}
```

## Creating Custom Templates

1. Copy `overlays/dark.html` to `overlays/user_custom.html`
2. Edit the CSS and HTML to your liking
3. Set `"template": "user_custom"` in config.json
4. Restart the server

## Template Structure

Each template is a complete HTML file containing:
- CSS styling
- HTML structure  
- JavaScript for Socket.IO communication
- Jinja2 template variables for configuration

## Position Options

- `top-left`
- `top-center` 
- `top-right`
- `bottom-left`
- `bottom-center`
- `bottom-right`

## Fallback

If a template is not found, the server automatically falls back to the `dark` theme.
