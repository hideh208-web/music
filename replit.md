# Replit.md

## Overview

This is a Discord bot application built with Python that integrates AI capabilities through the Groq API. The bot provides chat functionality with AI-powered responses and includes music playback features via Wavelink. A Flask server runs alongside the bot to maintain uptime on platforms like Replit and Render through health check endpoints.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Bot Framework
- **Discord.py** serves as the core framework for Discord bot functionality
- Uses the commands extension with hybrid command support (prefix `?` and slash commands via `app_commands`)
- Message content intent is enabled for reading user messages

### AI Integration
- **Groq API** provides the AI/LLM backend for generating responses
- The bot likely uses Groq's fast inference for conversational AI features
- Channel-specific configuration stored in `channel_config.json` allows per-channel AI behavior customization

### Keep-Alive System
- A lightweight **Flask** web server runs on a separate thread
- Exposes a health check endpoint at `/` to prevent hosting platforms from spinning down the application
- Uses threading to run Flask alongside the async Discord bot without blocking

### Music System
- **Wavelink** library is included for audio/music playback functionality
- Requires a Lavalink server connection (external dependency not configured in visible files)

### Configuration Management
- Environment variables (`DISCORD_TOKEN`, `GROQ_API_KEY`) handle sensitive credentials
- `channel_config.json` stores per-channel settings as a JSON file
- `config.json` exists as a template but environment variables take precedence

## External Dependencies

### APIs and Services
- **Discord API** - Bot hosting and interaction platform
- **Groq API** - AI/LLM inference service for generating chat responses
- **Lavalink Server** - Required for Wavelink music playback (must be self-hosted or use a public instance)

### Python Packages
- `discord.py` - Discord bot framework
- `groq` - Official Groq Python client
- `flask` - Web server for health checks
- `gunicorn` - Production WSGI server
- `wavelink` - Discord music/audio library
- `python-dotenv` - Environment variable loading

### Data Storage
- File-based JSON storage for channel configuration
- No database currently configured